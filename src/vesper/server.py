"""Vesper local API, behind the desktop window.

Binds to 127.0.0.1 only and requires a per-launch token on every /api call, so
other websites and other users on the machine cannot drive it. On top of that:
the Host header must name this server (DNS rebinding), cross-origin writes are
refused, every response carries strict security headers, and all input is
validated and size-capped.
"""

from __future__ import annotations

import hmac
import json
import os
import queue
import re
import secrets
import sys
import threading
import traceback
from collections import OrderedDict
from pathlib import Path
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import MutableHeaders

from vesper import __version__
from vesper import providers as prov

if getattr(sys, "frozen", False):
    BUNDLE = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    WEB_DIR = BUNDLE / "vesper" / "web"
else:
    WEB_DIR = Path(__file__).parent / "web"

CONFIG = prov.HOME / "config.json"
MAX_JSON_BYTES = 256 * 1024
MAX_QUESTION_CHARS = 100_000
MAX_CHAT_BYTES = 48 * 1024 * 1024  # a question with pasted images
MAX_AUDIO_BYTES = 8 * 1024 * 1024
MAX_ATTACHMENTS = 20
MAX_MODEL_LEN = 200
MAX_KEY_LEN = 500
AGENT_MODES = ("ask", "auto-edit", "yolo", "plan")
MAX_SESSIONS = 30
APPROVAL_TIMEOUT_S = 600
MAX_HISTORY_TURNS = 40
CHAT_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
SESSION_ID = re.compile(r"^[0-9a-f]{4,32}$")  # an id from the session database, or its first characters

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
    "workspace": "",
    "agent_mode": "ask",
    "thinking": "off",
}


OBSOLETE_SETTINGS = ("embeddings", "embed_model", "chunk_size", "chunk_overlap", "k", "mode", "check")


def friendly_error(exc: Exception, provider: str) -> str:
    """A short message for an error from `provider`'s model."""
    text = f"{type(exc).__name__}: {exc}"
    low = text.lower()
    p = prov.PROVIDERS.get(provider)
    label, local = (p.label, p.local) if p else (provider or "The provider", False)
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
    if name == "model":
        return _text_field(name, value, MAX_MODEL_LEN)
    if name == "agent_mode":
        if value not in AGENT_MODES:
            _bad("agent_mode must be ask, auto-edit, yolo or plan.")
        return value
    if name == "thinking":
        if value not in prov.THINKING:
            _bad("thinking must be off, low, medium or high.")
        return value
    if name == "workspace":
        value = _text_field(name, value, 4096)
        if not value:
            return ""
        folder = Path(os.path.expanduser(value))
        if not folder.is_absolute():
            _bad("The folder must be a full path.")
        try:
            folder = folder.resolve()
        except OSError:
            _bad("That folder cannot be opened.")
        if not folder.is_dir():
            _bad("That folder does not exist.")
        return str(folder)
    raise KeyError(name)


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
    return settings


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
    """Holds the settings, the model and the open chats."""

    def __init__(self):
        prov.HOME.mkdir(parents=True, exist_ok=True)
        try:
            prov.HOME.chmod(0o700)
        except OSError:  # Windows ignores POSIX modes
            pass
        self.settings = _load_saved_settings()
        try:
            self._config_mtime = CONFIG.stat().st_mtime_ns
        except OSError:
            self._config_mtime = None
        self._llm = None
        self._llm_key = None
        self._sessions: OrderedDict[str, dict] = OrderedDict()
        self._sessions_lock = threading.Lock()
        self._approvals: dict[str, dict] = {}
        self._history_db = None

    def history_db(self):
        """The SQLite session store shared with the terminal app; None if it cannot be opened."""
        if self._history_db is None:
            from vesper.store import Store

            try:
                self._history_db = Store()
            except Exception:  # noqa: BLE001 - the app works without saved history
                traceback.print_exc()
                self._history_db = False
        return self._history_db or None

    # ----- settings -----
    def sync_settings(self) -> None:
        """Re-read config.json when another Vesper process (the terminal app) changed it."""
        try:
            mtime = CONFIG.stat().st_mtime_ns
        except OSError:
            mtime = None
        if mtime != self._config_mtime:
            fresh = _load_saved_settings()
            if fresh != self.settings:
                self.settings, self._llm = fresh, None
            self._config_mtime = mtime

    def save(self, changed: dict | None = None):
        """Write `changed` (default: every setting) into config.json, keeping what else is there:
        the terminal app keeps its own values in the same file."""
        try:
            current = json.loads(CONFIG.read_text()) if CONFIG.exists() else {}
        except (OSError, ValueError):
            current = {}
        if not isinstance(current, dict):
            current = {}
        for name in OBSOLETE_SETTINGS:  # left by older versions
            current.pop(name, None)
        tmp = CONFIG.with_name(f"config.json.{secrets.token_hex(4)}.tmp")
        tmp.write_text(json.dumps({**current, **(self.settings if changed is None else changed)}, indent=2))
        os.replace(tmp, CONFIG)
        try:
            self._config_mtime = CONFIG.stat().st_mtime_ns
        except OSError:
            pass

    def update(self, values: dict) -> None:
        """Apply new settings; keys go to the .env file the terminal app reads too."""
        self.sync_settings()
        new, env, custom_url = validate_settings(values, self.settings)
        if custom_url is not None:
            env["CUSTOM_BASE_URL"] = custom_url
        if env:
            try:
                prov.save_user_settings(env)
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
        changed = {k: v for k, v in new.items() if self.settings.get(k) != v}
        self.settings = new
        if changed:
            self.save(changed)
        self._llm = None

    # ----- chat -----
    def llm(self):
        s = self.settings
        key = (s["provider"], s["model"], s["thinking"])
        if self._llm is None or self._llm_key != key:
            self._llm = prov.get_llm(s["provider"], s["model"] or None, thinking=s["thinking"])
            self._llm_key = key
        return self._llm

    def stream_answer(self, question: str, chat_id: str | None = None, history: list | None = None,
                      session_id: str | None = None, attachments: list | None = None, images: list | None = None,
                      retry: bool = False):
        """Yields (event, data) pairs for the chat stream.

        `session_id` continues a conversation saved in the session database. `attachments` are
        paths of files or folders, `images` are pasted pictures ({name, mime, data} in base64).
        `retry` forgets the chat's last question and answer first, to ask again.
        """
        self.sync_settings()
        yield from self._stream_folder(question, chat_id or "default", history or [], session_id,
                                       attachments or [], images or [], retry)

    # ----- folder agent -----
    def _folder_session(self, chat_id: str, history: list, session_id: str | None = None):
        """The agent session for one chat, rebuilt when the model changes. It follows the folder
        picked in the app, and the agent may move on from there itself."""
        from langchain_core.messages import AIMessage, HumanMessage

        from vesper import permissions
        from vesper.coder import Session
        from vesper.workspace import Workspace

        s = self.settings
        key = (s["provider"], s["model"], s["thinking"])
        rules, hosts = permissions.load_rules("allow_commands"), permissions.load_rules("allow_hosts")
        with self._sessions_lock:
            entry = self._sessions.get(chat_id)
            if entry is not None and entry["key"] == key:
                self._sessions.move_to_end(chat_id)
                ws = entry["session"].workspace
                ws.mode = s["agent_mode"]
                ws.rules, ws.hosts = rules, hosts  # rules saved in another chat or the terminal count here too
                if entry["picked"] != s["workspace"]:  # the user opened another folder in the app
                    entry["picked"] = s["workspace"]
                    ws.change_directory(s["workspace"])
                return entry
            db = self.history_db()
            saved = db.get_session(session_id) if db is not None and session_id and entry is None else None
            if saved and Path(saved["cwd"]).is_dir():  # a saved conversation continues where it was
                self.settings["workspace"] = saved["cwd"]
                self.save({"workspace": saved["cwd"]})
            ws = Workspace(Path(s["workspace"]), mode=s["agent_mode"], rules=rules, hosts=hosts,
                           on_rule=lambda key, rule: permissions.save_rule(rule, key))
            session = Session(self.llm(), ws, db, session_id=saved["id"] if saved else None, source="desktop",
                              provider=s["provider"], model=s["model"],
                              context_tokens=prov.context_tokens(s["provider"]))
            if entry is not None:
                old = entry["session"]  # same chat, new model: keep the conversation and its saved row
                session.history, session.id, session.title, session._saved = old.history, old.id, old.title, old._saved
                session._copies, ws.tasks = old._copies, old.workspace.tasks
            elif saved:
                pass  # the history came from the database
            else:
                for turn in history[-MAX_HISTORY_TURNS:]:
                    text = str(turn.get("text", ""))[:20000] if isinstance(turn, dict) else ""
                    if text:
                        session.history.append(HumanMessage(text) if turn.get("role") == "user" else AIMessage(text))
            entry = {"key": key, "session": session, "lock": threading.Lock(), "picked": s["workspace"]}
            self._sessions[chat_id] = entry
            while len(self._sessions) > MAX_SESSIONS:
                self._sessions.popitem(last=False)
            return entry

    def _stream_folder(self, question: str, chat_id: str, history: list, session_id: str | None = None,
                       attachments: list | None = None, images: list | None = None, retry: bool = False):
        s = self.settings
        if not s["workspace"]:  # no project opened yet: start in the home folder, like a terminal does
            s["workspace"] = str(Path.home())
        try:
            entry = self._folder_session(chat_id, history, session_id)
        except Exception as exc:
            yield "error", {"message": friendly_error(exc, s["provider"])}
            yield "done", {}
            return
        if not entry["lock"].acquire(blocking=False):
            yield "error", {"message": "This chat is still answering. Wait for it or stop it first."}
            yield "done", {}
            return

        events: queue.Queue = queue.Queue()
        closed = threading.Event()
        mine: list[str] = []
        session = entry["session"]
        entry["stop"] = lambda: (closed.set(), session.workspace.cancel())
        if retry:
            session.drop_last_turn()
        try:
            text, image_blocks, shown = self._attach(session.workspace, question, attachments or [], images or [])
        except HTTPException as exc:
            entry["lock"].release()
            yield "error", {"message": exc.detail}
            yield "done", {}
            return

        def approve(action: dict) -> str:
            if closed.is_set():
                return "no"
            aid = secrets.token_hex(8)
            slot = {"event": threading.Event(), "answer": "no"}
            self._approvals[aid] = slot
            mine.append(aid)
            events.put(("approval", {"id": aid, **action}))
            answered = slot["event"].wait(APPROVAL_TIMEOUT_S)
            self._approvals.pop(aid, None)
            if not answered:
                events.put(("approval_done", {"id": aid, "answer": "no", "timeout": True}))
            return slot["answer"] if answered and not closed.is_set() else "no"

        session.workspace.approve = approve
        # Shown live by the app: output of a running command, and edits that needed no approval.
        session.workspace.on_output = lambda line: events.put(("output", {"line": line[:2000]}))
        session.workspace.on_auto = lambda action: events.put(("auto", action))
        yield "session", {"id": session.id, "cwd": str(session.workspace.root)}
        if shown["names"] or shown["problems"]:
            yield "attached", {"names": shown["names"], "problems": shown["problems"]}

        def work():
            try:
                for item in session.ask(text, shown=shown["question"], images=image_blocks):
                    if closed.is_set():
                        break
                    if item[0] == "cwd":  # the agent moved: the app's folder follows it
                        entry["picked"] = self.settings["workspace"] = item[1]["path"]
                        self.save({"workspace": item[1]["path"]})
                    if item[0] != "done":
                        events.put(item)
            except Exception as exc:
                traceback.print_exc()
                events.put(("error", {"message": friendly_error(exc, s["provider"])}))
            finally:
                events.put(None)
                entry["lock"].release()

        threading.Thread(target=work, daemon=True).start()
        try:
            while True:
                item = events.get()
                if item is None:
                    break
                yield item
        finally:
            # The client went away or the answer ended: stop commands, refuse anything still waiting.
            closed.set()
            session.workspace.cancel()
            entry.pop("stop", None)
            for aid in mine:
                slot = self._approvals.pop(aid, None)
                if slot is not None:
                    slot["answer"] = "no"
                    slot["event"].set()
        yield "done", {}

    @staticmethod
    def _attach(workspace, question: str, refs: list, images: list):
        """(message text, image blocks, {"question", "names", "problems"}) for one question."""
        import base64
        import binascii

        from vesper import attachments as att

        out = att.attach(workspace, [r for r in refs if isinstance(r, str)][:MAX_ATTACHMENTS])
        for img in images[:att.MAX_IMAGES]:
            name = str(img.get("name") or "pasted image")[:120] if isinstance(img, dict) else "image"
            mime = img.get("mime") if isinstance(img, dict) else None
            if mime not in att.IMAGE_TYPES.values():
                out.problems.append(f"{name}: only PNG, JPEG, GIF and WebP images can be attached")
                continue
            try:
                data = base64.b64decode(str(img.get("data", "")), validate=True)
            except (binascii.Error, ValueError):
                out.problems.append(f"{name}: the image data is damaged")
                continue
            if len(data) > att.MAX_IMAGE_BYTES:
                out.problems.append(f"{name}: images must be under 5 MB")
                continue
            out.images.append(att.image_block(data, mime, name))
            out.names.append(name)
        return out.message(question), out.images, {"question": question, "names": out.names, "problems": out.problems}

    def stop_chat(self, chat_id: str) -> bool:
        """Stop a running answer: end its command and refuse the next tool call."""
        entry = self._sessions.get(chat_id)
        stop = entry.get("stop") if entry else None
        if stop is None:
            return False
        stop()
        return True

    def rename_chat(self, session_id: str, title: str) -> None:
        db = self.history_db()
        if db is None:
            raise HTTPException(503, "The session database cannot be opened.")
        if db.get_session(session_id) is None:
            raise HTTPException(404, "That conversation is not in the history.")
        with self._sessions_lock:
            for entry in self._sessions.values():
                if entry["session"].id == session_id:
                    entry["session"].title = title
        db.update_session(session_id, title=title)

    def answer_approval(self, aid: str, answer: str) -> bool:
        slot = self._approvals.get(aid)
        if slot is None:
            return False
        slot["answer"] = answer
        slot["event"].set()
        return True

    def _idle_session(self, chat_id: str, session_id: str | None):
        """(entry, session) of a chat that is not answering right now, with its lock held."""
        if not self.settings["workspace"]:
            raise HTTPException(409, "Open a folder first.")
        try:
            entry = self._folder_session(chat_id, [], session_id)
        except Exception as exc:
            raise HTTPException(409, friendly_error(exc, self.settings["provider"])) from exc
        if not entry["lock"].acquire(blocking=False):
            raise HTTPException(409, "This chat is still answering. Wait for it or stop it first.")
        return entry, entry["session"]

    def undo_chat(self, chat_id: str, session_id: str | None = None) -> list[str]:
        """Put back the files the agent's latest edits changed. Returns their paths."""
        entry, session = self._idle_session(chat_id, session_id)
        try:
            return session.undo()
        except OSError as exc:
            raise HTTPException(500, f"Could not put the files back: {exc}") from exc
        finally:
            entry["lock"].release()

    def compact_chat(self, chat_id: str, session_id: str | None = None) -> dict:
        """Summarize the older turns of a chat to free context."""
        entry, session = self._idle_session(chat_id, session_id)
        try:
            before = session.tokens["context"]
            done = session.compact()
            return {"compacted": done, "before": before, "context": session.tokens["context"]}
        finally:
            entry["lock"].release()

    def forget_chat(self, chat_id: str) -> None:
        with self._sessions_lock:
            self._sessions.pop(chat_id, None)

    # ----- saved sessions (the SQLite database the terminal app writes too) -----
    def saved_sessions(self, limit: int = 100) -> list[dict]:
        db = self.history_db()
        return db.list_sessions(limit) if db is not None else []

    def saved_session(self, session_id: str) -> dict | None:
        """One saved conversation with its turns, ready for the app to draw."""
        from vesper.coder import transcript

        db = self.history_db()
        row = db.get_session(session_id) if db is not None else None
        if row is None:
            return None
        return {**row, "turns": transcript(db.load_messages(row["id"]))}

    def delete_saved_session(self, session_id: str) -> None:
        db = self.history_db()
        if db is None:
            raise HTTPException(503, "The session database cannot be opened.")
        if db.get_session(session_id) is None:
            raise HTTPException(404, "That conversation is not in the history.")
        with self._sessions_lock:
            for chat_id, entry in list(self._sessions.items()):
                if entry["session"].id == session_id:
                    del self._sessions[chat_id]
        db.delete_session(db.get_session(session_id)["id"])

    # ----- view -----
    def state(self) -> dict:
        """Everything the UI shows. Never includes an API key, only whether one is set."""
        return {
            "providers": [
                {
                    "id": p.id, "label": p.label, "local": p.local, "key_url": p.key_url,
                    "needs_key": bool(p.key_env) and not p.local,
                    "has_key": bool(prov._key(p)), "chat_model": p.chat_model,
                    "suggestions": list(p.suggestions),
                }
                for p in prov.PROVIDERS.values()
            ],
            "custom_base_url": os.getenv("CUSTOM_BASE_URL", ""),
            "settings": dict(self.settings),
            "version": __version__,
            "home": str(Path.home()),
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
        engine.sync_settings()
        return engine.state()

    @app.post("/api/settings", dependencies=[Depends(auth)])
    async def settings(request: Request):
        engine.update(await json_body(request))
        return engine.state()

    @app.get("/api/models", dependencies=[Depends(auth)])
    def models(provider: str):
        if provider not in prov.PROVIDERS:
            raise HTTPException(404, "unknown provider")
        return {"models": prov.list_models(provider)}

    @app.post("/api/dictation", dependencies=[Depends(auth)])
    async def dictation(request: Request):
        audio = await read_capped(request, MAX_AUDIO_BYTES)
        if not audio:
            raise HTTPException(400, "The recording is empty.")
        from vesper.dictation import transcribe

        try:
            text = await run_in_threadpool(transcribe, audio)
        except ImportError:
            raise HTTPException(503, 'Dictation is not installed. Install Vesper with the "dictation" extra.') from None
        except Exception:
            raise HTTPException(503, "Dictation failed. Check your recording and internet connection for the first model download.") from None
        return {"text": text}

    @app.post("/api/chat", dependencies=[Depends(auth)])
    async def chat(request: Request):
        try:
            body = json.loads(await read_capped(request, MAX_CHAT_BYTES))
        except ValueError:
            raise HTTPException(400, "The request body must be JSON.") from None
        question = body.get("question") if isinstance(body, dict) else None
        if question is not None and not isinstance(question, str):
            raise HTTPException(400, "question must be text.")
        question = (question or "").strip()
        if not question:
            raise HTTPException(400, "empty question")
        if len(question) > MAX_QUESTION_CHARS:
            raise HTTPException(400, f"The question is longer than {MAX_QUESTION_CHARS} characters.")
        chat_id = body.get("chat_id") or "default"
        if not isinstance(chat_id, str) or not CHAT_ID.match(chat_id):
            raise HTTPException(400, "chat_id must be 1-64 letters, digits, - or _.")
        history = body.get("history") or []
        if not isinstance(history, list):
            raise HTTPException(400, "history must be a list.")
        session_id = body.get("session_id")
        if session_id is not None and not (isinstance(session_id, str) and SESSION_ID.match(session_id)):
            raise HTTPException(400, "session_id is not a session id.")

        attachments, images = body.get("attachments") or [], body.get("images") or []
        if not isinstance(attachments, list) or not isinstance(images, list):
            raise HTTPException(400, "attachments and images must be lists.")
        retry = body.get("retry") is True

        def events():
            for event, data in engine.stream_answer(question, chat_id, history, session_id, attachments, images, retry):
                yield f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

        return StreamingResponse(events(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    @app.post("/api/approve", dependencies=[Depends(auth)])
    async def approve(request: Request):
        body = await json_body(request)
        aid = body.get("id") if isinstance(body, dict) else None
        answer = body.get("answer") if isinstance(body, dict) else None
        if not isinstance(aid, str) or answer not in ("yes", "always", "no"):
            raise HTTPException(400, "Send an id and an answer: yes, always or no.")
        if not engine.answer_approval(aid, answer):
            raise HTTPException(404, "That request is no longer waiting.")
        return {"ok": True}

    async def chat_ref(request: Request, chat_id: str) -> str | None:
        """Checks a chat id from the path; returns the saved-session id from the body, if any."""
        if not CHAT_ID.match(chat_id):
            raise HTTPException(400, "chat_id must be 1-64 letters, digits, - or _.")
        body = await json_body(request)
        session_id = body.get("session_id") if isinstance(body, dict) else None
        if session_id is not None and not (isinstance(session_id, str) and SESSION_ID.match(session_id)):
            raise HTTPException(400, "session_id is not a session id.")
        return session_id

    @app.post("/api/chats/{chat_id}/undo", dependencies=[Depends(auth)])
    async def undo(request: Request, chat_id: str):
        session_id = await chat_ref(request, chat_id)
        return {"paths": await run_in_threadpool(engine.undo_chat, chat_id, session_id)}

    @app.post("/api/chats/{chat_id}/compact", dependencies=[Depends(auth)])
    async def compact(request: Request, chat_id: str):
        session_id = await chat_ref(request, chat_id)
        return await run_in_threadpool(engine.compact_chat, chat_id, session_id)

    @app.get("/api/permissions", dependencies=[Depends(auth)])
    def get_permissions():
        from vesper import permissions

        return {"commands": permissions.load_rules("allow_commands"), "hosts": permissions.load_rules("allow_hosts"),
                "safe": list(permissions.SAFE_COMMANDS)}

    @app.post("/api/permissions/remove", dependencies=[Depends(auth)])
    async def remove_permission(request: Request):
        from vesper import permissions

        body = await json_body(request)
        kind = body.get("kind") if isinstance(body, dict) else None
        rule = body.get("rule") if isinstance(body, dict) else None
        if kind not in ("commands", "hosts") or not isinstance(rule, str) or len(rule) > 500:
            raise HTTPException(400, "Send kind (commands or hosts) and the rule to remove.")
        permissions.remove_rule(rule, "allow_commands" if kind == "commands" else "allow_hosts")
        return get_permissions()

    @app.get("/api/sessions", dependencies=[Depends(auth)])
    def sessions(limit: int = 100):
        return {"sessions": engine.saved_sessions(max(1, min(int(limit), 500)))}

    @app.get("/api/sessions/{session_id}", dependencies=[Depends(auth)])
    def session(session_id: str):
        found = engine.saved_session(session_id) if SESSION_ID.match(session_id) else None
        if found is None:
            raise HTTPException(404, "That conversation is not in the history.")
        return found

    @app.delete("/api/sessions/{session_id}", dependencies=[Depends(auth)])
    def delete_session(session_id: str):
        if not SESSION_ID.match(session_id):
            raise HTTPException(400, "That is not a session id.")
        engine.delete_saved_session(session_id)
        return {"ok": True}

    @app.post("/api/sessions/{session_id}/rename", dependencies=[Depends(auth)])
    async def rename_session(request: Request, session_id: str):
        body = await json_body(request)
        title = body.get("title") if isinstance(body, dict) else None
        if not SESSION_ID.match(session_id) or not isinstance(title, str) or not title.strip():
            raise HTTPException(400, "Send a title.")
        engine.rename_chat(session_id, " ".join(title.split())[:80])
        return {"ok": True}

    @app.post("/api/chats/{chat_id}/stop", dependencies=[Depends(auth)])
    def stop_chat(chat_id: str):
        if not CHAT_ID.match(chat_id):
            raise HTTPException(400, "chat_id must be 1-64 letters, digits, - or _.")
        return {"stopped": engine.stop_chat(chat_id)}

    @app.delete("/api/chats/{chat_id}", dependencies=[Depends(auth)])
    def forget_chat(chat_id: str):
        if CHAT_ID.match(chat_id):
            engine.forget_chat(chat_id)
        return {"ok": True}

    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
    if (WEB_DIR / "assets").is_dir():  # Vite/React build output
        app.mount("/assets", StaticFiles(directory=WEB_DIR / "assets"), name="assets")
    return app
