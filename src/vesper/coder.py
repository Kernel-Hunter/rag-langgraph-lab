"""The workspace agent: a LangGraph `create_agent` loop over the tools in `workspace.py`.

`Session` keeps the conversation and turns each agent step into simple events that both
front ends draw: the terminal UI (`tui.py`) and the desktop app (`server.py`).

Events from `Session.ask()`:
    ("token", {"text"})                         streamed answer text
    ("tool_start", {"id", "name", "args"})      the model called a tool
    ("tool_end", {"id", "name", "output", "ok"})
    ("sources", {"query", "items"})             document passages found by search_docs
    ("done", {})
"""

from __future__ import annotations

from collections.abc import Iterator

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, HumanMessage, ToolMessage

from vesper.workspace import Workspace, system_prompt

MAX_HISTORY_MESSAGES = 200  # older turns are dropped so long sessions keep fitting the context
RECURSION_LIMIT = 80        # model and tool steps per question


def _text(content) -> str:
    """Anthropic and others may return content blocks instead of a string."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(b.get("text", "") if isinstance(b, dict) and b.get("type", "text") == "text" else ""
                       for b in content)
    return str(content or "")


def build_coder(llm, workspace: Workspace):
    return create_agent(llm, tools=workspace.tools(), system_prompt=system_prompt(workspace.root))


class Session:
    """One conversation with the agent in one folder."""

    def __init__(self, llm, workspace: Workspace):
        self.workspace = workspace
        self.history: list[BaseMessage] = []
        self._pending_sources: list[tuple[str, list]] = []
        workspace.on_sources = lambda query, docs: self._pending_sources.append((query, docs))
        self.agent = build_coder(llm, workspace)

    def set_llm(self, llm) -> None:
        self.agent = build_coder(llm, self.workspace)

    def clear(self) -> None:
        self.history = []

    def ask(self, text: str) -> Iterator[tuple[str, dict]]:
        messages = [*self.history, HumanMessage(text)]
        final: list[BaseMessage] | None = None
        streamed_ids: set[str] = set()
        try:
            for mode, chunk in self.agent.stream(
                {"messages": messages},
                {"recursion_limit": RECURSION_LIMIT},
                stream_mode=["messages", "updates", "values"],
            ):
                yield from self._drain_sources()
                if mode == "messages":
                    msg, meta = chunk
                    if meta.get("langgraph_node") == "model" and isinstance(msg, AIMessageChunk):
                        text = _text(msg.content)
                        if text:
                            streamed_ids.add(msg.id or "")
                            yield "token", {"text": text}
                elif mode == "updates":
                    for node, update in (chunk or {}).items():
                        msgs = update.get("messages", []) if isinstance(update, dict) else []
                        for m in msgs:
                            if isinstance(m, AIMessage):
                                if (m.id or "") not in streamed_ids and _text(m.content):
                                    yield "token", {"text": _text(m.content)}  # model that does not stream
                                for call in m.tool_calls or []:
                                    yield "tool_start", {"id": call.get("id"), "name": call["name"],
                                                         "args": call.get("args", {})}
                            elif isinstance(m, ToolMessage):
                                out = _text(m.content)
                                yield "tool_end", {
                                    "id": m.tool_call_id, "name": m.name or node, "output": out,
                                    "ok": m.status != "error" and not out.startswith("Error:"),
                                }
                elif mode == "values":
                    final = chunk.get("messages")
            yield from self._drain_sources()
        finally:
            # Keep what happened even if the turn was stopped or failed half way.
            if final:
                self.history = list(final)[-MAX_HISTORY_MESSAGES:]
                # A trimmed conversation must start at a user message, never mid tool call.
                while self.history and not isinstance(self.history[0], HumanMessage):
                    self.history.pop(0)
        yield "done", {}

    def _drain_sources(self):
        while self._pending_sources:
            query, docs = self._pending_sources.pop(0)
            yield "sources", {"query": query, "items": [
                {"page": d.metadata.get("page"), "source": d.metadata.get("source"), "text": d.page_content}
                for d in docs
            ]}
