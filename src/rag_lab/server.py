"""Local API behind the desktop window.

Binds to 127.0.0.1 only and requires a per-launch token on every /api call, so
other websites and other users on the machine cannot drive it.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
import threading
import traceback
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from langchain_core.vectorstores import InMemoryVectorStore

from rag_lab import providers as prov
from rag_lab.indexing import load_documents, split_documents

if getattr(sys, "frozen", False):
    BUNDLE = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    WEB_DIR = BUNDLE / "rag_lab" / "web"
    SAMPLE = BUNDLE / "data" / "handbook.md"
else:
    WEB_DIR = Path(__file__).parent / "web"
    SAMPLE = Path(__file__).resolve().parents[2] / "data" / "handbook.md"

CONFIG = prov.HOME / "config.json"
DOCS_DIR = prov.HOME / "docs"
INDEX_DIR = prov.HOME / "index"
DOC_TYPES = {".pdf", ".md", ".txt"}

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


def friendly_error(exc: Exception, provider: str) -> str:
    text = f"{type(exc).__name__}: {exc}"
    p = prov.PROVIDERS.get(provider, prov.PROVIDERS["custom"])
    low = text.lower()
    if "401" in text or "authentication" in low or "invalid x-api-key" in low or "incorrect api key" in low:
        return f"{p.label} rejected the API key. Open Settings and paste a valid key."
    if "connection" in low and p.local:
        return f"Cannot reach {p.label}. Start it first (for Ollama: open the Ollama app or run `ollama serve`)."
    if "404" in text and "model" in low:
        return f"{p.label} does not know this model. Pick another one in the model menu."
    if "429" in text or "rate limit" in low or "quota" in low:
        return f"{p.label} says you hit a rate limit or ran out of credit."
    return text[:400]


class Engine:
    """Holds settings, the index and the models."""

    def __init__(self):
        prov.HOME.mkdir(parents=True, exist_ok=True)
        DOCS_DIR.mkdir(exist_ok=True)
        INDEX_DIR.mkdir(exist_ok=True)
        if not any(DOCS_DIR.iterdir()) and SAMPLE.exists():
            shutil.copy(SAMPLE, DOCS_DIR / "sample-handbook.md")
        saved = json.loads(CONFIG.read_text()) if CONFIG.exists() else {}
        self.settings = {**DEFAULTS, **{k: v for k, v in saved.items() if k in DEFAULTS}}
        self.lock = threading.Lock()
        self.store = None
        self.index = {"status": "idle", "chunks": 0, "pages": 0, "error": None}
        self._llm = None
        self._llm_key = None

    # ----- settings -----
    def save(self):
        CONFIG.write_text(json.dumps(self.settings, indent=2))

    def update(self, values: dict) -> bool:
        """Apply new settings. Returns True when the index must be rebuilt."""
        before = self._fingerprint()
        keys = values.pop("keys", None) or {}
        custom_url = values.pop("custom_base_url", None)
        for k, v in values.items():
            if k in DEFAULTS:
                self.settings[k] = type(DEFAULTS[k])(v)
        env = {}
        for pid, key in keys.items():
            p = prov.PROVIDERS.get(pid)
            if p and p.key_env and key is not None:
                env[p.key_env] = key.strip()
        if custom_url is not None:
            env["CUSTOM_BASE_URL"] = custom_url.strip()
        if env:
            prov.save_user_settings(env)
        self.save()
        self._llm = None
        embed_key_changed = any(
            prov.PROVIDERS.get(pid) and prov.PROVIDERS[pid].id == self.settings["embeddings"] for pid in keys
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
        files = [(d["name"], d["size"], (DOCS_DIR / d["name"]).stat().st_mtime) for d in self.docs()]
        raw = json.dumps([files, s["embeddings"], s["embed_model"], s["chunk_size"], s["chunk_overlap"]])
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def rebuild_async(self):
        threading.Thread(target=self.rebuild, daemon=True).start()

    def rebuild(self):
        with self.lock:
            s = self.settings
            if not self.docs():
                self.store = None
                self.index = {"status": "empty", "chunks": 0, "pages": 0, "error": None}
                return
            try:
                key = self._fingerprint()
                self.index = {**self.index, "status": "loading", "error": None}
                emb = prov.get_embeddings(s["embeddings"], s["embed_model"] or None)
                cache = INDEX_DIR / f"{key}.json"
                docs = load_documents(DOCS_DIR)
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
                self.index = {"status": "ready", "chunks": chunks, "pages": len(docs), "error": None}
            except Exception as exc:
                traceback.print_exc()
                self.store = None
                self.index = {"status": "error", "chunks": 0, "pages": 0,
                              "error": friendly_error(exc, s["embeddings"])}

    def add_doc(self, name: str, data: bytes) -> str:
        safe = re.sub(r"[^\w.\- ]+", "_", Path(name).name).strip() or "document.txt"
        if Path(safe).suffix.lower() not in DOC_TYPES:
            raise HTTPException(400, "Only .pdf, .md and .txt files are supported.")
        (DOCS_DIR / safe).write_bytes(data)
        return safe

    def remove_doc(self, name: str):
        target = DOCS_DIR / Path(name).name
        if target.exists():
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
        from rag_lab.graph import build_graph

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
        from rag_lab.agent import build_agent

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
            "settings": self.settings,
            "index": self.index,
            "docs": self.docs(),
        }


def create_app(token: str, engine: Engine | None = None) -> FastAPI:
    engine = engine or Engine()
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.engine = engine

    def auth(x_token: str = Header(default="")):
        if x_token != token:
            raise HTTPException(401, "bad token")

    @app.get("/")
    def index():
        return FileResponse(WEB_DIR / "index.html", headers={"Cache-Control": "no-store"})

    @app.get("/api/state", dependencies=[Depends(auth)])
    def state():
        return engine.state()

    @app.post("/api/settings", dependencies=[Depends(auth)])
    async def settings(request: Request):
        if engine.update(await request.json()):
            engine.rebuild_async()
        return engine.state()

    @app.get("/api/models", dependencies=[Depends(auth)])
    def models(provider: str):
        if provider not in prov.PROVIDERS:
            raise HTTPException(404, "unknown provider")
        return {"models": prov.list_models(provider)}

    @app.post("/api/docs", dependencies=[Depends(auth)])
    async def add_doc(request: Request, name: str):
        engine.add_doc(name, await request.body())
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
        question = ((await request.json()).get("question") or "").strip()
        if not question:
            raise HTTPException(400, "empty question")

        def events():
            for event, data in engine.stream_answer(question):
                yield f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

        return StreamingResponse(events(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
    return app
