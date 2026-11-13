"""Engine: error labels, model lists, fake mode, the home folder and keys shared between apps."""

import importlib
import io
import json
import os

import pytest


def test_friendly_error_names_the_provider(server):
    msg = server.friendly_error(Exception("Error code: 401 - invalid x-api-key"), "anthropic")
    assert msg.startswith("Anthropic Claude rejected the API key")
    assert "Cannot reach Ollama" in server.friendly_error(ConnectionError("connection refused"), "ollama")
    assert server.friendly_error(ValueError("odd"), "nobody").startswith("ValueError: odd")


def test_keys_saved_by_another_app_are_picked_up(home, server, monkeypatch):
    """The terminal and the desktop app share HOME/.env: a key saved by one is seen by the other."""
    prov = server.prov
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    groq = prov.PROVIDERS["groq"]
    assert prov._key(groq) is None
    env = home / ".env"
    env.write_text("GROQ_API_KEY=from-terminal\n")
    os.utime(env, ns=(1, 1_000_000_000))
    assert prov._key(groq) == "from-terminal"
    assert {p["id"]: p["has_key"] for p in server.Engine().state()["providers"]}["groq"]
    env.write_text("")  # removed in the other app: gone here too
    os.utime(env, ns=(2, 2_000_000_000))
    assert prov._key(groq) is None


def test_a_shell_key_wins_over_the_file(home, server, monkeypatch):
    monkeypatch.setenv("MISTRAL_API_KEY", "from-shell")
    (home / ".env").write_text("MISTRAL_API_KEY=from-file\n")
    prov = importlib.reload(server.prov)
    assert prov._key(prov.PROVIDERS["mistral"]) == "from-shell"


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


def test_fake_llm_off_by_default(server, monkeypatch):
    monkeypatch.delenv("VESPER_FAKE_LLM", raising=False)
    from langchain_ollama import ChatOllama
    from langchain_openai import ChatOpenAI

    llm = server.prov.get_llm("ollama")
    # The native client, with a context window large enough for an agent (the default is 4096).
    assert isinstance(llm, ChatOllama) and llm.num_ctx >= 8192 and not llm.base_url.endswith("/v1")
    monkeypatch.setenv("VESPER_OLLAMA_CTX", "32768")
    assert server.prov.get_llm("ollama").num_ctx == 32768 and server.prov.context_tokens("ollama") == 32768
    monkeypatch.setenv("GROQ_API_KEY", "x")
    assert isinstance(server.prov.get_llm("groq"), ChatOpenAI)


# ----- home folder -----

@pytest.fixture
def no_home_env(monkeypatch):
    monkeypatch.delenv("VESPER_HOME", raising=False)
    from vesper import providers

    return providers


def test_home_defaults_to_vesper(tmp_path, no_home_env):
    assert no_home_env.resolve_home(tmp_path) == tmp_path / ".vesper"
    assert not (tmp_path / ".vesper").exists()  # created later, by the engine


def test_home_env_overrides(tmp_path, monkeypatch, no_home_env):
    monkeypatch.setenv("VESPER_HOME", str(tmp_path / "p"))
    assert no_home_env.resolve_home(tmp_path) == tmp_path / "p"


def test_module_home_follows_env(tmp_path, monkeypatch):
    monkeypatch.setenv("VESPER_HOME", str(tmp_path))
    from vesper import providers

    try:
        assert importlib.reload(providers).HOME == tmp_path
    finally:
        monkeypatch.undo()
        importlib.reload(providers)
