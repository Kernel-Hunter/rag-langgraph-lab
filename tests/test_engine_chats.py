"""Desktop engine: attachments, stop, rename, retry, delete, and settings shared with the terminal."""

import base64
import json
import os

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


class Recorder(GenericFakeChatModel):
    """Answers from a script and remembers what it was sent."""

    seen: list = []

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.seen.append(list(messages))
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


@pytest.fixture
def engine(server, tmp_path, monkeypatch):
    proj = tmp_path / "proj"
    (proj / "pkg").mkdir(parents=True)
    (proj / "notes.md").write_text("The secret word is marmalade.\n")
    (proj / "pkg" / "a.py").write_text("x = 1\n")
    (proj / "shot.png").write_bytes(PNG)
    e = server.Engine()
    e.update({"workspace": str(proj), "agent_mode": "ask"})
    llm = Recorder(messages=iter([AIMessage(f"answer {i}") for i in range(10)]), seen=[], disable_streaming=True)
    monkeypatch.setattr(e, "llm", lambda: llm)
    e.test_llm = llm
    return e


def test_files_folders_and_images_reach_the_model_once(engine):
    pasted = {"name": "clip.png", "mime": "image/png", "data": base64.b64encode(PNG).decode()}
    events = list(engine.stream_answer("look", "c1", [], attachments=["notes.md", "pkg", "shot.png", "nope.txt"],
                                       images=[pasted, {"name": "bad", "mime": "text/html", "data": "eA=="}]))
    attached = next(d for e, d in events if e == "attached")
    assert attached["names"] == ["notes.md", "pkg", "shot.png", "clip.png"]
    assert [p.split(":")[0] for p in attached["problems"]] == ["nope.txt", "bad"]
    sent = engine.test_llm.seen[0][-1]
    assert isinstance(sent, HumanMessage)
    text = sent.content[0]["text"]
    assert text.startswith("look") and "marmalade" in text and '<folder path="pkg">' in text and "a.py" in text
    assert [b["type"] for b in sent.content[1:]] == ["image", "image"]
    # Later turns do not carry the pictures again.
    list(engine.stream_answer("again", "c1", []))
    history = engine.test_llm.seen[1]
    assert not any(isinstance(m.content, list) and any(b.get("type") == "image" for b in m.content) for m in history)
    assert "[image shot.png was shown to you in this message]" in str(history[1].content)
    # The saved transcript shows what was attached, not the file contents.
    sid = next(d for e, d in events if e == "session")["id"]
    first = engine.saved_session(sid)["turns"][0]
    assert first == {"role": "user", "text": "look", "attachments": ["notes.md", "pkg", "shot.png", "clip.png"]}


def test_retry_forgets_the_last_answer(engine):
    list(engine.stream_answer("first", "c2", []))
    list(engine.stream_answer("second", "c2", []))
    list(engine.stream_answer("second", "c2", [], retry=True))
    asked = [m.content for m in engine.test_llm.seen[-1] if isinstance(m, HumanMessage)]
    assert asked == ["first", "second"]


def test_rename_and_delete_reach_the_database(engine, client, server):
    events = list(engine.stream_answer("hello", "c3", []))
    sid = next(d for e, d in events if e == "session")["id"]
    app_client = client.__class__(server.create_app("t", engine))
    app_client.headers["X-Token"] = "t"
    assert app_client.post(f"/api/sessions/{sid}/rename", json={"title": "  My   chat "}).status_code == 200
    assert app_client.get(f"/api/sessions/{sid}").json()["title"] == "My chat"
    assert app_client.delete(f"/api/sessions/{sid}").status_code == 200
    assert app_client.get(f"/api/sessions/{sid}").status_code == 404
    assert app_client.delete(f"/api/sessions/{sid}").status_code == 404          # not silently ok
    assert app_client.delete("/api/sessions/not-an-id").status_code == 400
    assert app_client.post("/api/chats/c3/stop").json() == {"stopped": False}   # nothing running


def test_stop_ends_a_running_command(engine, server, monkeypatch):
    from vesper.workspace import Workspace

    started = []
    real_run = Workspace.run_command

    def slow(self, command, *a, **k):
        started.append(command)
        engine.stop_chat("c4")
        return real_run(self, command, *a, **k)

    monkeypatch.setattr(Workspace, "run_command", slow)
    engine.update({"agent_mode": "yolo"})
    engine.test_llm.messages = iter([
        AIMessage("", tool_calls=[{"name": "run_command", "args": {"command": "sleep 20"}, "id": "r1"}]),
        AIMessage("never"),
    ])
    import time

    t = time.monotonic()
    events = list(engine.stream_answer("run it", "c4", []))
    assert started, events
    assert time.monotonic() - t < 5 and events[-1] == ("done", {})
    assert not any(e == "token" and "never" in d["text"] for e, d in events)
    assert engine.stop_chat("c4") is False  # nothing left running


def test_settings_saved_by_the_terminal_are_seen(engine, server):
    cfg = server.CONFIG
    data = json.loads(cfg.read_text())
    data.update(provider="groq", model="llama-x", thinking="high")
    cfg.write_text(json.dumps(data))
    os.utime(cfg, ns=(1, 1))
    engine.sync_settings()
    assert (engine.settings["provider"], engine.settings["model"], engine.settings["thinking"]) == ("groq", "llama-x", "high")
    # A desktop change writes only what changed: the terminal's values stay.
    engine.update({"agent_mode": "plan"})
    saved = json.loads(cfg.read_text())
    assert (saved["provider"], saved["model"], saved["agent_mode"]) == ("groq", "llama-x", "plan")
