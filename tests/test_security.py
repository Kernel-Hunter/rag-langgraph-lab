"""Hardening of the local API: host/origin checks, headers, upload and input limits."""

import json
import os
import stat
import sys

import pytest

CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
       "font-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")


# ----- token, host, origin -----

def test_token_missing_or_wrong(client):
    assert client.get("/api/state", headers={"X-Token": ""}).status_code == 401
    assert client.get("/api/state", headers={"X-Token": "t0k-but-longer"}).status_code == 401
    assert client.get("/api/state").status_code == 200


def test_token_not_accepted_in_query(client):
    res = client.get("/api/state?token=t0k", headers={"X-Token": ""})
    assert res.status_code == 401


def test_host_check(make_client):
    hosts = ["127.0.0.1:8123", "localhost:8123"]
    c = make_client(allowed_hosts=hosts, base_url="http://127.0.0.1:8123")
    assert c.get("/api/state").status_code == 200
    assert c.get("/").status_code == 200
    assert c.get("/api/state", headers={"Host": "localhost:8123"}).status_code == 200
    for bad in ("evil.example:8123", "127.0.0.1:9999", "127.0.0.1", "attacker.localhost:8123"):
        res = c.get("/api/state", headers={"Host": bad})
        assert res.status_code == 403, bad
        assert c.get("/", headers={"Host": bad}).status_code == 403


def test_host_check_skipped_when_not_configured(client):
    assert client.get("/api/state", headers={"Host": "anything:1"}).status_code == 200


def test_origin_check_on_writes(make_client):
    c = make_client(allowed_hosts=["127.0.0.1:8123", "localhost:8123"], base_url="http://127.0.0.1:8123")
    body = {"thinking": "low"}
    assert c.post("/api/settings", json=body, headers={"Origin": "http://evil.example"}).status_code == 403
    assert c.post("/api/settings", json=body, headers={"Origin": "null"}).status_code == 403
    assert c.post("/api/settings", json=body, headers={"Origin": "http://127.0.0.1:9999"}).status_code == 403
    assert c.delete("/api/sessions/abcd", headers={"Origin": "http://evil.example"}).status_code == 403
    assert c.options("/api/chat", headers={"Origin": "http://evil.example"}).status_code == 403
    assert c.post("/api/settings", json=body, headers={"Origin": "http://127.0.0.1:8123"}).status_code == 200
    assert c.post("/api/settings", json=body, headers={"Origin": "http://localhost:8123"}).status_code == 200
    assert c.post("/api/settings", json=body).status_code == 200  # no Origin: not a browser
    # GET is not origin-checked (the token still protects it)
    assert c.get("/api/state", headers={"Origin": "http://evil.example"}).status_code == 200


def test_origin_check_without_host_list_uses_request_host(client):
    body = {"thinking": "off"}
    assert client.post("/api/settings", json=body, headers={"Origin": "http://evil.example"}).status_code == 403
    assert client.post("/api/settings", json=body, headers={"Origin": "http://testserver"}).status_code == 200


@pytest.mark.parametrize("path,headers,code", [
    ("/", {}, 200),
    ("/api/state", {}, 200),
    ("/api/state", {"X-Token": "nope"}, 401),
    ("/nope", {}, 404),
    ("/static/index.html", {}, 200),
])
def test_security_headers_everywhere(client, path, headers, code):
    res = client.get(path, headers=headers)
    assert res.status_code == code
    assert res.headers["content-security-policy"] == CSP
    assert res.headers["x-content-type-options"] == "nosniff"
    assert res.headers["referrer-policy"] == "no-referrer"
    assert res.headers["x-frame-options"] == "DENY"
    assert res.headers["cross-origin-opener-policy"] == "same-origin"


def test_security_headers_on_rejections_and_streams(make_client):
    c = make_client(allowed_hosts=["127.0.0.1:1"], base_url="http://127.0.0.1:1")
    res = c.get("/", headers={"Host": "evil:1"})
    assert res.status_code == 403 and res.headers["x-frame-options"] == "DENY"
    res = c.post("/api/chat", json={"question": "How many absences?"})
    assert res.status_code == 200 and res.headers["content-security-policy"] == CSP


# ----- chat input -----

def test_question_length_limit(client, server):
    assert client.post("/api/chat", json={"question": "q" * 100_001}).status_code == 400
    assert client.post("/api/chat", json={"question": "q" * 100_000}).status_code == 200
    assert server.MAX_QUESTION_CHARS == 100_000


@pytest.mark.parametrize("body", [{"question": ""}, {"question": "  "}, {"question": 5}, {"question": ["a"]}, [1], {}])
def test_question_must_be_text(client, body):
    assert client.post("/api/chat", json=body).status_code == 400


def test_bad_json_is_400(client):
    res = client.post("/api/chat", content=b"{nope", headers={"Content-Type": "application/json"})
    assert res.status_code == 400
    res = client.post("/api/settings", content=b"{nope", headers={"Content-Type": "application/json"})
    assert res.status_code == 400


# ----- settings validation -----

@pytest.mark.parametrize("body", [
    {"provider": "nope"},
    {"provider": 3},
    {"model": "m" * 201},
    {"model": 7},
    {"model": "a\nb"},
    {"agent_mode": "chaos"},
    {"agent_mode": 1},
    {"thinking": "max"},
    {"workspace": "relative/path"},
    {"workspace": "/no/such/folder/anywhere"},
    {"workspace": 5},
    {"custom_base_url": "ftp://x"},
    {"custom_base_url": "javascript:alert(1)"},
    {"custom_base_url": "localhost:1234"},
    {"custom_base_url": "http://x\nEVIL=1"},
    {"custom_base_url": "http://user:pass@llm.example/v1"},
    {"custom_base_url": "http://169.254.169.254/latest"},
    {"keys": {"groq": "k" * 501}},
    {"keys": {"groq": "abc\nOPENAI_API_KEY=stolen"}},
    {"keys": {"groq": "abc\rX=1"}},
    {"keys": {"groq": 12}},
    {"keys": ["groq"]},
    [1, 2],
])
def test_settings_rejects_bad_values(client, body):
    before = client.get("/api/state").json()["settings"]
    res = client.post("/api/settings", json=body)
    assert res.status_code == 400, body
    assert isinstance(res.json()["detail"], str) and res.json()["detail"]
    assert client.get("/api/state").json()["settings"] == before  # nothing half-applied


def test_settings_accepts_good_values(client, server, tmp_path):
    body = {"provider": "groq", "model": "llama-3.1-8b-instant", "agent_mode": "plan", "thinking": "high",
            "workspace": str(tmp_path), "custom_base_url": "https://llm.example/v1", "unknown": "ignored",
            "keys": {"nope": "x"}}
    res = client.post("/api/settings", json=body)
    assert res.status_code == 200
    s = res.json()["settings"]
    assert (s["provider"], s["agent_mode"], s["thinking"], s["workspace"]) == \
        ("groq", "plan", "high", str(tmp_path.resolve()))
    assert "unknown" not in s
    saved = json.loads((server.prov.HOME / "config.json").read_text())
    assert saved["agent_mode"] == "plan" and "unknown" not in saved
    assert client.post("/api/settings", json={"custom_base_url": ""}).status_code == 200


def test_bad_saved_config_falls_back_to_defaults(home, server):
    (home / "config.json").write_text(json.dumps(
        {"thinking": "max", "agent_mode": "plan", "provider": "nope", "workspace": "/no/such/folder"}))
    engine = server.Engine()
    assert engine.settings["thinking"] == server.DEFAULTS["thinking"]
    assert engine.settings["provider"] == server.DEFAULTS["provider"]
    assert engine.settings["workspace"] == ""
    assert engine.settings["agent_mode"] == "plan"
    (home / "config.json").write_text("{broken")
    assert server.Engine().settings == server.DEFAULTS


# ----- keys and the .env file -----

def test_env_newline_injection_rejected(client, server):
    env = server.prov.HOME / ".env"
    client.post("/api/settings", json={"keys": {"groq": "gsk_ok"}})
    before = env.read_text()
    res = client.post("/api/settings", json={"keys": {"groq": "x\nOPENAI_BASE_URL=http://evil"}})
    assert res.status_code == 400
    assert env.read_text() == before
    with pytest.raises(ValueError):
        server.prov.save_user_settings({"GROQ_API_KEY": "a\rB=1"})
    with pytest.raises(ValueError):
        server.prov.save_user_settings({"BAD\nKEY": "a"})
    assert env.read_text() == before


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes")
def test_env_file_private_and_atomic(server):
    prov = server.prov
    prov.HOME.chmod(0o755)
    path = prov.save_user_settings({"GROQ_API_KEY": "a"})
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(prov.HOME.stat().st_mode) == 0o700
    prov.save_user_settings({"MISTRAL_API_KEY": "b"})
    prov.save_user_settings({"GROQ_API_KEY": ""})
    assert path.read_text() == "MISTRAL_API_KEY=b\n"
    assert "GROQ_API_KEY" not in os.environ
    assert not list(prov.HOME.glob(".env.*.tmp"))  # no temp files left behind


def test_keys_never_in_state(client):
    secrets = {"groq": "gsk_SECRET1", "custom": "cus_SECRET2", "openai": "sk-SECRET3", "anthropic": "sk-ant-SECRET4"}
    res = client.post("/api/settings", json={"keys": secrets, "custom_base_url": "http://127.0.0.1:9/v1"})
    assert res.status_code == 200
    for state in (res.json(), client.get("/api/state").json()):
        dumped = json.dumps(state)
        for value in secrets.values():
            assert value not in dumped
        has = {p["id"]: p["has_key"] for p in state["providers"]}
        assert all(has[pid] for pid in secrets)


def test_json_body_size_limit(client):
    big = {"question": "x", "images": [{"data": "x" * (49 * 1024 * 1024)}]}
    assert client.post("/api/chat", json=big).status_code == 413
    assert client.post("/api/settings", json={"model": "m" * (300 * 1024)}).status_code == 413
