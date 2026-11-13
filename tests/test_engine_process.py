"""`python -m vesper.desktop --engine`: the handshake contract the Electron shell relies on."""

import json
import os
import re
import subprocess
import sys
import threading
import urllib.error
import urllib.request

READY = re.compile(r'^VESPER_READY \{"port": (\d+), "token": "([^"]+)"\}$')


def _launch(tmp_path, **env):
    full = {**os.environ, "VESPER_HOME": str(tmp_path), "VESPER_FAKE_LLM": "1", "PYTHONUNBUFFERED": "1", **env}
    (tmp_path / "config.json").write_text(json.dumps({"provider": "groq"}))
    return subprocess.Popen([sys.executable, "-m", "vesper.desktop", "--engine"], env=full,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def _read_line(proc, timeout=30):
    box = []
    t = threading.Thread(target=lambda: box.append(proc.stdout.readline()), daemon=True)
    t.start()
    t.join(timeout)
    assert box, "no READY line in time"
    return box[0].rstrip("\n")


def _get(port, path, token=None, host=None):
    headers = {"X-Token": token} if token else {}
    if host:
        headers["Host"] = host
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as res:
            return res.status, res.read()
    except urllib.error.HTTPError as err:
        return err.code, err.read()


def test_engine_handshake_and_exit_on_stdin_eof(tmp_path):
    proc = _launch(tmp_path)
    try:
        line = _read_line(proc)
        m = READY.match(line)
        assert m, line
        payload = json.loads(line.removeprefix("VESPER_READY "))
        assert set(payload) == {"port", "token"} and isinstance(payload["port"], int)
        port, token = int(m.group(1)), m.group(2)

        status, body = _get(port, "/api/state", token)
        assert status == 200
        assert json.loads(body)["settings"]["provider"] == "groq"
        assert _get(port, "/api/state", "wrong")[0] == 401
        assert _get(port, "/api/state", token, host=f"evil.example:{port}")[0] == 403  # real hosts are enforced
        assert _get(port, "/api/state", token, host=f"localhost:{port}")[0] == 200

        proc.stdin.close()  # parent went away
        assert proc.wait(timeout=10) == 0
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_engine_uses_env_token_and_port(tmp_path):
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    proc = _launch(tmp_path, VESPER_TOKEN="fixed-token", VESPER_PORT=str(port))
    try:
        line = _read_line(proc)
        assert line == f'VESPER_READY {{"port": {port}, "token": "fixed-token"}}'
        assert _get(port, "/api/state", "fixed-token")[0] == 200
        proc.stdin.close()
        assert proc.wait(timeout=10) == 0
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
