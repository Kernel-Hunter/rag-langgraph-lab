"""The web tools: fetch a page as plain text, and search the web.

Standard library only. Pages come back as readable text (no tags, scripts or styles; the
main part of the page when it marks one) and are cut to a fixed size, so a large page
cannot fill the model's context.

Search uses the Brave Search API when BRAVE_API_KEY is set, Tavily when TAVILY_API_KEY is
set, and DuckDuckGo's HTML page otherwise. DuckDuckGo needs no key but sometimes asks
automated clients to prove they are human; Vesper does not try to get around that and says
how to set a key instead.
"""

from __future__ import annotations

import html
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser

MAX_PAGE_BYTES = 3_000_000
TIMEOUT_S = 20
USER_AGENT = "Mozilla/5.0 (compatible; Vesper)"
SEARCH_URL = "https://html.duckduckgo.com/html/?q="
BRAVE_URL = "https://api.search.brave.com/res/v1/web/search?count={n}&q="
TAVILY_URL = "https://api.tavily.com/search"
NO_KEYLESS_SEARCH = ("Web search is not available right now: the search page asked for a human check. "
                     "Tell the user they can add BRAVE_API_KEY or TAVILY_API_KEY to ~/.vesper/.env for reliable "
                     "search. Meanwhile use web_fetch on an address you know.")
SKIP = {"script", "style", "noscript", "svg", "template", "iframe", "nav", "footer", "form", "head", "aside", "button"}
MAIN = {"main", "article"}
MIN_MAIN_CHARS = 400  # a <main> shorter than this is not the whole story: keep the full page
BLOCK = {"p", "div", "br", "li", "tr", "section", "article", "h1", "h2", "h3", "h4", "h5", "h6", "pre",
         "blockquote", "table", "ul", "ol", "header", "main", "dd", "dt"}


class WebError(Exception):
    """A fetch or search that failed in a way the model can act on."""


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.main: list[str] = []   # the same text, only from inside <main> or <article>
        self.title = ""
        self._skip = 0
        self._main = 0
        self._in_title = False

    def _add(self, text: str) -> None:
        self.out.append(text)
        if self._main:
            self.main.append(text)

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self._in_title = True
        elif tag in SKIP:
            self._skip += 1
        elif tag in MAIN or ("role", "main") in attrs:
            self._main += 1
        if tag in BLOCK and not self._skip:
            self._add("\n")
            if tag in ("h1", "h2", "h3"):
                self._add("#" * int(tag[1]) + " ")
            elif tag == "li":
                self._add("- ")

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        elif tag in SKIP:
            self._skip = max(0, self._skip - 1)
        elif tag in MAIN:
            self._main = max(0, self._main - 1)
        if tag in BLOCK and not self._skip:
            self._add("\n")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self._skip:
            self._add(data)


def _tidy(pieces: list[str]) -> str:
    lines = [" ".join(line.replace("¶", "").split()) for line in "".join(pieces).splitlines()]
    text = re.sub(r"(?m)^(-|#{1,3})\n+(?=[^\n\-#])", r"\1 ", "\n".join(lines))  # "- " and its text on one line
    lines = [line for line in text.splitlines() if not line or line.strip("-#|· ")]  # no empty bullets or headings
    body = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    return re.sub(r"(?m)^(- .*)\n\n(?=- )", r"\1\n", body)  # list items on consecutive lines


def html_to_text(page: str) -> str:
    parser = _Text()
    parser.feed(page)
    body = _tidy(parser.main)
    if len(body) < MIN_MAIN_CHARS:
        body = _tidy(parser.out)
    title = " ".join(parser.title.split())
    return f"# {title}\n\n{body}" if title and not body.startswith("# ") else body


def host_of(url: str) -> str:
    return (urllib.parse.urlsplit(url).hostname or "").lower()


def check_url(url: str) -> str:
    url = (url or "").strip()
    if "://" not in url:
        url = "https://" + url
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise WebError("Give a full http or https address.")
    return url


def _tls():
    """Certificate checks with certifi's list when it is installed: some Python builds ship
    without a usable system list, and then every https page would fail."""
    import ssl

    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:  # noqa: BLE001 - fall back to the system's list
        return ssl.create_default_context()


def _get(url: str, headers: dict | None = None, data: bytes | None = None) -> tuple[str, str]:
    """(content type, decoded body) of `url`. With `data` the request is a POST."""
    req = urllib.request.Request(url, data=data, headers={
        "User-Agent": USER_AGENT, "Accept": "text/html,text/plain,application/json,*/*", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S, context=_tls()) as resp:
            kind = (resp.headers.get_content_type() or "").lower()
            charset = resp.headers.get_content_charset() or "utf-8"
            raw = resp.read(MAX_PAGE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise WebError(f"The server answered {exc.code} {exc.reason} for {url.split('?')[0]}.") from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        raise WebError(f"Could not reach {url.split('?')[0]}: {getattr(exc, 'reason', exc)}") from None
    try:
        return kind, raw[:MAX_PAGE_BYTES].decode(charset, errors="replace")
    except LookupError:
        return kind, raw[:MAX_PAGE_BYTES].decode("utf-8", errors="replace")


def fetch(url: str, max_chars: int, offset: int = 0) -> str:
    """The page at `url` as text, `max_chars` characters starting at `offset`."""
    kind, body = _get(check_url(url))
    if kind and not (kind.startswith("text/") or "json" in kind or "xml" in kind or "javascript" in kind):
        raise WebError(f"{url} is {kind}, not text.")
    text = html_to_text(body) if "html" in kind or body.lstrip()[:15].lower().startswith(("<!doctype", "<html")) else body
    if not text.strip():
        return "(the page has no readable text; it may need JavaScript)"
    part = text[offset: offset + max_chars]
    if not part:
        return f"The page has {len(text)} characters; nothing at offset {offset}."
    left = len(text) - offset - len(part)
    return part + (f"\n\n({left} more characters; call web_fetch with offset={offset + len(part)})" if left > 0 else "")


RESULT = re.compile(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', re.S)
SNIPPET = re.compile(r'class="result__snippet"[^>]*>(.*?)</a>', re.S)


def _plain(fragment: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", "", fragment)).split())


def parse_results(page: str, limit: int) -> list[dict]:
    snippets = [_plain(s) for s in SNIPPET.findall(page)]
    out = []
    for i, (href, title) in enumerate(RESULT.findall(page)):
        href = html.unescape(href)
        target = urllib.parse.parse_qs(urllib.parse.urlsplit(href).query).get("uddg", [href])[0]
        if target.startswith("//"):
            target = "https:" + target
        if "duckduckgo.com/y.js" in target:  # an ad
            continue
        out.append({"title": _plain(title), "url": target, "snippet": snippets[i] if i < len(snippets) else ""})
        if len(out) >= limit:
            break
    return out


def _api_results(query: str, limit: int) -> list[dict] | None:
    """Results from a search API the user has a key for, or None when no key is set."""
    brave, tavily = os.getenv("BRAVE_API_KEY"), os.getenv("TAVILY_API_KEY")
    try:
        if brave:
            _, body = _get(BRAVE_URL.format(n=limit) + urllib.parse.quote_plus(query),
                           {"X-Subscription-Token": brave, "Accept": "application/json"})
            rows = (json.loads(body).get("web") or {}).get("results") or []
            return [{"title": _plain(r.get("title", "")), "url": r.get("url", ""), "snippet": _plain(r.get("description", ""))}
                    for r in rows[:limit] if r.get("url")]
        if tavily:
            _, body = _get(TAVILY_URL, {"Authorization": f"Bearer {tavily}", "Content-Type": "application/json"},
                           json.dumps({"query": query, "max_results": limit}).encode())
            rows = json.loads(body).get("results") or []
            return [{"title": r.get("title", ""), "url": r.get("url", ""), "snippet": " ".join(str(r.get("content", "")).split())[:300]}
                    for r in rows[:limit] if r.get("url")]
    except (ValueError, AttributeError):
        raise WebError("The search service sent an answer that could not be read.") from None
    return None


def search(query: str, limit: int = 6) -> str:
    query = " ".join((query or "").split())
    if not query:
        raise WebError("Give a search query.")
    results = _api_results(query, limit)
    if results is None:
        _, page = _get(SEARCH_URL + urllib.parse.quote_plus(query))
        results = parse_results(page, limit)
        if not results and "anomaly" in page:
            raise WebError(NO_KEYLESS_SEARCH)
    if not results:
        raise WebError("The search found nothing. Try other words, or web_fetch a page you know.")
    return "\n".join(f"{n}. {r['title']}\n   {r['url']}" + (f"\n   {r['snippet']}" if r["snippet"] else "")
                     for n, r in enumerate(results, 1))
