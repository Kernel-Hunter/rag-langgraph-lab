"""Pagewise desktop launcher: `rag-lab-app`.

Modes:
  (no flag)   start the local API on a free 127.0.0.1 port and open the UI in
              Chrome/Edge/Brave app mode (looks like a normal window), or in the
              default browser when none of those is installed.
  --browser   same as no flag (kept for older shortcuts).
  --engine    for the Electron shell: start the API, print one handshake line on
              stdout, and exit as soon as stdin closes (parent quit or crashed).
  --serve     API only, for development; prints the URL to open.
  --smoke     prove a packaged build starts, using a throwaway home folder.

Handshake printed by --engine once the server is listening (one line, flushed):

    PAGEWISE_READY {"port": 51234, "token": "..."}

RAG_LAB_PORT fixes the port and RAG_LAB_TOKEN fixes the token in --engine and --serve.
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import webbrowser

TITLE = "Pagewise"
READY_PREFIX = "PAGEWISE_READY"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _port() -> int:
    return int(os.getenv("RAG_LAB_PORT") or _free_port())


def _token() -> str:
    return os.getenv("RAG_LAB_TOKEN") or secrets.token_urlsafe(24)


def allowed_hosts(port: int) -> list[str]:
    return [f"127.0.0.1:{port}", f"localhost:{port}"]


def _make_app(token: str, port: int):
    from rag_lab.server import create_app

    return create_app(token, allowed_hosts=allowed_hosts(port))


def start_server(token: str, port: int | None = None):
    """Starts uvicorn in a daemon thread and returns (app, port, thread) once it listens."""
    import uvicorn

    port = port or _free_port()
    app = _make_app(token, port)
    app.state.engine.rebuild_async()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 20
    while not server.started and thread.is_alive() and time.time() < deadline:
        time.sleep(0.05)
    if not server.started:
        raise RuntimeError(f"local server did not start on 127.0.0.1:{port}")
    return app, port, thread


def _browser_candidates() -> list[str]:
    names = ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "microsoft-edge",
             "msedge", "brave-browser"]
    found = [exe for exe in (shutil.which(n) for n in names) if exe]
    if sys.platform == "darwin":
        for app, binary in (("Google Chrome", "Google Chrome"), ("Microsoft Edge", "Microsoft Edge"),
                            ("Brave Browser", "Brave Browser"), ("Chromium", "Chromium")):
            for root in ("/Applications", os.path.expanduser("~/Applications")):
                path = os.path.join(root, f"{app}.app", "Contents", "MacOS", binary)
                if os.path.exists(path):
                    found.append(path)
    elif sys.platform == "win32":
        for env in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            root = os.environ.get(env)
            if not root:
                continue
            for rel in (r"Google\Chrome\Application\chrome.exe", r"Microsoft\Edge\Application\msedge.exe",
                        r"BraveSoftware\Brave-Browser\Application\brave.exe"):
                path = os.path.join(root, rel)
                if os.path.exists(path):
                    found.append(path)
    return found


def _app_mode_browser(url: str) -> bool:
    """Chrome, Chromium, Edge or Brave in --app mode looks like a normal window. Blocks until it closes."""
    from rag_lab.providers import HOME

    for exe in _browser_candidates():
        try:
            proc = subprocess.Popen([exe, f"--app={url}", f"--user-data-dir={HOME / 'browser-profile'}",
                                     "--window-size=1240,820"])
        except OSError:
            continue
        proc.wait()
        return True
    return False


def _exit_when_stdin_closes():
    """Electron keeps our stdin open. EOF means the parent is gone, so we go too."""
    stdin = sys.stdin
    if stdin is None:  # pythonw / GUI launch without pipes: nothing to watch
        return
    try:
        fd = stdin.fileno()
    except (OSError, ValueError):
        return

    def watch():
        try:
            while os.read(fd, 4096):
                pass
        except OSError:
            pass
        os._exit(0)

    threading.Thread(target=watch, name="stdin-watch", daemon=True).start()


def engine_mode() -> int:
    """`--engine`: child process of the Electron shell."""
    token = _token()
    _, port, thread = start_server(token, _port())
    sys.stdout.write(f"{READY_PREFIX} {json.dumps({'port': port, 'token': token})}\n")
    sys.stdout.flush()
    _exit_when_stdin_closes()
    thread.join()  # the server only stops when the process is killed
    return 0


def smoke_test() -> int:
    """`--smoke`: prove a packaged build starts. Uses a throwaway home folder."""
    import urllib.request

    home = tempfile.mkdtemp(prefix="pagewise-smoke-")
    os.environ["RAG_LAB_HOME"] = home
    os.environ.pop("PAGEWISE_HOME", None)
    with open(os.path.join(home, "config.json"), "w") as f:
        json.dump({"embeddings": "fake"}, f)  # no network in the smoke test

    from rag_lab import providers as prov

    for pid in prov.PROVIDERS:  # every client must construct
        prov.get_llm(pid, "smoke-model")
    prov.get_embeddings("openai")
    token = secrets.token_urlsafe(16)
    app, port, _ = start_server(token)
    app.state.engine.rebuild()
    from rag_lab.providers import LocalEmbeddings  # bundled ONNX runtime must load

    if getattr(sys, "frozen", False):
        LocalEmbeddings().embed_query("smoke")
    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/state", headers={"X-Token": token})
    state = json.load(urllib.request.urlopen(req, timeout=10))
    page = urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=10).read()
    ok = state["index"]["status"] == "ready" and b"<html" in page.lower()
    print("smoke", "ok" if ok else f"failed: {state['index']}")
    return 0 if ok else 1


def serve_only():
    """`--serve`: no window, for development. RAG_LAB_PORT / RAG_LAB_TOKEN fix the address."""
    import uvicorn

    token = _token()
    port = _port()
    app = _make_app(token, port)
    app.state.engine.rebuild_async()
    print(f"Open http://127.0.0.1:{port}/#token={token}", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


def main():
    if "--smoke" in sys.argv:
        sys.exit(smoke_test())
    if "--engine" in sys.argv:
        sys.exit(engine_mode())
    if "--serve" in sys.argv:
        return serve_only()

    token = secrets.token_urlsafe(24)
    _, port, _ = start_server(token)
    url = f"http://127.0.0.1:{port}/#token={token}"

    if not _app_mode_browser(url):
        webbrowser.open(url)
        print(f"{TITLE} is running at {url}\nClose this window or press Ctrl+C to quit.")
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
