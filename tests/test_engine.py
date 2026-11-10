"""Engine bug fixes: seeding, error labels, model lists, rebuild coalescing, PDF safety, fake mode, home folder."""

import importlib
import io
import json
import threading
import time

import pypdf
import pytest

from rag_lab import indexing


# ----- sample handbook is seeded once -----

def test_sample_seeded_once(server):
    engine = server.Engine()
    sample = server.DOCS_DIR / "sample-handbook.md"
    assert sample.exists() and server.SEEDED.exists()
    sample.unlink()
    server.Engine()
    assert not sample.exists()  # deleting it sticks across restarts
    assert server.Engine().docs() == [] and engine.docs() == []


def test_existing_docs_not_reseeded(home, server):
    (home / "docs").mkdir()
    (home / "docs" / "mine.md").write_text("hello")
    server.Engine()
    assert not (home / "docs" / "sample-handbook.md").exists()
    assert server.SEEDED.exists()


# ----- friendly_error knows embedding sources -----

def test_friendly_error_embedding_labels(server):
    msg = server.friendly_error(ConnectionError("Connection refused while downloading"), "local")
    assert "on-device embedding model" in msg and "Custom" not in msg
    msg = server.friendly_error(RuntimeError("401 authentication failed"), "fake")
    assert msg.startswith("The fake embedding model rejected")
    msg = server.friendly_error(RuntimeError("401 authentication failed"), "openai")
    assert msg.startswith("OpenAI rejected")
    msg = server.friendly_error(RuntimeError("connection refused"), "ollama")
    assert msg.startswith("Cannot reach Ollama")


def test_rebuild_error_uses_embedding_label(server, monkeypatch):
    engine = server.Engine()
    engine.settings["embeddings"] = "local"

    def boom(*a, **k):
        raise RuntimeError("429 rate limit")

    monkeypatch.setattr(server.prov, "get_embeddings", boom)
    engine.rebuild()
    assert engine.index["status"] == "error"
    assert engine.index["error"].startswith("The on-device embedding model says")
    assert engine.index["warnings"] == []


# ----- list_models for custom endpoints -----

class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_list_models_custom_without_key(server, monkeypatch):
    prov = server.prov
    calls = []

    def fake_urlopen(req, timeout):
        calls.append((req.full_url, req.headers))
        return _Resp(json.dumps({"data": [{"id": "my-model"}, {"id": "other"}]}).encode())

    monkeypatch.setattr(prov.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.delenv("CUSTOM_API_KEY", raising=False)
    monkeypatch.delenv("CUSTOM_BASE_URL", raising=False)
    assert prov.list_models("custom") == []  # no URL: nothing to ask
    assert calls == []
    monkeypatch.setenv("CUSTOM_BASE_URL", "http://127.0.0.1:9999/v1/")
    assert prov.list_models("custom") == ["my-model", "other"]
    assert calls[0][0] == "http://127.0.0.1:9999/v1/models"
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    assert prov.list_models("groq") == list(prov.PROVIDERS["groq"].suggestions)  # keyed provider, no key
    assert len(calls) == 1


# ----- rebuilds are coalesced -----

def test_rebuild_async_coalesces(server, monkeypatch):
    engine = server.Engine()
    started, release = threading.Event(), threading.Event()
    runs = []

    def slow_rebuild():
        runs.append(1)
        started.set()
        release.wait(5)

    monkeypatch.setattr(engine, "rebuild", slow_rebuild)
    threads_before = threading.active_count()
    engine.rebuild_async()
    assert started.wait(5)
    for _ in range(20):
        engine.rebuild_async()
    assert threading.active_count() <= threads_before + 1  # no pile of waiting threads
    release.set()
    deadline = time.time() + 5
    while engine._rebuild_running and time.time() < deadline:
        time.sleep(0.01)
    assert not engine._rebuild_running
    assert len(runs) == 2  # the running pass plus exactly one more for all the queued requests
    engine.rebuild_async()  # and a new request after that starts again
    deadline = time.time() + 5
    while len(runs) < 3 and time.time() < deadline:
        time.sleep(0.01)
    assert len(runs) == 3


def test_rebuild_loop_survives_exceptions(server, monkeypatch):
    engine = server.Engine()

    def broken():
        raise RuntimeError("x")

    monkeypatch.setattr(engine, "rebuild", broken)
    engine.rebuild_async()
    deadline = time.time() + 5
    while engine._rebuild_running and time.time() < deadline:
        time.sleep(0.01)
    assert not engine._rebuild_running


# ----- PDF and file safety -----

def _pdf(pages: int) -> bytes:
    w = pypdf.PdfWriter()
    for _ in range(pages):
        w.add_blank_page(width=72, height=72)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def test_bad_file_does_not_break_index(client, server):
    client.post("/api/docs?name=broken.pdf", content=b"%PDF-1.4 this is not a pdf")
    server_engine = client.app.state.engine
    server_engine.rebuild()
    index = server_engine.index
    assert index["status"] == "ready" and index["chunks"] > 0
    assert [w["source"] for w in index["warnings"]] == ["broken.pdf"]
    assert "skipped" in index["warnings"][0]["message"]
    assert client.get("/api/state").json()["index"]["warnings"] == index["warnings"]


def test_big_file_skipped_and_page_cap(tmp_path, monkeypatch):
    (tmp_path / "ok.md").write_text("fine")
    pdf = _pdf(3)
    (tmp_path / "long.pdf").write_bytes(pdf)
    (tmp_path / "big.txt").write_text("x" * (len(pdf) + 100))
    monkeypatch.setattr(indexing, "MAX_FILE_BYTES", len(pdf) + 50)
    monkeypatch.setattr(indexing, "MAX_PDF_PAGES", 2)
    warnings = []
    docs = indexing.load_documents(tmp_path, warnings=warnings)
    pdf_pages = [d for d in docs if d.metadata["source"] == "long.pdf"]
    assert len(pdf_pages) <= 2  # blank pages may be empty but are capped
    msgs = {w["source"]: w["message"] for w in warnings}
    assert "larger than" in msgs["big.txt"]
    assert "first 2 of 3 pages" in msgs["long.pdf"]
    assert all(d.metadata["source"] != "big.txt" for d in docs)


def test_limits_defaults():
    assert indexing.MAX_FILE_BYTES == 50 * 1024 * 1024
    assert indexing.MAX_PDF_PAGES == 2000


def test_load_documents_still_raises_without_warning_list(tmp_path):
    (tmp_path / "bad.pdf").write_bytes(b"not a pdf")
    with pytest.raises(Exception):
        indexing.load_documents(tmp_path)


def test_index_has_warnings_field_when_empty(server):
    engine = server.Engine()
    (server.DOCS_DIR / "sample-handbook.md").unlink()
    engine.rebuild()
    assert engine.index == {"status": "empty", "chunks": 0, "pages": 0, "error": None, "warnings": []}


# ----- RAG_LAB_FAKE_LLM -----

def test_fake_llm_streams_through_graph(server, monkeypatch):
    monkeypatch.setenv("RAG_LAB_FAKE_LLM", "1")
    prov = server.prov
    from langchain_core.embeddings import DeterministicFakeEmbedding

    assert isinstance(prov.get_embeddings("local"), DeterministicFakeEmbedding)
    engine = server.Engine()
    engine.settings["embeddings"] = "local"
    engine.rebuild()
    assert engine.index["status"] == "ready"
    events = list(engine.stream_answer("How many absences?"))
    tokens = [d["text"] for e, d in events if e == "token"]
    assert len(tokens) > 1  # really streamed, not one blob
    assert "".join(tokens) == prov.FAKE_ANSWER
    assert events[0][0] == "sources" and events[-1][0] == "done"


def test_fake_llm_agent_mode(server, monkeypatch):
    monkeypatch.setenv("RAG_LAB_FAKE_LLM", "1")
    engine = server.Engine()
    engine.rebuild()
    engine.settings["mode"] = "agent"
    events = list(engine.stream_answer("How many absences?"))
    assert "".join(d["text"] for e, d in events if e == "token") == server.prov.FAKE_ANSWER
    assert not [e for e, _ in events if e == "error"]


def test_fake_llm_off_by_default(server, monkeypatch):
    monkeypatch.delenv("RAG_LAB_FAKE_LLM", raising=False)
    from langchain_openai import ChatOpenAI

    assert isinstance(server.prov.get_llm("ollama"), ChatOpenAI)


# ----- home folder -----

@pytest.fixture
def no_home_env(monkeypatch):
    monkeypatch.delenv("RAG_LAB_HOME", raising=False)
    monkeypatch.delenv("PAGEWISE_HOME", raising=False)
    from rag_lab import providers

    return providers


def test_home_defaults_to_pagewise(tmp_path, no_home_env):
    assert no_home_env.resolve_home(tmp_path) == tmp_path / ".pagewise"
    assert not (tmp_path / ".pagewise").exists()  # created later, by the engine


def test_home_migrates_old_folder_once(tmp_path, no_home_env):
    old = tmp_path / ".rag-lab"
    (old / "docs").mkdir(parents=True)
    (old / "docs" / "a.md").write_text("A")
    (old / ".env").write_text("GROQ_API_KEY=x\n")
    (old / "browser-profile").mkdir()
    (old / "browser-profile" / "cache").write_text("big")
    home = no_home_env.resolve_home(tmp_path)
    assert home == tmp_path / ".pagewise"
    assert (home / "docs" / "a.md").read_text() == "A"
    assert (home / ".env").read_text() == "GROQ_API_KEY=x\n"
    assert not (home / "browser-profile").exists()
    assert (old / "docs" / "a.md").exists()  # copied, not moved
    (old / "docs" / "b.md").write_text("B")
    no_home_env.resolve_home(tmp_path)
    assert not (home / "docs" / "b.md").exists()  # only once


def test_home_env_overrides(tmp_path, monkeypatch, no_home_env):
    monkeypatch.setenv("RAG_LAB_HOME", str(tmp_path / "r"))
    assert no_home_env.resolve_home(tmp_path) == tmp_path / "r"
    monkeypatch.setenv("PAGEWISE_HOME", str(tmp_path / "p"))
    assert no_home_env.resolve_home(tmp_path) == tmp_path / "p"


def test_module_home_follows_env(tmp_path, monkeypatch):
    monkeypatch.delenv("RAG_LAB_HOME", raising=False)
    monkeypatch.setenv("PAGEWISE_HOME", str(tmp_path))
    from rag_lab import providers

    try:
        assert importlib.reload(providers).HOME == tmp_path
    finally:
        monkeypatch.undo()
        importlib.reload(providers)
