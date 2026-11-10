"""Shared fixtures: a throwaway home folder, fake models, and a reloaded server module."""

import importlib
import json
import os
import tempfile

# Before anything imports rag_lab: never read, migrate or write the real ~/.pagewise or ~/.rag-lab.
os.environ.pop("PAGEWISE_HOME", None)
os.environ["RAG_LAB_HOME"] = tempfile.mkdtemp(prefix="pagewise-tests-")

import pytest  # noqa: E402
from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import FakeListChatModel

ANSWER = "Two absences are allowed [page 3]."


@pytest.fixture(autouse=True)
def _restore_environ():
    """save_user_settings writes keys into os.environ; keep tests independent."""
    saved = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(saved)


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("RAG_LAB_HOME", str(tmp_path))
    monkeypatch.delenv("PAGEWISE_HOME", raising=False)
    monkeypatch.delenv("RAG_LAB_FAKE_LLM", raising=False)
    (tmp_path / "config.json").write_text(json.dumps({"embeddings": "fake", "provider": "ollama"}))
    return tmp_path


@pytest.fixture
def server(home):
    """The server module, reloaded so its paths point at the temp home."""
    from rag_lab import providers, server

    importlib.reload(providers)
    importlib.reload(server)
    return server


@pytest.fixture
def make_client(server):
    def make(token="t0k", allowed_hosts=None, base_url="http://testserver", engine=None):
        if engine is None:
            engine = server.Engine()
            engine.rebuild()
            engine._llm = FakeListChatModel(responses=[ANSWER])
            engine._llm_key = (engine.settings["provider"], engine.settings["model"])
        app = server.create_app(token, engine, allowed_hosts=allowed_hosts)
        c = TestClient(app, base_url=base_url)
        c.headers["X-Token"] = token
        return c

    return make


@pytest.fixture
def client(make_client):
    return make_client()
