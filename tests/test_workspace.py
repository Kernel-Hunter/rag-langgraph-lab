"""Workspace tools and the agent loop, offline (fake models)."""

import os
import sys

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

from vesper.coder import Session
from vesper.workspace import Workspace


@pytest.fixture
def ws(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("def hello():\n    return 'hi'\n\nprint(hello())\n")
    (tmp_path / "notes.md").write_text("# Notes\n\nTwo absences are allowed.\n")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "junk.js").write_text("x")
    (tmp_path / "blob.bin").write_bytes(b"\x00\x01\x02")
    return Workspace(tmp_path, approve=lambda a: "yes")


def tools(ws):
    return {t.name: t for t in ws.tools()}


# ---------------------------------------------------------------- paths
def test_paths_are_not_confined_to_the_working_directory(ws, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside")
    (outside / "other.txt").write_text("elsewhere\n")
    assert "elsewhere" in ws.read_file(str(outside / "other.txt"))        # absolute
    assert ws.resolve("~") == ws.resolve(os.path.expanduser("~"))          # home
    assert ws.resolve("src/../notes.md") == ws.root / "notes.md"           # relative to the working directory
    assert "other.txt" in ws.list_files(str(outside))
    assert ws.rel(ws.root / "src" / "app.py") == "src/app.py" and ws.rel(outside) == str(outside)


@pytest.mark.parametrize("name", ["id_rsa", "server.pem", "cert.key", ".netrc", "credentials"])
def test_secret_files_are_never_read(ws, name):
    (ws.root / name).write_text("TOP SECRET")
    out = tools(ws)["read_file"].invoke({"path": name})
    assert out.startswith("Error:") and "TOP SECRET" not in out


def test_change_directory_moves_every_tool(ws, tmp_path_factory):
    other = tmp_path_factory.mktemp("other")
    (other / "there.txt").write_text("x\n")
    moved = []
    ws.on_cwd = moved.append
    out = tools(ws)["change_directory"].invoke({"path": str(other)})
    assert "Working directory is now" in out and "there.txt" in out
    assert ws.root == other.resolve() and moved == [other.resolve()]
    assert "there.txt" in ws.list_files(".")
    assert "exit code 0" in ws.run_command("ls") and "there.txt" in ws.run_command("ls")
    assert tools(ws)["change_directory"].invoke({"path": "nope"}).startswith("Error:")
    assert tools(ws)["change_directory"].invoke({"path": "there.txt"}).startswith("Error:")
    start = ws.root
    ws.run_command("cd ..")  # a bare cd lasts, like in a shell
    assert ws.root == start.parent


def test_dangerous_commands_and_credential_edits_always_ask(tmp_path, monkeypatch):
    asked = []

    def approve(action):
        asked.append(action)
        return "no"

    w = Workspace(tmp_path, approve=approve, mode="yolo")
    for command in ("rm -rf /", "rm -rf ~", "sudo shutdown now", "dd if=/dev/zero of=/dev/disk2"):
        assert tools(w)["run_command"].invoke({"command": command}).startswith("Error:")
    assert len(asked) == 4 and all(a["strict"] for a in asked)
    assert "exit code 0" in w.run_command("echo fine")  # ordinary commands still run freely in yolo
    # A stand-in home folder. Absolute paths only: `~` must never reach the real home in a test.
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    target = tmp_path / ".ssh" / "config"
    target.parent.mkdir()
    assert tools(w)["write_file"].invoke({"path": str(target), "content": "x"}).startswith("Error:")
    assert asked[-1]["strict"] and not target.exists()
    w.approve = lambda a: "always"
    w.mode = "ask"
    w.write_file(str(tmp_path / ".ssh" / "config"), "x")
    assert "edit" not in w.always  # "always" never covers a strict action


def test_auto_approved_actions_are_reported(tmp_path):
    seen = []
    w = Workspace(tmp_path, mode="auto-edit", on_auto=seen.append)
    w.write_file("a.txt", "hello\n")
    assert seen and seen[0]["kind"] == "edit" and "+hello" in seen[0]["diff"]


def test_run_command_streams_output_and_can_be_cancelled(tmp_path):
    import threading
    import time

    lines = []
    w = Workspace(tmp_path, mode="yolo", on_output=lines.append)
    assert "exit code 0" in w.run_command("echo one && echo two")
    assert [line.strip() for line in lines] == ["one", "two"]  # cmd.exe keeps the space before &&
    if os.name != "posix":
        return
    threading.Timer(0.3, w.cancel).start()
    started = time.monotonic()
    out = tools(w)["run_command"].invoke({"command": "sleep 30", "timeout": 60})
    assert time.monotonic() - started < 10 and "stopped" in out
    w.stopped = False
    assert "did not finish within 1 s" in w.run_command("sleep 5", timeout=1)


def test_glob_takes_a_folder_and_grep_has_a_budget(ws, tmp_path_factory, monkeypatch):
    other = tmp_path_factory.mktemp("lib")
    (other / "deep").mkdir()
    (other / "deep" / "mod.py").write_text("needle = 1\n")
    assert "deep/mod.py" in ws.glob("**/*.py", str(other)).replace("\\", "/")
    monkeypatch.setattr("vesper.workspace.MAX_SCAN_FILES", 1)
    for i in range(5):
        (other / f"f{i}.txt").write_text("nothing\n")
    assert "stopped after" in ws.grep("zzz", str(other))


# ---------------------------------------------------------------- read side
def test_list_files_skips_ignored(ws):
    out = ws.list_files(".")
    assert "src/" in out and "app.py" in out and "notes.md" in out
    assert "node_modules" not in out


def test_read_file_numbers_lines(ws):
    out = ws.read_file("src/app.py", offset=2, limit=1)
    assert out.startswith("2\t    return 'hi'") and "offset=3" in out
    assert "binary" in ws.read_file("blob.bin")
    assert tools(ws)["read_file"].invoke({"path": "missing.txt"}).startswith("Error:")


def test_read_pdf(ws):
    pypdf = pytest.importorskip("pypdf")
    w = pypdf.PdfWriter()
    w.add_blank_page(width=100, height=100)
    with open(ws.root / "doc.pdf", "wb") as f:
        w.write(f)
    assert ws.read_file("doc.pdf").startswith("[page 1]")


def test_glob_and_grep(ws):
    assert ws.glob("**/*.py") == "src/app.py"
    assert "node_modules" not in ws.glob("*.js")
    assert ws.grep(r"def \w+", file_glob="*.py") == "src/app.py:1: def hello():"
    assert tools(ws)["grep"].invoke({"pattern": "("}).startswith("Error:")


# ---------------------------------------------------------------- write side + approvals
def test_edit_file_asks_and_writes(ws):
    seen = []
    ws.approve = lambda action: seen.append(action) or "yes"
    assert ws.edit_file("src/app.py", "return 'hi'", "return 'hello'").startswith("Edited")
    assert "return 'hello'" in (ws.root / "src" / "app.py").read_text()
    assert seen[0]["kind"] == "edit" and "-    return 'hi'" in seen[0]["diff"]


def test_edit_requires_unique_match(ws):
    (ws.root / "dup.txt").write_text("a\na\n")
    edit = tools(ws)["edit_file"]
    assert "appears 2 times" in edit.invoke({"path": "dup.txt", "old_string": "a", "new_string": "b"})
    assert "not found" in edit.invoke({"path": "dup.txt", "old_string": "zzz", "new_string": "b"})
    assert edit.invoke({"path": "dup.txt", "old_string": "a", "new_string": "b", "replace_all": True}).startswith("Edited")
    assert (ws.root / "dup.txt").read_text() == "b\nb\n"


def test_declined_actions_change_nothing(ws):
    ws.approve = lambda action: "no"
    t = tools(ws)
    assert "declined" in t["write_file"].invoke({"path": "new.txt", "content": "x"})
    assert not (ws.root / "new.txt").exists()
    assert "declined" in t["edit_file"].invoke({"path": "src/app.py", "old_string": "hi", "new_string": "yo"})
    assert "'hi'" in (ws.root / "src" / "app.py").read_text()
    assert "declined" in t["run_command"].invoke({"command": "echo pwned > pwned.txt"})
    assert not (ws.root / "pwned.txt").exists()


def test_always_and_modes(ws):
    calls = []
    ws.approve = lambda action: calls.append(action) or "always"
    ws.write_file("a.txt", "1")
    ws.write_file("b.txt", "2")
    assert len(calls) == 1  # "always" covers later edits in this session
    ws.always.clear()
    ws.mode = "auto-edit"
    ws.approve = lambda action: "no"
    assert ws.write_file("c.txt", "3").startswith("Created")
    assert "declined" in tools(ws)["run_command"].invoke({"command": "echo hi"})


def test_run_command_in_root(ws):
    out = ws.run_command(f'"{sys.executable}" -c "import os; print(os.getcwd())"')
    assert out.startswith("exit code 0") and str(ws.root) in out


def test_write_creates_parents_atomically(ws):
    assert ws.write_file("deep/dir/new.txt", "hello").startswith("Created")
    assert (ws.root / "deep" / "dir" / "new.txt").read_text() == "hello"
    assert not list(ws.root.rglob("*.vesper-tmp"))


# ---------------------------------------------------------------- agent loop
class ToolFake(GenericFakeChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


def test_session_runs_tools_and_streams(ws):
    llm = ToolFake(disable_streaming=True, messages=iter([
        AIMessage(content="", tool_calls=[{"name": "read_file", "args": {"path": "notes.md"}, "id": "c1"}]),
        AIMessage(content="", tool_calls=[{"name": "edit_file", "id": "c2", "args": {
            "path": "notes.md", "old_string": "Two", "new_string": "Three"}}]),
        AIMessage(content="Done: notes.md now says three."),
        AIMessage(content="Second answer."),
    ]))
    session = Session(llm, ws)
    events = list(session.ask("Change two to three in notes.md"))
    kinds = [e for e, _ in events]
    assert kinds.count("tool_start") == 2 and kinds.count("tool_end") == 2 and kinds[-1] == "done"
    ends = [d for e, d in events if e == "tool_end"]
    assert ends[0]["ok"] and "Two absences" in ends[0]["output"]
    assert ends[1]["ok"] and ends[1]["output"].startswith("Edited")
    assert "Done" in "".join(d["text"] for e, d in events if e == "token")
    assert "Three absences" in (ws.root / "notes.md").read_text()
    n = len(session.history)
    list(session.ask("thanks"))
    assert len(session.history) > n  # history carries over
    session.clear()
    assert session.history == []


# ---------------------------------------------------------------- desktop engine: folder mode
def _folder_engine(server, tmp_path, monkeypatch, replies):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "a.txt").write_text("old\n")
    engine = server.Engine()
    engine.update({"workspace": str(proj)})
    llm = ToolFake(disable_streaming=True, messages=iter(replies))
    monkeypatch.setattr(engine, "llm", lambda: llm)
    return engine, proj


def test_engine_folder_mode_approval_roundtrip(server, tmp_path, monkeypatch):
    import threading

    engine, proj = _folder_engine(server, tmp_path, monkeypatch, [
        AIMessage(content="", tool_calls=[{"name": "edit_file", "id": "e1", "args": {
            "path": "a.txt", "old_string": "old", "new_string": "new"}}]),
        AIMessage(content="Changed it."),
    ])
    gen = engine.stream_answer("change it", "chat1", [])
    seen = []
    for event, data in gen:
        seen.append(event)
        if event == "approval":
            assert data["path"] == "a.txt" and "+new" in data["diff"]
            threading.Timer(0.05, engine.answer_approval, (data["id"], "yes")).start()
    assert "tool_start" in seen and "tool_end" in seen and seen[-1] == "done"
    assert (proj / "a.txt").read_text() == "new\n"
    assert engine.answer_approval("nope", "yes") is False


def test_engine_folder_mode_refuses_when_client_leaves(server, tmp_path, monkeypatch):
    engine, proj = _folder_engine(server, tmp_path, monkeypatch, [
        AIMessage(content="", tool_calls=[{"name": "run_command", "id": "r1", "args": {"command": "echo hi > x.txt"}}]),
        AIMessage(content="ok"),
    ])
    gen = engine.stream_answer("run it", "chat2", [])
    for event, _ in gen:
        if event == "approval":
            gen.close()  # the window closed while the approval card was showing
            break
    import time

    time.sleep(0.3)
    assert not (proj / "x.txt").exists()


def test_engine_without_a_folder_starts_in_home(server, monkeypatch):
    from langchain_core.messages import AIMessage

    engine = server.Engine()
    engine.update({"workspace": ""})
    monkeypatch.setattr(engine, "llm", lambda: ToolFake(messages=iter([AIMessage("hi")])))
    events = list(engine.stream_answer("hi", "c", []))
    assert events[0] == ("session", {"id": events[0][1]["id"], "cwd": str(server.Path.home().resolve())})
    assert ("token", {"text": "hi"}) in events


@pytest.mark.parametrize("value", ["relative/path", "/definitely/not/here", 5])
def test_workspace_setting_validated(client, value):
    assert client.post("/api/settings", json={"workspace": value}).status_code == 400


def test_approve_endpoint_validates(client):
    assert client.post("/api/approve", json={"id": "x", "answer": "maybe"}).status_code == 400
    assert client.post("/api/approve", json={"id": "x", "answer": "yes"}).status_code == 404
    assert client.post("/api/chat", json={"question": "hi", "chat_id": "../../x"}).status_code == 400


def test_print_mode_with_fresh_home_and_piped_input(tmp_path, monkeypatch, capsys):
    """`git diff | vesper "..."` on a machine where vesper has never run (fake model)."""
    import io

    from vesper import tui

    monkeypatch.setenv("VESPER_FAKE_LLM", "1")
    monkeypatch.setattr(tui, "load_config", lambda: {})
    monkeypatch.setattr("sys.stdin", io.StringIO("diff --git a/x b/x\n"))
    (tmp_path / "x").write_text("x\n")
    assert tui.main(["-C", str(tmp_path), "--mode", "ask", "describe this"]) == 0
    out = capsys.readouterr()
    assert "Let me look at the folder first." in out.out and "List" in out.err
    assert not (tmp_path / "vesper-notes.md").exists()  # ask mode with nobody to answer: refused


def test_print_mode_attaches_mentioned_files(tmp_path, monkeypatch, capsys):
    """`vesper "explain @notes.md"` sends the file with the question, like a session does."""
    import io

    from vesper import tui
    from vesper.store import Store

    monkeypatch.setenv("VESPER_FAKE_LLM", "1")
    monkeypatch.setattr(tui, "load_config", lambda: {})
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    (tmp_path / "notes.md").write_text("the secret word is plum\n")
    assert tui.main(["-C", str(tmp_path), "--mode", "ask", "explain @notes.md"]) == 0
    assert "attached" in capsys.readouterr().err
    store = Store()
    sid = store.list_sessions()[0]["id"]
    first = store.load_messages(sid)[0]
    assert "the secret word is plum" in str(first.content)
    assert store.list_sessions()[0]["title"].startswith("explain @notes.md")


def test_stopped_workspace_refuses_tools_until_the_next_turn(ws):
    ws.cancel()
    assert "stopped" in tools(ws)["read_file"].invoke({"path": "notes.md"})
    assert "stopped" in tools(ws)["run_command"].invoke({"command": "echo hi"})
    llm = ToolFake(disable_streaming=True, messages=iter([AIMessage(content="hello")]))
    list(Session(llm, ws).ask("hi"))  # a new turn clears it
    assert "Two absences" in tools(ws)["read_file"].invoke({"path": "notes.md"})
