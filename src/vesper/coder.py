"""The coding agent: a LangGraph `create_agent` loop over the tools in `workspace.py`.

`Session` is one conversation. It keeps the history, saves it to the SQLite store, logs
what happens, and turns each agent step into simple events that both front ends draw: the
terminal UI (`tui.py`) and the desktop app (`server.py`).

Events from `Session.ask()`:
    ("token", {"text"})                              streamed answer text
    ("reasoning", {"text"})                          streamed thinking, for models that show it
    ("tool_draft", {"name", "chars"})                the model is writing a tool call (its arguments so far)
    ("tool_start", {"id", "name", "args"})           the model called a tool
    ("tool_end", {"id", "name", "output", "ok", "ms"})
    ("sources", {"query", "items"})                  document passages found by search_docs
    ("cwd", {"path"})                                the working directory changed
    ("tasks", {"items": [{"text", "status"}]})       the agent's task list changed
    ("notice", {"text"})                             something the user should know (context trimmed)
    ("usage", {"input", "output", "cached", "context", "seconds", "tools"})   once, at the end of the turn
    ("done", {})
"""

from __future__ import annotations

import ast
import json
import re
import time
from collections.abc import Iterator
from pathlib import Path

from langchain.agents import create_agent
from langchain.agents.middleware import wrap_model_call
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from vesper.prompts import COMPACT_PROMPT, agent_system_prompt
from vesper.store import Store, new_id
from vesper.workspace import Workspace

RECURSION_LIMIT = 120            # model and tool steps per question
DEFAULT_CONTEXT_TOKENS = 120_000  # assumed context window when the provider does not say
HISTORY_SHARE = 0.6              # part of the window the history may fill; the rest is prompt, tools and the answer
CHARS_PER_TOKEN = 3.5            # rough, on the safe side for code
OLD_OUTPUT_CHARS = 1_200         # tool output from earlier turns is cut to this when space runs out
RECENT_OUTPUTS = 3               # tool results of the current turn that are never shortened
KEEP_TURNS = 2                   # latest turns kept word for word when older ones are summarized
MAX_SUMMARY_INPUT_CHARS = 60_000
MAX_CHECKPOINT_BYTES = 5_000_000  # larger files are changed without an undo copy
INTERRUPTED = "Interrupted by the user before this finished."
SUMMARY_OPEN, SUMMARY_ACK = "<summary>", "Understood. I will continue from this summary."
SUPERSEDED = "[This file was read again later in the conversation; use the newer result.]"
RETRY_HINT = ("Your last reply was empty or was a tool call whose arguments were not valid JSON. "
              "Reply again: either call the tool with valid JSON arguments or answer in text.")


def text_of(content) -> str:
    """Anthropic and others may return content blocks instead of a string."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(b.get("text", "") if isinstance(b, dict) and b.get("type", "text") == "text" else ""
                       for b in content)
    return str(content or "")


def reasoning_of(msg: BaseMessage) -> str:
    """The thinking a model sent next to its answer, whatever shape the provider gives it."""
    out = []
    if isinstance(msg.content, list):
        for block in msg.content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "thinking":                      # Anthropic
                out.append(str(block.get("thinking") or ""))
            elif block.get("type") in ("reasoning", "reasoning_content"):
                value = block.get("reasoning") or block.get("reasoning_content") or block.get("text") or ""
                if not value and isinstance(block.get("summary"), list):  # OpenAI reasoning summaries
                    value = "".join(str(part.get("text", "")) for part in block["summary"] if isinstance(part, dict))
                out.append(value if isinstance(value, str) else str(value.get("text", "")) if isinstance(value, dict) else "")
    extra = (msg.additional_kwargs or {}).get("reasoning_content")  # Ollama, DeepSeek
    if isinstance(extra, str):
        out.append(extra)
    return "".join(out)


class ThinkSplitter:
    """Separates `<think>...</think>` from the answer in a stream of text pieces.

    Local reasoning models write their thinking inline between these tags. A tag can be cut
    in two by the stream, so a possible half tag at the end of a piece is held back.
    """

    OPEN, CLOSE = "<think>", "</think>"

    def __init__(self) -> None:
        self.inside = False
        self.held = ""

    def feed(self, text: str) -> list[tuple[str, str]]:
        """[("reasoning" | "token", text), ...] for one more piece of the stream."""
        out: list[tuple[str, str]] = []
        buf, self.held = self.held + text, ""
        while buf:
            kind = "reasoning" if self.inside else "token"
            tag = self.CLOSE if self.inside else self.OPEN
            at = buf.find(tag)
            if at >= 0:
                if buf[:at]:
                    out.append((kind, buf[:at]))
                buf = buf[at + len(tag):]
                self.inside = not self.inside
                continue
            keep = next((n for n in range(min(len(tag) - 1, len(buf)), 0, -1) if tag.startswith(buf[-n:])), 0)
            if buf[:len(buf) - keep]:
                out.append((kind, buf[:len(buf) - keep]))
            self.held = buf[len(buf) - keep:]
            break
        return out

    def flush(self) -> list[tuple[str, str]]:
        held, self.held = self.held, ""
        return [("reasoning" if self.inside else "token", held)] if held else []


def split_thinking(text: str) -> tuple[str, str]:
    """(thinking, answer) of a finished message that may hold `<think>` blocks."""
    splitter = ThinkSplitter()
    parts = splitter.feed(text) + splitter.flush()
    return ("".join(t for k, t in parts if k == "reasoning").strip(),
            "".join(t for k, t in parts if k == "token").strip())


def transcript(messages: list[BaseMessage]) -> list[dict]:
    """A saved conversation as turns the front ends can draw.

    [{"role": "user", "text"}, {"role": "assistant", "parts": [...]}] where a part is
    {"type": "reasoning" | "text", "text"} or {"type": "tool", "id", "name", "args", "output", "ok"}.
    """
    turns: list[dict] = []
    tools: dict[str, dict] = {}
    for m in messages:
        if isinstance(m, HumanMessage):
            text = text_of(m.content)
            if text.startswith(SUMMARY_OPEN):  # older turns, replaced by a summary (Session.compact)
                body = text.removeprefix(SUMMARY_OPEN).split("</summary>")[0].strip()
                turns.append({"role": "assistant", "parts": [
                    {"type": "text", "text": "**Summary of the earlier conversation**\n\n" + body}]})
            else:
                turns.append({"role": "user", "text": re.split(r"\n\n<(?:file path=|note>)", text)[0].strip()})
            continue
        if not turns or turns[-1]["role"] != "assistant":
            turns.append({"role": "assistant", "parts": []})
        parts = turns[-1]["parts"]
        if isinstance(m, AIMessage):
            inline, answer = split_thinking(text_of(m.content))
            if answer == SUMMARY_ACK:
                continue
            thought = (reasoning_of(m) + ("\n" if reasoning_of(m) and inline else "") + inline).strip()
            if thought:
                parts.append({"type": "reasoning", "text": thought})
            if answer:
                parts.append({"type": "text", "text": answer})
            for call in m.tool_calls or []:
                part = {"type": "tool", "id": call.get("id") or "", "name": call["name"],
                        "args": call.get("args", {}), "output": None, "ok": None}
                tools[part["id"]] = part
                parts.append(part)
        elif isinstance(m, ToolMessage):
            part = tools.get(m.tool_call_id or "")
            if part is not None:
                out = text_of(m.content)
                part["output"], part["ok"] = out, m.status != "error" and not out.startswith("Error:")
    return turns


def build_coder(llm, workspace: Workspace, middleware=()):
    plan = workspace.mode == "plan"
    return create_agent(llm, tools=workspace.tools(), system_prompt=agent_system_prompt(workspace.root, plan),
                        middleware=list(middleware))


def _size(m: BaseMessage) -> int:
    return len(text_of(m.content)) + sum(len(str(c.get("args", ""))) + 40 for c in (getattr(m, "tool_calls", None) or []))


def _shorter(m: ToolMessage, body: str) -> ToolMessage:
    return ToolMessage(content=body, tool_call_id=m.tool_call_id, name=m.name, id=m.id, status=m.status)


def _clip_output(m: BaseMessage, limit: int = OLD_OUTPUT_CHARS) -> BaseMessage:
    body = text_of(m.content) if isinstance(m, ToolMessage) else ""
    if len(body) <= limit:
        return m
    return _shorter(m, body[:limit] + f"\n[... {len(body) - limit} characters of this output trimmed to save context]")


def _drop_repeated_reads(messages: list[BaseMessage]) -> list[BaseMessage]:
    """When the same part of a file was read more than once, only the last result is kept."""
    calls = {c.get("id"): (c["name"], json.dumps(c.get("args", {}), sort_keys=True, default=str))
             for m in messages if isinstance(m, AIMessage) for c in (m.tool_calls or [])}
    out, newer = list(messages), set()
    for i in range(len(out) - 1, -1, -1):
        m = out[i]
        call = calls.get(m.tool_call_id) if isinstance(m, ToolMessage) else None
        if call is None or call[0] != "read_file" or text_of(m.content).startswith(("Error:", "You already")):
            continue
        if call in newer:
            out[i] = _shorter(m, SUPERSEDED)
        newer.add(call)
    return out


def fit_history(messages: list[BaseMessage], budget_chars: int) -> tuple[list[BaseMessage], bool]:
    """Make a conversation fit the model's context window, cheapest cut first.

    1. a file read several times keeps only its last result
    2. bulky tool output of earlier turns is shortened
    3. whole oldest turns are dropped (`Session` summarizes them first when it can); a summary
       at the start of the conversation is never dropped
    4. tool output of the current turn is shortened: first all but the most recent results,
       then all but the last one, then the last one too

    Returns (messages, whether anything was cut).
    """
    def total(msgs) -> int:
        return sum(map(_size, msgs))

    if total(messages) <= budget_chars:
        return messages, False
    out = _drop_repeated_reads(messages)
    if total(out) <= budget_chars:
        return out, True
    starts = [i for i, m in enumerate(out) if isinstance(m, HumanMessage)]
    last_turn = starts[-1] if starts else len(out)
    out = [_clip_output(m) if i < last_turn else m for i, m in enumerate(out)]
    summary = 2 if len(out) > 1 and text_of(out[0].content).startswith(SUMMARY_OPEN) else 0
    head, out = out[:summary], out[summary:]
    while total(head) + total(out) > budget_chars:
        starts = [i for i, m in enumerate(out) if isinstance(m, HumanMessage)]
        if len(starts) < 2:
            break
        out = out[starts[1]:]
    out = head + out
    results = [i for i, m in enumerate(out) if isinstance(m, ToolMessage)]
    for keep in (RECENT_OUTPUTS, 1):
        if total(out) <= budget_chars:
            break
        for i in results[:-keep]:
            out[i] = _clip_output(out[i])
    if results and total(out) > budget_chars:
        out[results[-1]] = _clip_output(out[results[-1]], max(OLD_OUTPUT_CHARS, budget_chars // 2))
    return out, True


def _loose_json(text):
    """JSON the way small models write it: single quotes, True/None, a trailing comma."""
    if not isinstance(text, str):
        return text
    text = text.strip()
    for load in (json.loads, ast.literal_eval, lambda t: json.loads(re.sub(r",\s*([}\]])", r"\1", t))):
        try:
            return load(text)
        except (ValueError, SyntaxError, MemoryError, RecursionError):
            continue
    return None


def _call_from(data, names: set[str]) -> dict | None:
    """{"name", "args"} when `data` is a tool call in one of the shapes models use for it."""
    if not isinstance(data, dict):
        return None
    inner = data.get("function") if isinstance(data.get("function"), dict) else data
    name = inner.get("name") or inner.get("tool") or inner.get("tool_name")
    args = next((inner[k] for k in ("arguments", "parameters", "args", "input") if k in inner), None)
    args = _loose_json(args)
    if name in names and isinstance(args, dict):
        return {"name": name, "args": args}
    return None


def repair_tool_calls(msg: AIMessage, names: set[str]) -> AIMessage | None:
    """A reply that was meant as a tool call but did not arrive as one, made into one.

    Small local models do two things: they send arguments that are almost JSON, and they write
    the call as text (`{"name": "read_file", "arguments": {...}}`, sometimes inside
    <tool_call> tags or a code fence) instead of using the tool-call channel. Without this the
    turn would simply end. Returns None when there is nothing to repair.
    """
    if msg.tool_calls:
        return None
    found = []
    for bad in msg.invalid_tool_calls or []:
        call = _call_from({"name": bad.get("name"), "args": bad.get("args") or "{}"}, names)
        if call:
            found.append({**call, "id": bad.get("id")})
    if not found:
        _, text = split_thinking(text_of(msg.content))
        text = text.strip()
        blobs = re.findall(r"<tool_call>(.*?)</tool_call>", text, re.S)
        if not blobs:
            fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, re.S)
            blobs = [fenced.group(1)] if fenced else [text] if text.startswith("{") and text.endswith("}") else []
        for blob in blobs:
            data = _loose_json(blob)
            for item in data if isinstance(data, list) else [data]:
                call = _call_from(item, names)
                if call:
                    found.append({**call, "id": None})
    if not found:
        return None
    calls = [{"name": c["name"], "args": c["args"], "id": c["id"] or f"call_{new_id()}", "type": "tool_call"}
             for c in found]
    return AIMessage(content="", tool_calls=calls, id=msg.id, usage_metadata=msg.usage_metadata,
                     response_metadata=msg.response_metadata)


def _render(messages: list[BaseMessage]) -> str:
    """A conversation as plain text for the summarizer, with long parts cut."""
    lines = []
    for m in messages:
        body = text_of(m.content).strip()
        if isinstance(m, HumanMessage):
            lines.append("USER: " + body[:3000])
        elif isinstance(m, AIMessage):
            if body:
                lines.append("ASSISTANT: " + split_thinking(body)[1][:3000])
            lines += [f"TOOL CALL: {c['name']} {json.dumps(c.get('args', {}), default=str)[:300]}" for c in m.tool_calls or []]
        elif isinstance(m, ToolMessage):
            lines.append("RESULT: " + body[:500])
    return "\n".join(lines)[-MAX_SUMMARY_INPUT_CHARS:]


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
        self.tokens = {"input": 0, "output": 0, "cached": 0, "context": 0}
        self._llm = llm
        self._agent = None
        self._agent_key = None
        self._pending: list[tuple[str, dict]] = []  # events raised by tools while the agent runs
        self._turn_id = 0                           # groups the file copies kept for undo
        self._copies: list[tuple[int, str, bytes | None]] = []  # (turn, path, old bytes), newest last
        self._undone: list[str] = []                # files put back by undo, to tell the model next turn
        workspace.on_sources = self._on_sources
        workspace.on_cwd = self._on_cwd
        workspace.on_change = self._on_change
        workspace.on_tasks = lambda items: self._pending.append(("tasks", {"items": [dict(t) for t in items]}))
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
        self.tokens = {"input": 0, "output": 0, "cached": 0, "context": 0}
        self._copies, self._undone, self.workspace.tasks = [], [], []

    def _agent_for_cwd(self):
        # The system prompt names the working directory, the project's instruction file and
        # whether plan mode is on, so the agent is rebuilt when one of them changes.
        key = (self.workspace.root, self.workspace.mode == "plan")
        if self._agent is None or self._agent_key != key:
            self._agent = build_coder(self._llm, self.workspace, self._middleware())
            self._agent_key = key
        return self._agent

    def _budget(self) -> int:
        """Characters of conversation the model's window has room for."""
        return int(self.context_tokens * HISTORY_SHARE * CHARS_PER_TOKEN)

    def _middleware(self) -> list:
        """What happens around every model call inside a turn."""
        from vesper import providers as prov

        session = self
        names = {t.name for t in self.workspace.tools()}

        @wrap_model_call
        def guard(request, handler):
            # A long turn can outgrow the window on its own (many files read in one go).
            messages, cut = fit_history(request.messages, session._budget())
            if cut:
                session.workspace._seen.clear()  # trimmed results may need to be read again
                request = request.override(messages=messages)
            for _heal in range(4):
                if request.model is not session._llm:  # a setting was dropped earlier in this turn
                    request = request.override(model=session._llm)
                try:
                    response = handler(request)
                    break
                except Exception as exc:  # noqa: BLE001 - re-raised unless a refused setting explains it
                    fixed = prov.adapt(session._llm, exc)
                    if fixed is None or _heal == 3:
                        raise
                    session._llm, session._agent = fixed, None
            for _attempt in range(2):
                msg = response if isinstance(response, AIMessage) else next(
                    (m for m in reversed(getattr(response, "result", [])) if isinstance(m, AIMessage)), None)
                if msg is None:
                    return response
                fixed = repair_tool_calls(msg, names)
                if fixed is not None:
                    return fixed
                broken = bool(msg.invalid_tool_calls) or not (msg.tool_calls or text_of(msg.content).strip() or reasoning_of(msg))
                if not broken or _attempt:
                    return response
                response = handler(request.override(messages=[*request.messages, HumanMessage(RETRY_HINT)]))
            return response

        return [guard, *prov.agent_middleware(self._llm)]

    # ---------------------------------------------------------------- context
    def compact(self, keep_turns: int = KEEP_TURNS) -> bool:
        """Replace older turns with a summary written by the model. Returns whether it did.

        Unlike dropping turns, the model keeps what was decided, which files were touched and
        what is still open. The latest `keep_turns` turns stay word for word.
        """
        starts = [i for i, m in enumerate(self.history) if isinstance(m, HumanMessage)]
        keep_turns = min(keep_turns, len(starts) - 1) if len(starts) > 1 else 0
        cut = starts[len(starts) - keep_turns] if keep_turns else len(self.history)
        older = self.history[:cut]
        if not older or (len(older) == 2 and text_of(older[0].content).startswith(SUMMARY_OPEN)):
            return False
        try:
            reply = None  # streamed: some providers refuse a long non-streaming request
            for piece in self._llm.stream([SystemMessage(COMPACT_PROMPT), HumanMessage(_render(older))]):
                reply = piece if reply is None else reply + piece
        except Exception as exc:  # noqa: BLE001 - the caller falls back to trimming
            self._log("error", detail={"error": f"compact: {type(exc).__name__}: {exc}"[:2000]})
            return False
        summary = split_thinking(text_of(reply.content))[1].strip() if reply is not None else ""
        if not summary:
            return False
        before = sum(map(_size, self.history))
        self.history = [HumanMessage(f"{SUMMARY_OPEN}\n{summary}\n</summary>"), AIMessage(SUMMARY_ACK), *self.history[cut:]]
        used = getattr(reply, "usage_metadata", None) or {}
        self.tokens["input"] += used.get("input_tokens", 0)
        self.tokens["output"] += used.get("output_tokens", 0)
        self.tokens["context"] = int(sum(map(_size, self.history)) / CHARS_PER_TOKEN)
        self._log("compact", detail={"before": int(before / CHARS_PER_TOKEN), "after": self.tokens["context"]})
        self._save()
        return True

    def _save(self) -> None:
        if self.store is not None and self._saved:
            try:
                self.store.save_messages(self.id, self.history)
            except Exception:  # noqa: BLE001
                pass

    # ---------------------------------------------------------------- undo
    def _on_change(self, path: Path, before: bytes | None) -> None:
        """A tool is about to write `path`: keep what was there (once per turn and file)."""
        if any(turn == self._turn_id and p == str(path) for turn, p, _ in self._copies):
            return
        if before is not None and len(before) > MAX_CHECKPOINT_BYTES:
            return
        self._copies.append((self._turn_id, str(path), before))
        if self.store is not None and self._saved:
            try:
                self.store.add_checkpoint(self.id, self._turn_id, path, before)
            except Exception:  # noqa: BLE001 - a missing undo copy must not stop the edit
                pass

    def undo(self) -> list[str]:
        """Put files back to how they were before the latest turn that changed any.

        Returns the paths restored (empty when there is nothing to undo). Commands the agent
        ran are not undone. The model is told about it with the next message.
        """
        rows = self._copies
        if self.store is not None and self._saved:
            rows = self.store.checkpoints(self.id)
        if not rows:
            return []
        turn = max(r[0] for r in rows)
        paths = []
        for _, path, before in [r for r in rows if r[0] == turn]:
            target = Path(path)
            if before is None:
                target.unlink(missing_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(before)
            paths.append(path)
        self._copies = [r for r in self._copies if r[0] != turn]
        if self.store is not None and self._saved:
            self.store.delete_checkpoints(self.id, turn)
        self.workspace._seen.clear()
        self._undone += paths
        self._log("undo", detail={"paths": paths})
        return paths

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
        self.workspace._seen.clear()
        self._ensure_saved(shown or text)
        self._log("prompt", detail={"text": shown or text})
        started = time.monotonic()
        self._turn_id = max(int(time.time() * 1000), self._turn_id + 1)
        if self._undone:  # the model still remembers its edits; tell it they are gone
            text += ("\n\n<note>The user undid your changes to these files; they are back to how they were "
                     "before: " + ", ".join(self._undone) + "</note>")
            self._undone = []
        if sum(map(_size, self.history)) > self._budget() and self.compact():
            yield "notice", {"text": "The conversation is long, so older turns were summarized to save context."}
        history, cut = fit_history(self.history, self._budget())
        if cut:
            yield "notice", {"text": "The conversation is long, so older tool output and turns were trimmed to fit the model."}
        messages = [*history, HumanMessage(text)]
        final: list[BaseMessage] | None = None
        streamed_ids: set[str] = set()
        tool_started: dict[str, float] = {}
        waited_before = self.workspace.waited
        tools = 0
        splitter = ThinkSplitter()
        drafts: dict[int, dict] = {}  # tool calls the model is still writing, by position
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
                        thought = reasoning_of(msg)
                        if thought:
                            streamed_ids.add(msg.id or "")
                            yield "reasoning", {"text": thought}
                        piece = text_of(msg.content)
                        if piece:
                            streamed_ids.add(msg.id or "")
                            for kind, part in splitter.feed(piece):
                                yield kind, {"text": part}
                        for part in msg.tool_call_chunks or []:
                            draft = drafts.setdefault(part.get("index") or 0, {"name": "", "chars": 0, "told": -1})
                            draft["name"] = part.get("name") or draft["name"]
                            draft["chars"] += len(part.get("args") or "")
                            if draft["name"] and draft["chars"] - draft["told"] >= 120:
                                draft["told"] = draft["chars"]
                                yield "tool_draft", {"name": draft["name"], "chars": draft["chars"]}
                elif mode == "updates":
                    for node, update in (chunk or {}).items():
                        for m in (update.get("messages", []) if isinstance(update, dict) else []):
                            if isinstance(m, AIMessage):
                                yield from ((kind, {"text": part}) for kind, part in splitter.flush())
                                drafts.clear()
                                if (m.id or "") not in streamed_ids:  # a model that does not stream
                                    thought, answer = split_thinking(text_of(m.content))
                                    thought = reasoning_of(m) + thought
                                    if thought:
                                        yield "reasoning", {"text": thought}
                                    if answer:
                                        yield "token", {"text": answer}
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
                self._save()
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
        cached = sum(((m.usage_metadata or {}).get("input_token_details") or {}).get("cache_read", 0) or 0 for m in new)
        estimated = not (inp or out)
        if estimated:
            out = int(sum(_size(m) for m in new) / CHARS_PER_TOKEN)
            inp = int(sum(_size(m) for m in final[:sent]) / CHARS_PER_TOKEN)
        last = (new[-1].usage_metadata or {}) if new else {}
        context = last.get("input_tokens", 0) + last.get("output_tokens", 0) or int(sum(map(_size, final)) / CHARS_PER_TOKEN)
        self.tokens["input"] += inp
        self.tokens["output"] += out
        self.tokens["cached"] += cached
        self.tokens["context"] = context
        return {"input": inp, "output": out, "cached": cached, "context": context, "estimated": estimated,
                "seconds": round(time.monotonic() - started, 1), "tools": tools}
