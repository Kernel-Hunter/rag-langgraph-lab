"""Local API behind the desktop app, with fake models and a throwaway home folder."""

import importlib
import json

import pytest
from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import FakeListChatModel


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("RAG_LAB_HOME", str(tmp_path))
    (tmp_path / "config.json").write_text(json.dumps({"embeddings": "fake", "provider": "ollama"}))
    from rag_lab import providers, server

    importlib.reload(providers)
    importlib.reload(server)
    engine = server.Engine()
    engine.rebuild()
    engine._llm = FakeListChatModel(responses=["Two absences are allowed [page 3]."])
    engine._llm_key = (engine.settings["provider"], engine.settings["model"])
    app = server.create_app("t0k", engine)
    c = TestClient(app)
    c.headers["X-Token"] = "t0k"
    return c


def _events(text):
    out = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        out.append((lines["event"], json.loads(lines["data"])))
    return out


def test_token_required(client):
    assert client.get("/api/state", headers={"X-Token": "wrong"}).status_code == 401


def test_state_lists_many_providers_and_sample_doc(client):
    state = client.get("/api/state").json()
    ids = {p["id"] for p in state["providers"]}
    assert {"openai", "anthropic", "google", "deepseek", "groq", "openrouter", "ollama", "custom"} <= ids
    assert state["index"]["status"] == "ready"
    assert state["docs"][0]["name"] == "sample-handbook.md"


def test_chat_streams_sources_then_tokens(client):
    res = client.post("/api/chat", json={"question": "How many absences?"})
    events = _events(res.text)
    names = [e for e, _ in events]
    assert names[0] == "sources" and names[-1] == "done"
    answer = "".join(d["text"] for e, d in events if e == "token")
    assert answer == "Two absences are allowed [page 3]."


def test_upload_and_remove_document(client):
    r = client.post("/api/docs?name=notes.txt", content=b"The lab is in room C3.")
    assert any(d["name"] == "notes.txt" for d in r.json()["docs"])
    assert client.post("/api/docs?name=evil.exe", content=b"x").status_code == 400
    r = client.delete("/api/docs/notes.txt")
    assert all(d["name"] != "notes.txt" for d in r.json()["docs"])


def test_keys_saved_privately(client, tmp_path):
    client.post("/api/settings", json={"keys": {"groq": "gsk_test"}})
    env = (tmp_path / ".env").read_text()
    assert "GROQ_API_KEY=gsk_test" in env
    state = client.get("/api/state").json()
    assert next(p for p in state["providers"] if p["id"] == "groq")["has_key"] is True
    assert "gsk_test" not in json.dumps(state)  # keys never go back to the page
