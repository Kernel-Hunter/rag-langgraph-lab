"""The coding agent: a LangGraph `create_agent` loop over the tools in `workspace.py`.

`Session` is one conversation. It keeps the history, saves it to the SQLite store, logs
what happens, and turns each agent step into simple events that both front ends draw: the
terminal UI (`tui.py`) and the desktop app (`server.py`).

Events from `Session.ask()`:
    ("token", {"text"})                              streamed answer text
    ("tool_start", {"id", "name", "args"})           the model called a tool
    ("tool_end", {"id", "name", "output", "ok", "ms"})
    ("sources", {"query", "items"})                  document passages found by search_docs
    ("cwd", {"path"})                                the working directory changed
    ("notice", {"text"})                             something the user should know (context trimmed)
    ("usage", {"input", "output", "context", "seconds", "tools"})   once, at the end of the turn
    ("done", {})
"""

from __future__ import annotations

import time
from collections.abc import Iterator

from langchain.agents import create_agent
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    ToolMessage,
)

from vesper.prompts import agent_system_prompt
from vesper.store import Store, new_id
from vesper.workspace import Workspace

RECURSION_LIMIT = 120            # model and tool steps per question
DEFAULT_CONTEXT_TOKENS = 120_000  # assumed context window when the provider does not say
HISTORY_SHARE = 0.6              # part of the window the history may fill; the rest is prompt, tools and the answer
CHARS_PER_TOKEN = 3.5            # rough, on the safe side for code
OLD_OUTPUT_CHARS = 1_200         # tool output from earlier turns is cut to this when space runs out
INTERRUPTED = "Interrupted by the user before this finished."


def text_of(content) -> str:
    """Anthropic and others may return content blocks instead of a string."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(b.get("text", "") if isinstance(b, dict) and b.get("type", "text") == "text" else ""
                       for b in content)
    return str(content or "")


def build_coder(llm, workspace: Workspace):
    return create_agent(llm, tools=workspace.tools(), system_prompt=agent_system_prompt(workspace.root))


def _size(m: BaseMessage) -> int:
    return len(text_of(m.content)) + sum(len(str(c.get("args", ""))) + 40 for c in (getattr(m, "tool_calls", None) or []))


def fit_history(messages: list[BaseMessage], budget_chars: int) -> tuple[list[BaseMessage], bool]:
    """Make a conversation fit the model's context window.

    First the bulky tool output of earlier turns is shortened, then whole oldest turns are
    dropped. The latest turn is never touched. Returns (messages, whether anything was cut).
    """
    if sum(map(_size, messages)) <= budget_chars:
        return messages, False
    starts = [i for i, m in enumerate(messages) if isinstance(m, HumanMessage)]
    last_turn = starts[-1] if starts else len(messages)
    out = list(messages)
    for i in range(last_turn):
        m = out[i]
        if isinstance(m, ToolMessage) and len(text_of(m.content)) > OLD_OUTPUT_CHARS:
            body = text_of(m.content)
            out[i] = ToolMessage(content=body[:OLD_OUTPUT_CHARS] + f"\n[... {len(body) - OLD_OUTPUT_CHARS} characters of older output trimmed]",
                                 tool_call_id=m.tool_call_id, name=m.name, id=m.id, status=m.status)
    while sum(map(_size, out)) > budget_chars:
        starts = [i for i, m in enumerate(out) if isinstance(m, HumanMessage)]
        if len(starts) < 2:
            break
        out = out[starts[1]:]
    return out, True


def repair_history(messages: list[BaseMessage]) -> list[BaseMessage]:
    """A stopped turn can end on tool calls that never got an answer; providers reject that.

    Every unanswered call gets a short "interrupted" result, and the conversation is made
    to start at a user message.
    """
    out: list[BaseMessage] = []
    answered = {m.tool_call_id for m in messages if isinstance(m, ToolMessage)}
    for m in messages:
        out.append(m)
        if isinstance(m, AIMessage):
            for call in m.tool_calls or []:
                if call.get("id") and call["id"] not in answered:
                    out.append(ToolMessage(content=INTERRUPTED, tool_call_id=call["id"], name=call.get("name")))
                    answered.add(call["id"])
    while out and not isinstance(out[0], HumanMessage):
        out.pop(0)
    return out


class Session:
    """One conversation with the agent. Starts in `workspace.root`; the agent may move from there."""

    def __init__(self, llm, workspace: Workspace, store: Store | None = None, session_id: str | None = None,
                 source: str = "terminal", provider: str = "", model: str = "",
                 context_tokens: int = DEFAULT_CONTEXT_TOKENS):
        self.workspace = workspace
        self.store = store
        self.source = source
        self.provider, self.model = provider, model
        self.context_tokens = context_tokens
        self.history: list[BaseMessage] = []
        self.id = session_id or new_id()
        self.title = ""
        self._saved = False                     # a row exists in the store
        self.tokens = {"input": 0, "output": 0, "context": 0}
        self._llm = llm
        self._agent = None
        self._agent_cwd = None
        self._pending: list[tuple[str, dict]] = []  # events raised by tools while the agent runs
        workspace.on_sources = self._on_sources
        workspace.on_cwd = self._on_cwd
        if store is not None and session_id:
            row = store.get_session(session_id)
            if row:
                self.id, self.title, self._saved = row["id"], row["title"], True
                self.history = repair_history(store.load_messages(self.id))

    # ---------------------------------------------------------------- setup
    def set_llm(self, llm, provider: str = "", model: str = "", context_tokens: int | None = None) -> None:
        self._llm, self._agent = llm, None
        self.provider, self.model = provider or self.provider, model or self.model
        if context_tokens:
            self.context_tokens = context_tokens
        if self.store is not None and self._saved:
            self.store.update_session(self.id, provider=self.provider, model=self.model)

    def clear(self) -> None:
        """Start a new conversation (a new session id); the old one stays in the store."""
        self.history, self.title, self._saved = [], "", False
        self.id = new_id()
        self.tokens = {"input": 0, "output": 0, "context": 0}

    def _agent_for_cwd(self):
        # The system prompt names the working directory and the project's instruction file,
        # so the agent is rebuilt when the directory changes.
        if self._agent is None or self._agent_cwd != self.workspace.root:
            self._agent = build_coder(self._llm, self.workspace)
            self._agent_cwd = self.workspace.root
        return self._agent

    def _on_sources(self, query: str, docs: list) -> None:
        self._pending.append(("sources", {"query": query, "items": [
            {"page": d.metadata.get("page"), "source": d.metadata.get("source"), "text": d.page_content}
            for d in docs
        ]}))

    def _on_cwd(self, path) -> None:
        self._pending.append(("cwd", {"path": str(path)}))
        self._log("cwd", detail={"path": str(path)})
        if self.store is not None and self._saved:
            self.store.update_session(self.id, cwd=path)

    def _log(self, kind: str, name: str = "", detail: dict | None = None, ok: bool | None = None,
             ms: int | None = None) -> None:
        if self.store is not None and self._saved:
            try:
                self.store.add_event(self.id, kind, name, detail, ok, ms)
            except Exception:  # noqa: BLE001 - logging must never break a turn
                pass

    def _ensure_saved(self, first_text: str) -> None:
        if self.store is None:
            return
        if not self._saved:
            self.title = " ".join(first_text.split())[:80]
            self.store.create_session(self.workspace.root, self.provider, self.model, self.source,
                                      session_id=self.id, title=self.title)
            self._saved = True
        elif not self.title:
            self.title = " ".join(first_text.split())[:80]
            self.store.update_session(self.id, title=self.title)

    # ---------------------------------------------------------------- one turn
    def ask(self, text: str, shown: str | None = None) -> Iterator[tuple[str, dict]]:
        """`shown` is what the user typed when `text` also carries attached files (for titles and logs)."""
        self.workspace.stopped = False
        self._ensure_saved(shown or text)
        self._log("prompt", detail={"text": shown or text})
        started = time.monotonic()
        budget = int(self.context_tokens * HISTORY_SHARE * CHARS_PER_TOKEN)
        history, cut = fit_history(self.history, budget)
        if cut:
            yield "notice", {"text": "The conversation is long, so older tool output and turns were trimmed to fit the model."}
        messages = [*history, HumanMessage(text)]
        final: list[BaseMessage] | None = None
        streamed_ids: set[str] = set()
        tool_started: dict[str, float] = {}
        waited_before = self.workspace.waited
        tools = 0
        try:
            for mode, chunk in self._agent_for_cwd().stream(
                {"messages": messages},
                {"recursion_limit": RECURSION_LIMIT},
                stream_mode=["messages", "updates", "values"],
            ):
                yield from self._drain()
                if mode == "messages":
                    msg, meta = chunk
                    if meta.get("langgraph_node") == "model" and isinstance(msg, AIMessageChunk):
                        piece = text_of(msg.content)
                        if piece:
                            streamed_ids.add(msg.id or "")
                            yield "token", {"text": piece}
                elif mode == "updates":
                    for node, update in (chunk or {}).items():
                        for m in (update.get("messages", []) if isinstance(update, dict) else []):
                            if isinstance(m, AIMessage):
                                if (m.id or "") not in streamed_ids and text_of(m.content):
                                    yield "token", {"text": text_of(m.content)}  # a model that does not stream
                                for call in m.tool_calls or []:
                                    tools += 1
                                    # Time spent waiting for the user's approval is not the tool's time.
                                    tool_started[call.get("id") or ""] = time.monotonic() - self.workspace.waited
                                    yield "tool_start", {"id": call.get("id"), "name": call["name"],
                                                         "args": call.get("args", {})}
                            elif isinstance(m, ToolMessage):
                                out = text_of(m.content)
                                ok = m.status != "error" and not out.startswith("Error:")
                                began = tool_started.pop(m.tool_call_id or "", None)
                                ms = None
                                if began is not None:
                                    ms = max(0, int((time.monotonic() - self.workspace.waited - began) * 1000))
                                name = m.name or node
                                call_args = self._args_of(final, messages, m.tool_call_id)
                                self._log("tool", name, {"args": call_args, "output": out}, ok, ms)
                                yield "tool_end", {"id": m.tool_call_id, "name": name, "output": out, "ok": ok, "ms": ms}
                elif mode == "values":
                    final = chunk.get("messages")
            yield from self._drain()
        except BaseException as exc:
            if isinstance(exc, Exception):
                self._log("error", detail={"error": f"{type(exc).__name__}: {exc}"[:2000]})
            else:  # Ctrl+C, or the front end stopped listening
                self._log("interrupt")
            raise
        finally:
            # Keep what happened even if the turn was stopped or failed half way.
            if final:
                self.history = repair_history(list(final))
                if self.store is not None and self._saved:
                    try:
                        self.store.save_messages(self.id, self.history)
                    except Exception:  # noqa: BLE001
                        pass
        usage = self._usage(final or [], len(messages), started + (self.workspace.waited - waited_before), tools)
        self._log("usage", detail=usage, ms=int(usage["seconds"] * 1000))
        yield "usage", usage
        yield "done", {}

    def _drain(self):
        while self._pending:
            yield self._pending.pop(0)

    @staticmethod
    def _args_of(final, messages, call_id) -> dict:
        for m in reversed(final or messages):
            for call in (getattr(m, "tool_calls", None) or []):
                if call.get("id") == call_id:
                    return call.get("args", {})
        return {}

    def _usage(self, final: list[BaseMessage], sent: int, started: float, tools: int) -> dict:
        """Tokens of this turn. Providers that report none get an estimate from the text length."""
        new = [m for m in final[sent:] if isinstance(m, AIMessage)]
        inp = sum((m.usage_metadata or {}).get("input_tokens", 0) for m in new)
        out = sum((m.usage_metadata or {}).get("output_tokens", 0) for m in new)
        estimated = not (inp or out)
        if estimated:
            out = int(sum(_size(m) for m in new) / CHARS_PER_TOKEN)
            inp = int(sum(_size(m) for m in final[:sent]) / CHARS_PER_TOKEN)
        last = (new[-1].usage_metadata or {}) if new else {}
        context = last.get("input_tokens", 0) + last.get("output_tokens", 0) or int(sum(map(_size, final)) / CHARS_PER_TOKEN)
        self.tokens["input"] += inp
        self.tokens["output"] += out
        self.tokens["context"] = context
        return {"input": inp, "output": out, "context": context, "estimated": estimated,
                "seconds": round(time.monotonic() - started, 1), "tools": tools}
