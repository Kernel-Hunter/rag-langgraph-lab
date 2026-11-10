"""Desktop app: `rag-lab-app`.

Starts the local API on a free 127.0.0.1 port, then shows the UI in a native
window (WebKit on macOS, WebView2 on Windows). Where no native web view is
available (some Linux setups) it opens the UI in Chrome/Edge app mode or the
default browser instead.
"""

from __future__ import annotations

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

TITLE = "RAG Help Desk"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_server(token: str):
    import uvicorn

    from rag_lab.server import create_app

    app = create_app(token)
    app.state.engine.rebuild_async()
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    deadline = time.time() + 20
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    if not server.started:
        raise RuntimeError("local server did not start")
    return app, port


def _app_mode_browser(url: str) -> bool:
    """Chrome, Chromium or Edge in --app mode looks like a normal window."""
    from rag_lab.providers import HOME

    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "microsoft-edge",
                 "msedge", "brave-browser"):
        exe = shutil.which(name)
        if exe:
            proc = subprocess.Popen([exe, f"--app={url}", f"--user-data-dir={HOME / 'browser-profile'}",
                                     "--window-size=1240,820"])
            proc.wait()
            return True
    return False


def smoke_test() -> int:
    """`--smoke`: prove a packaged build starts. Uses a throwaway home folder."""
    import json
    import urllib.request

    home = tempfile.mkdtemp(prefix="rag-lab-smoke-")
    os.environ["RAG_LAB_HOME"] = home
    with open(os.path.join(home, "config.json"), "w") as f:
        json.dump({"embeddings": "fake"}, f)  # no network in the smoke test
    import webview  # noqa: F401  the window library must be bundled

    from rag_lab import providers as prov

    for pid in prov.PROVIDERS:  # every client must construct
        prov.get_llm(pid, "smoke-model")
    prov.get_embeddings("openai")
    token = secrets.token_urlsafe(16)
    app, port = start_server(token)
    app.state.engine.rebuild()
    from rag_lab.providers import LocalEmbeddings  # bundled ONNX runtime must load

    if getattr(sys, "frozen", False):
        LocalEmbeddings().embed_query("smoke")
    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/state", headers={"X-Token": token})
    state = json.load(urllib.request.urlopen(req, timeout=10))
    page = urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=10).read()
    ok = state["index"]["status"] == "ready" and b"<html" in page.lower()
    if ok and "--window" in sys.argv:  # also open the native window, then close it
        import webview

        loaded = threading.Event()
        win = webview.create_window(TITLE, f"http://127.0.0.1:{port}/#token={token}", width=900, height=600)
        win.events.loaded += loaded.set

        def close_soon():
            loaded.wait(30)
            time.sleep(1)
            win.destroy()

        webview.start(close_soon)
        ok = loaded.is_set()
    print("smoke", "ok" if ok else f"failed: {state['index']}")
    return 0 if ok else 1


def serve_only():
    """`--serve`: no window, for development. RAG_LAB_PORT / RAG_LAB_TOKEN fix the address."""
    import uvicorn

    from rag_lab.server import create_app

    token = os.getenv("RAG_LAB_TOKEN") or secrets.token_urlsafe(24)
    port = int(os.getenv("RAG_LAB_PORT") or _free_port())
    app = create_app(token)
    app.state.engine.rebuild_async()
    print(f"Open http://127.0.0.1:{port}/#token={token}", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


def main():
    if "--smoke" in sys.argv:
        sys.exit(smoke_test())
    if "--serve" in sys.argv:
        return serve_only()

    token = secrets.token_urlsafe(24)
    _, port = start_server(token)
    url = f"http://127.0.0.1:{port}/#token={token}"

    if "--browser" not in sys.argv:
        try:
            import webview

            webview.create_window(TITLE, url, width=1240, height=820, min_size=(860, 560),
                                  background_color="#111113", text_select=True)
            webview.start()
            return
        except Exception as exc:  # no native web view on this system
            print(f"Native window unavailable ({exc}); opening in a browser.", file=sys.stderr)

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
