"""Local API behind the desktop app, with fake models and a throwaway home folder."""

import json


def _events(text):
    out = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        out.append((lines["event"], json.loads(lines["data"])))
    return out


def test_token_required(client):
    assert client.get("/api/state", headers={"X-Token": "wrong"}).status_code == 401


def test_state_lists_many_providers(client):
    state = client.get("/api/state").json()
    ids = {p["id"] for p in state["providers"]}
    assert {"openai", "anthropic", "google", "deepseek", "groq", "openrouter", "ollama", "custom"} <= ids
    assert set(state) == {"providers", "custom_base_url", "settings", "version", "home"}


def test_keys_saved_privately(client, tmp_path):
    client.post("/api/settings", json={"keys": {"groq": "gsk_test"}})
    env = (tmp_path / ".env").read_text()
    assert "GROQ_API_KEY=gsk_test" in env
    state = client.get("/api/state").json()
    assert next(p for p in state["providers"] if p["id"] == "groq")["has_key"] is True
    assert "gsk_test" not in json.dumps(state)  # keys never go back to the page
