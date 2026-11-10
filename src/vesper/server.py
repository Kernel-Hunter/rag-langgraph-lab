"""Vesper local API, behind the desktop window.

Binds to 127.0.0.1 only and requires a per-launch token on every /api call, so
other websites and other users on the machine cannot drive it. On top of that:
the Host header must name this server (DNS rebinding), cross-origin writes are
refused, every response carries strict security headers, and all input is
validated and size-capped.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import sys
import threading
import traceback
from pathlib import Path

from urllib.parse import urlparse

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from langchain_core.vectorstores import InMemoryVectorStore
from starlette.datastructures import MutableHeaders

from vesper import providers as prov
from vesper.indexing import load_documents, split_documents

if getattr(sys, "frozen", False):
    BUNDLE = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    WEB_DIR = BUNDLE / "vesper" / "web"
    SAMPLE = BUNDLE / "data" / "handbook.md"
else:
    WEB_DIR = Path(__file__).parent / "web"
    SAMPLE = Path(__file__).resolve().parents[2] / "data" / "handbook.md"

CONFIG = prov.HOME / "config.json"
DOCS_DIR = prov.HOME / "docs"
INDEX_DIR = prov.HOME / "index"
SEEDED = prov.HOME / ".seeded"
DOC_TYPES = {".pdf", ".md", ".txt"}

MAX_UPLOAD_BYTES = 50 * 1024 * 1024
MAX_JSON_BYTES = 256 * 1024
MAX_DOCS = 200
MAX_NAME_LEN = 200
MAX_QUESTION_CHARS = 8000
MAX_MODEL_LEN = 200
MAX_KEY_LEN = 500
MODES = ("graph", "agent")

SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; font-src 'self' data:; connect-src 'self'; "
        "frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Cross-Origin-Opener-Policy": "same-origin",
}

DEFAULTS = {
    "provider": "ollama",
    "model": "",
    "embeddings": "local",
    "embed_model": "",
    "chunk_size": 600,
    "chunk_overlap": 100,
    "k": 3,
    "mode": "graph",
    "check": False,
}


def _text(content) -> str:
    """Anthropic and others may return content blocks instead of a string."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
    return str(content or "")


# Embedding sources that are not chat providers.
_EMBEDDING_LABELS = {"local": "The on-device embedding model", "fake": "The fake embedding model",
                     "huggingface": "Hugging Face embeddings"}


def friendly_error(exc: Exception, provider: str) -> str:
    """`provider` is a chat provider id or an embeddings id ("local", "fake", ...)."""
    text = f"{type(exc).__name__}: {exc}"
    low = text.lower()
    if provider in prov.PROVIDERS:
        p = prov.PROVIDERS[provider]
        label, local = p.label, p.local
    else:
        label, local = _EMBEDDING_LABELS.get(provider, provider or "The provider"), False
        if provider == "local" and ("connection" in low or "download" in low or "resolve" in low):
            return ("Could not download the on-device embedding model (about 65 MB, needed once). "
                    "Check your internet connection, then press Reindex.")
    if "401" in text or "authentication" in low or "invalid x-api-key" in low or "incorrect api key" in low:
        return f"{label} rejected the API key. Open Settings and paste a valid key."
    if "connection" in low and local:
        return f"Cannot reach {label}. Start it first (for Ollama: open the Ollama app or run `ollama serve`)."
    if "404" in text and "model" in low:
        return f"{label} does not know this model. Pick another one in the model menu."
    if "429" in text or "rate limit" in low or "quota" in low:
        return f"{label} says you hit a rate limit or ran out of credit."
    return text[:400]


# ----- input validation -----

def _bad(message: str):
    raise HTTPException(400, message)


def _int_field(name: str, value, lo: int, hi: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        _bad(f"{name} must be a whole number.")
    if not lo <= value <= hi:
        _bad(f"{name} must be between {lo} and {hi}.")
    return value


def _text_field(name: str, value, max_len: int) -> str:
    if not isinstance(value, str):
        _bad(f"{name} must be text.")
    if len(value) > max_len:
        _bad(f"{name} must be at most {max_len} characters.")
    if "\n" in value or "\r" in value or "\x00" in value:
        _bad(f"{name} must be a single line.")
    return value.strip()


def _url_field(name: str, value) -> str:
    value = _text_field(name, value, MAX_KEY_LEN)
    if value:
        u = urlparse(value)
        if u.scheme not in ("http", "https") or not u.hostname:
            _bad(f"{name} must be empty or an http(s):// URL.")
        if u.username or u.password:
            _bad(f"{name} must not contain a user name or password. Put the key in the key field.")
        if u.hostname.startswith("169.254.") or u.hostname in ("metadata.google.internal",):
            _bad(f"{name} points at a cloud metadata address.")
    return value


def _check_field(name: str, value) -> object:
    """Validates one setting. Raises HTTPException(400) with a readable message."""
    if name == "provider":
        if value not in prov.PROVIDERS:
            _bad(f"Unknown provider {str(value)[:50]!r}.")
        return value
    if name in ("model", "embed_model"):
        return _text_field(name, value, MAX_MODEL_LEN)
    if name == "embeddings":
        if value not in prov.EMBEDDING_PROVIDERS:
            _bad(f"embeddings must be one of {', '.join(prov.EMBEDDING_PROVIDERS)}.")
        return value
    if name == "chunk_size":
        return _int_field(name, value, 100, 4000)
    if name == "chunk_overlap":
        if isinstance(value, bool) or not isinstance(value, int):
            _bad("chunk_overlap must be a whole number.")
        return value  # range depends on chunk_size, see _fix_overlap
    if name == "k":
        return _int_field(name, value, 1, 20)
    if name == "mode":
        if value not in MODES:
            _bad("mode must be graph or agent.")
        return value
    if name == "check":
        if not isinstance(value, bool):
            _bad("check must be true or false.")
        return value
    raise KeyError(name)


def _fix_overlap(settings: dict) -> None:
    """chunk_overlap must be in 0..min(1000, chunk_size - 1); otherwise use chunk_size // 5."""
    size, overlap = settings["chunk_size"], settings["chunk_overlap"]
    if not 0 <= overlap <= min(1000, size - 1):
        settings["chunk_overlap"] = size // 5


def validate_settings(values, current: dict) -> tuple[dict, dict, str | None]:
    """Checks a /api/settings body. Returns (new settings, {env var: key}, custom base URL or None).

    Unknown fields are ignored. Raises HTTPException(400) on the first bad value.
    """
    if not isinstance(values, dict):
        _bad("Settings must be a JSON object.")
    new = dict(current)
    for name, value in values.items():
        if name in DEFAULTS:
            new[name] = _check_field(name, value)
    _fix_overlap(new)

    env: dict[str, str] = {}
    keys = values.get("keys")
    if keys is not None:
        if not isinstance(keys, dict):
            _bad("keys must be an object of provider id to key.")
        for pid, key in keys.items():
            p = prov.PROVIDERS.get(pid)
            if not p or not p.key_env or key is None:
                continue  # unknown providers are ignored
            env[p.key_env] = _text_field(f"The {p.label} key", key, MAX_KEY_LEN)

    custom_url = values.get("custom_base_url")
    if custom_url is not None:
        custom_url = _url_field("custom_base_url", custom_url)
    return new, env, custom_url


def _load_saved_settings() -> dict:
    """config.json, keeping only values that still pass validation."""
    settings = dict(DEFAULTS)
    try:
        saved = json.loads(CONFIG.read_text()) if CONFIG.exists() else {}
    except (OSError, ValueError):
        saved = {}
    if isinstance(saved, dict):
        for name, value in saved.items():
            if name in DEFAULTS:
                try:
                    settings[name] = _check_field(name, value)
                except HTTPException:
                    pass
    _fix_overlap(settings)
    return settings


def clean_doc_name(name) -> str:
    """A safe file name for DOCS_DIR, or HTTPException(400)."""
    if not isinstance(name, str):
        _bad("Missing file name.")
    base = re.split(r"[\\/]", name)[-1]
    safe = re.sub(r"[^\w.\- ]+", "_", base).strip()
    if not safe or not safe.strip("._ -"):
        _bad("The file name is empty.")
    if safe.startswith("."):
        _bad("Hidden files are not accepted.")
    if len(safe) > MAX_NAME_LEN:
        _bad(f"The file name is longer than {MAX_NAME_LEN} characters.")
    if Path(safe).suffix.lower() not in DOC_TYPES:
        _bad("Only .pdf, .md and .txt files are supported.")
    return safe


async def read_capped(request: Request, limit: int) -> bytes:
    """The request body, or HTTPException(413) once it grows past `limit` bytes."""
    size = f"{limit // (1024 * 1024)} MB" if limit >= 1024 * 1024 else f"{limit // 1024} KB"
    too_big = HTTPException(413, f"The request is larger than {size}.")
    length = request.headers.get("content-length")
    if length and length.isdigit() and int(length) > limit:
        raise too_big
    data = bytearray()
    async for chunk in request.stream():
        data += chunk
        if len(data) > limit:
            raise too_big
    return bytes(data)


class GuardMiddleware:
    """Pure ASGI (so streaming is untouched).

    - Host must be one of `allowed_hosts` (blocks DNS rebinding). None skips this check.
    - Methods other than GET/HEAD with an Origin header must come from this server's origin.
    - Adds SECURITY_HEADERS to every response, including errors.
    """

    def __init__(self, app, allowed_hosts=None):
        self.app = app
        self.hosts = None if allowed_hosts is None else {h.lower() for h in allowed_hosts}

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def send_secure(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for k, v in SECURITY_HEADERS.items():
                    headers[k] = v
            await send(message)

        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        host = headers.get("host", "").lower()
        if self.hosts is not None and host not in self.hosts:
            return await JSONResponse({"detail": "bad host"}, 403)(scope, receive, send_secure)
        origin = headers.get("origin")
        if scope["method"] not in ("GET", "HEAD") and origin is not None:
            allowed = {f"http://{h}" for h in (self.hosts if self.hosts is not None else {host})}
            if origin.lower() not in allowed:
                return await JSONResponse({"detail": "bad origin"}, 403)(scope, receive, send_secure)
        await self.app(scope, receive, send_secure)


class Engine:
    """Holds settings, the index and the models."""

    def __init__(self):
        prov.HOME.mkdir(parents=True, exist_ok=True)
        try:
            prov.HOME.chmod(0o700)
        except OSError:  # Windows ignores POSIX modes
            pass
        DOCS_DIR.mkdir(exist_ok=True)
        INDEX_DIR.mkdir(exist_ok=True)
        # The sample goes in once, on first launch. Deleting it later must stick.
        if not SEEDED.exists():
            if not any(DOCS_DIR.iterdir()) and SAMPLE.exists():
                shutil.copy(SAMPLE, DOCS_DIR / "sample-handbook.md")
            SEEDED.write_text("sample handbook added once; delete this file to add it again\n")
        self.settings = _load_saved_settings()
        self.lock = threading.Lock()
        self._rebuild_flag = threading.Lock()
        self._rebuild_running = False
        self._rebuild_dirty = False
        self.store = None
        self.index = {"status": "idle", "chunks": 0, "pages": 0, "error": None, "warnings": []}
        self._llm = None
        self._llm_key = None

    # ----- settings -----
    def save(self):
        CONFIG.write_text(json.dumps(self.settings, indent=2))

    def update(self, values: dict) -> bool:
        """Apply new settings. Returns True when the index must be rebuilt."""
        before = self._fingerprint()
        new, env, custom_url = validate_settings(values, self.settings)
        if custom_url is not None:
            env["CUSTOM_BASE_URL"] = custom_url
        if env:
            try:
                prov.save_user_settings(env)
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
        self.settings = new
        self.save()
        self._llm = None
        keys = values.get("keys") or {}
        embed_key_changed = any(
            prov.PROVIDERS.get(pid) and prov.PROVIDERS[pid].key_env in env and pid == self.settings["embeddings"]
            for pid in keys
        )
        return self._fingerprint() != before or embed_key_changed or self.store is None

    # ----- documents and index -----
    def docs(self) -> list[dict]:
        return [
            {"name": f.name, "size": f.stat().st_size}
            for f in sorted(DOCS_DIR.iterdir())
            if f.is_file() and f.suffix.lower() in DOC_TYPES
        ]

    def _fingerprint(self) -> str:
        s = self.settings
        files = []
        for d in self.docs():
            try:
                files.append((d["name"], d["size"], (DOCS_DIR / d["name"]).stat().st_mtime))
            except OSError:  # removed while we were listing
                continue
        raw = json.dumps([files, s["embeddings"], s["embed_model"], s["chunk_size"], s["chunk_overlap"]])
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def rebuild_async(self):
        """Rebuild in the background. Calls made while one runs are merged into one more pass."""
        with self._rebuild_flag:
            if self._rebuild_running:
                self._rebuild_dirty = True
                return
            self._rebuild_running = True
            self._rebuild_dirty = False
        threading.Thread(target=self._rebuild_loop, daemon=True).start()

    def _rebuild_loop(self):
        while True:
            try:
                self.rebuild()
            except Exception:
                traceback.print_exc()
            with self._rebuild_flag:
                if not self._rebuild_dirty:
                    self._rebuild_running = False
                    return
                self._rebuild_dirty = False

    def rebuild(self):
        with self.lock:
            s = self.settings
            if not self.docs():
                self.store = None
                self.index = {"status": "empty", "chunks": 0, "pages": 0, "error": None, "warnings": []}
                return
            warnings: list[dict] = []
            try:
                key = self._fingerprint()
                self.index = {**self.index, "status": "loading", "error": None}
                emb = prov.get_embeddings(s["embeddings"], s["embed_model"] or None)
                cache = INDEX_DIR / f"{key}.json"
                docs = load_documents(DOCS_DIR, warnings=warnings)
                if cache.exists():
                    store = InMemoryVectorStore.load(str(cache), emb)
                    chunks = len(store.store)
                else:
                    self.index = {**self.index, "status": "embedding"}
                    parts = split_documents(docs, s["chunk_size"], s["chunk_overlap"])
                    store = InMemoryVectorStore(emb)
                    store.add_documents(parts)
                    store.dump(str(cache))
                    chunks = len(parts)
                    for old in sorted(INDEX_DIR.glob("*.json"), key=lambda f: f.stat().st_mtime)[:-5]:
                        old.unlink(missing_ok=True)  # keep the 5 most recent caches
                self.store = store
                self.index = {"status": "ready", "chunks": chunks, "pages": len(docs), "error": None,
                              "warnings": warnings}
            except Exception as exc:
                traceback.print_exc()
                self.store = None
                self.index = {"status": "error", "chunks": 0, "pages": 0,
                              "error": friendly_error(exc, s["embeddings"]),
                              "warnings": warnings}

    def add_doc(self, name: str, data: bytes) -> str:
        safe = clean_doc_name(name)
        target = DOCS_DIR / safe
        if not target.exists() and len(self.docs()) >= MAX_DOCS:
            raise HTTPException(409, f"You can index at most {MAX_DOCS} documents. Remove one first.")
        tmp = DOCS_DIR / f".{safe}.{secrets.token_hex(4)}.upload"  # unique, so parallel uploads never collide
        try:
            tmp.write_bytes(data)
            os.replace(tmp, target)
        finally:
            tmp.unlink(missing_ok=True)
        return safe

    def remove_doc(self, name: str):
        target = DOCS_DIR / clean_doc_name(name)
        if target.is_file():
            target.unlink()

    # ----- chat -----
    def llm(self):
        s = self.settings
        key = (s["provider"], s["model"])
        if self._llm is None or self._llm_key != key:
            self._llm = prov.get_llm(s["provider"], s["model"] or None)
            self._llm_key = key
        return self._llm

    def stream_answer(self, question: str):
        """Yields (event, data) pairs for the chat stream."""
        s = self.settings
        if self.store is None:
            msg = self.index.get("error") or "Add a document first, then ask."
            yield "error", {"message": msg}
            yield "done", {}
            return
        try:
            llm = self.llm()
            if s["mode"] == "agent":
                yield from self._stream_agent(question, llm)
            else:
                yield from self._stream_graph(question, llm)
        except Exception as exc:
            traceback.print_exc()
            yield "error", {"message": friendly_error(exc, s["provider"])}
        yield "done", {}

    @staticmethod
    def _sources(docs):
        return [{"page": d.metadata.get("page"), "source": d.metadata.get("source"), "text": d.page_content}
                for d in docs]

    def _stream_graph(self, question, llm):
        from vesper.graph import build_graph

        graph = build_graph(self.store, llm, k=self.settings["k"], check=self.settings["check"])
        streamed = False
        for mode, chunk in graph.stream({"question": question}, stream_mode=["updates", "messages"]):
            if mode == "updates":
                if "retrieve" in chunk:
                    yield "sources", {"items": self._sources(chunk["retrieve"]["context"])}
                if "generate" in chunk and not streamed:  # model that does not stream
                    yield "token", {"text": _text(chunk["generate"]["answer"])}
                if "check" in chunk:
                    yield "check", {"supported": chunk["check"]["supported"]}
            else:
                msg, meta = chunk
                if meta.get("langgraph_node") == "generate":
                    text = _text(msg.content)
                    if text:
                        streamed = True
                        yield "token", {"text": text}

    def _stream_agent(self, question, llm):
        from vesper.agent import build_agent

        searches: list = []
        agent = build_agent(self.store, llm, k=self.settings["k"], recorder=searches)
        seen = 0
        for mode, chunk in agent.stream({"messages": [{"role": "user", "content": question}]},
                                        stream_mode=["updates", "messages"]):
            while seen < len(searches):
                query, docs = searches[seen]
                seen += 1
                yield "search", {"query": query}
                yield "sources", {"items": self._sources(docs)}
            if mode == "messages":
                msg, meta = chunk
                if meta.get("langgraph_node") == "model" and not getattr(msg, "tool_call_chunks", None):
                    text = _text(msg.content)
                    if text:
                        yield "token", {"text": text}

    # ----- view -----
    def state(self) -> dict:
        """Everything the UI shows. Never includes an API key, only whether one is set."""
        return {
            "providers": [
                {
                    "id": p.id, "label": p.label, "local": p.local, "key_url": p.key_url,
                    "needs_key": bool(p.key_env) and not p.local,
                    "has_key": bool(prov._key(p)), "chat_model": p.chat_model,
                    "embeddings": bool(p.embed_model), "suggestions": list(p.suggestions),
                }
                for p in prov.PROVIDERS.values()
            ],
            "custom_base_url": os.getenv("CUSTOM_BASE_URL", ""),
            "settings": dict(self.settings),
            "index": self.index,
            "docs": self.docs(),
        }


def create_app(token: str, engine: Engine | None = None, allowed_hosts=None) -> FastAPI:
    """`allowed_hosts`: accepted Host headers, e.g. ["127.0.0.1:8123", "localhost:8123"].
    None skips the Host check (tests); the desktop app always passes the real ones."""
    engine = engine or Engine()
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.engine = engine
    app.add_middleware(GuardMiddleware, allowed_hosts=allowed_hosts)
    expected = token.encode()

    def auth(x_token: str = Header(default="")):
        if not hmac.compare_digest(x_token.encode(), expected):
            raise HTTPException(401, "bad token")

    async def json_body(request: Request):
        try:
            return json.loads(await read_capped(request, MAX_JSON_BYTES))
        except ValueError:
            raise HTTPException(400, "The request body must be JSON.") from None

    @app.get("/")
    def index():
        return FileResponse(WEB_DIR / "index.html", headers={"Cache-Control": "no-store"})

    @app.get("/api/state", dependencies=[Depends(auth)])
    def state():
        return engine.state()

    @app.post("/api/settings", dependencies=[Depends(auth)])
    async def settings(request: Request):
        if engine.update(await json_body(request)):
            engine.rebuild_async()
        return engine.state()

    @app.get("/api/models", dependencies=[Depends(auth)])
    def models(provider: str):
        if provider not in prov.PROVIDERS:
            raise HTTPException(404, "unknown provider")
        return {"models": prov.list_models(provider)}

    @app.post("/api/docs", dependencies=[Depends(auth)])
    async def add_doc(request: Request, name: str):
        clean_doc_name(name)  # fail fast, before reading the body
        engine.add_doc(name, await read_capped(request, MAX_UPLOAD_BYTES))
        engine.rebuild_async()
        return engine.state()

    @app.delete("/api/docs/{name}", dependencies=[Depends(auth)])
    def remove_doc(name: str):
        engine.remove_doc(name)
        engine.rebuild_async()
        return engine.state()

    @app.post("/api/reindex", dependencies=[Depends(auth)])
    def reindex():
        engine.rebuild_async()
        return engine.state()

    @app.post("/api/chat", dependencies=[Depends(auth)])
    async def chat(request: Request):
        body = await json_body(request)
        question = body.get("question") if isinstance(body, dict) else None
        if question is not None and not isinstance(question, str):
            raise HTTPException(400, "question must be text.")
        question = (question or "").strip()
        if not question:
            raise HTTPException(400, "empty question")
        if len(question) > MAX_QUESTION_CHARS:
            raise HTTPException(400, f"The question is longer than {MAX_QUESTION_CHARS} characters.")

        def events():
            for event, data in engine.stream_answer(question):
                yield f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

        return StreamingResponse(events(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
    if (WEB_DIR / "assets").is_dir():  # Vite/React build output
        app.mount("/assets", StaticFiles(directory=WEB_DIR / "assets"), name="assets")
    return app
