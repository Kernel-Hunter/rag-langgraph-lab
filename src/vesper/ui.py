"""How the terminal app looks: colors, the banner, tool lines, the streaming answer,
and the tables for sessions and the activity log.

Nothing here talks to a model or touches files; `tui.py` decides what to show and when.
"""

from __future__ import annotations

import datetime as dt
import re
import time
from pathlib import Path

from rich import box
from rich.console import Console, Group
from rich.live import Live
from rich.markdown import Markdown
from rich.padding import Padding
from rich.panel import Panel
from rich.spinner import Spinner
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from vesper import __version__

ACCENT = "#8b7cff"
ACCENT_2 = "#2dd4bf"
MUTED = "grey58"
OK = "#34d399"
ERR = "#f87171"
WARN = "#fbbf24"

MODES = {
    "ask": "asks before edits and commands",
    "auto-edit": "edits files freely, asks before commands",
    "yolo": "never asks (careful)",
    "plan": "reads and plans; changes nothing until you approve the plan",
}
TOOL_LABELS = {
    "list_files": "List", "read_file": "Read", "glob": "Find", "grep": "Search",
    "change_directory": "Go to", "write_file": "Write",
    "edit_file": "Edit", "run_command": "Run", "update_tasks": "Tasks", "web_fetch": "Fetch",
    "web_search": "Search the web",
}
# The argument that says most about a tool call.
MAIN_ARG = {
    "read_file": "path", "write_file": "path", "edit_file": "path", "list_files": "path",
    "change_directory": "path", "glob": "pattern", "grep": "pattern",
    "run_command": "command", "web_fetch": "url", "web_search": "query",
}
LIVE_OUTPUT_LINES = 5     # lines of a running command kept on screen
LIVE_THOUGHT_LINES = 4    # lines of the model's thinking kept on screen while it thinks
VERBOSE_OUTPUT_LINES = 60  # lines of tool output shown in verbose mode
AUTO_DIFF_LINES = 24      # lines of a diff shown for an edit that did not need approval

console = Console(highlight=False)
err_console = Console(stderr=True, highlight=False)


# ---------------------------------------------------------------- small pieces
def gradient(text: str, start: str = ACCENT, end: str = ACCENT_2) -> Text:
    def rgb(h):
        return tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))

    a, b = rgb(start), rgb(end)
    out = Text()
    n = max(1, len(text) - 1)
    for i, ch in enumerate(text):
        t = i / n
        c = tuple(round(a[j] + (b[j] - a[j]) * t) for j in range(3))
        out.append(ch, style=f"bold #{c[0]:02x}{c[1]:02x}{c[2]:02x}")
    return out


def short_path(p: Path | str) -> str:
    p = Path(p)
    home = Path.home()
    if p == home:
        return "~"
    try:
        return "~/" + p.relative_to(home).as_posix()
    except ValueError:
        return str(p)


def tail_path(p: Path | str, width: int = 40) -> str:
    """short_path(), keeping the end when it is too long: the last folders say the most."""
    text = short_path(p)
    if len(text) <= width:
        return text
    parts = Path(text).parts
    kept: list[str] = []
    for part in reversed(parts):
        if len("/".join([part, *kept])) + 2 > width and kept:
            break
        kept.insert(0, part)
    return "…/" + "/".join(kept)


def model_label(cfg: dict) -> str:
    from vesper import providers as prov

    p = prov.PROVIDERS.get(cfg.get("provider", ""))
    if p is None:
        return "no model"
    return f"{p.label} · {cfg.get('model') or p.chat_model}"


def fmt_tokens(n: int) -> str:
    return f"{n / 1000:.1f}k" if n >= 1000 else str(n)


def fmt_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s" if seconds < 10 else f"{seconds:.0f}s"
    minutes, rest = divmod(int(seconds), 60)
    return f"{minutes}m {rest:02d}s"


def fmt_ago(timestamp: float) -> str:
    gap = max(0, time.time() - timestamp)
    for limit, unit, size in ((60, "s", 1), (3600, "m", 60), (86400, "h", 3600), (86400 * 14, "d", 86400)):
        if gap < limit:
            return f"{int(gap // size)}{unit} ago"
    return dt.datetime.fromtimestamp(timestamp).strftime("%d %b %Y")


def banner(root: Path, cfg: dict, mode: str) -> None:
    title = Text.assemble(("✦ ", f"bold {ACCENT}"), gradient("Vesper"), (f"  v{__version__}", MUTED))
    rows = Table.grid(padding=(0, 2))
    rows.add_column(style=MUTED, justify="right")
    rows.add_column()
    rows.add_row("model", model_label(cfg))
    rows.add_row("folder", tail_path(root, 70))
    rows.add_row("mode", f"{mode} · {MODES[mode]}")
    key = f"bold {ACCENT}"
    tips = Text.assemble(
        ("Ask for anything: code, files, commands.\n", ""),
        ("/", key), (" commands  ", MUTED), ("@file", key), (" attach  ", MUTED), ("!cmd", key), (" shell  ", MUTED),
        ("/cd", key), (" move  ", MUTED), ("/resume", key), (" history  ", MUTED), ("shift+tab", key), (" mode", MUTED),
    )
    console.print(Panel(Group(title, Text(""), rows, Text(""), tips), box=box.ROUNDED,
                        border_style=ACCENT, padding=(1, 2), expand=False))


def error_panel(message: str, hint: str = "") -> None:
    body = Text(message)
    if hint:
        body.append("\n" + hint, style=MUTED)
    console.print(Panel(body, title="error", title_align="left", border_style=ERR, box=box.ROUNDED, expand=False))


def note(text: str, style: str = MUTED) -> None:
    console.print(Text("  " + text, style=style))


# ---------------------------------------------------------------- tool calls
def summarize_args(name: str, args: dict) -> str:
    key = MAIN_ARG.get(name)
    value = str(args.get(key, "")) if key else ""
    if name in ("grep", "glob") and args.get("path") not in (None, "", "."):
        value += f"  in {args['path']}"
    value = value.replace("\n", " ")
    return value if len(value) <= 90 else value[:87] + "…"


def summarize_output(name: str, output: str, ok: bool, limit: int = 6) -> Text:
    """A tool result in a few words. `limit` is how many lines of command output to keep."""
    if not ok:
        first = output.removeprefix("Error:").strip().splitlines()[0] if output.strip() else "failed"
        return Text(first[:200], style=ERR)
    lines = output.splitlines()
    if name == "read_file":
        pages = sum(1 for line in lines if line.startswith("[page "))
        if pages:
            return Text(f"{pages} page{'s' if pages != 1 else ''}", style=MUTED)
        n = sum(1 for line in lines if re.match(r"^\s*\d+\t", line))
        return Text(f"{n} line{'s' if n != 1 else ''}" if n else (lines[0][:120] if lines else ""), style=MUTED)
    if name in ("glob", "grep"):
        n = 0 if output.startswith("No ") else len([line for line in lines if not line.startswith(("...", "("))])
        return Text(f"{n} result{'s' if n != 1 else ''}", style=MUTED)
    if name == "list_files":
        return Text(f"{max(0, len(lines) - 1)} entries", style=MUTED)
    if name == "change_directory":
        return Text(short_path(lines[0].removeprefix("Working directory is now ")) if lines else "", style=MUTED)
    if name == "run_command":
        head = lines[0] if lines else ""
        body = [line for line in lines[1:] if line.strip()]
        rest = body[:limit]
        t = Text(head, style=OK if head.startswith("exit code 0") else WARN)
        for line in rest:
            t.append("\n     " + line[:160], style=MUTED)
        if len(body) > len(rest):
            t.append(f"\n     … {len(body) - len(rest)} more lines (ctrl+o shows all)", style=MUTED)
        return t
    return Text(lines[0][:200] if lines else "done", style=MUTED)


def tool_start_line(name: str, args: dict) -> Text:
    return Text.assemble(("● ", ACCENT), (TOOL_LABELS.get(name, name), "bold"), ("  " + summarize_args(name, args), MUTED))


def tool_end_line(name: str, output: str, ok: bool, ms: int | None, verbose: bool = False) -> Text:
    line = Text("  ⎿  ", style=MUTED) + summarize_output(name, output, ok, VERBOSE_OUTPUT_LINES if verbose else 6)
    if ms is not None and ms >= 1000:
        line.append(f"  ({fmt_duration(ms / 1000)})", style=MUTED)
    if verbose and ok and len(output.splitlines()) > 1 and name != "run_command":
        shown = output.splitlines()[:VERBOSE_OUTPUT_LINES]
        for row in shown:
            line.append("\n     " + row[:200], style=MUTED)
        extra = len(output.splitlines()) - len(shown)
        if extra > 0:
            line.append(f"\n     … {extra} more lines", style=MUTED)
    return line


def diff_panel(action: dict, title: str, border: str = WARN, limit: int | None = None) -> Panel:
    diff = action.get("diff") or "(no changes)"
    rows = diff.splitlines()
    if limit and len(rows) > limit:
        diff = "\n".join(rows[:limit]) + f"\n… {len(rows) - limit} more lines"
    return Panel(Syntax(diff, "diff", theme="ansi_dark", word_wrap=True), title=title, title_align="left",
                 border_style=border, box=box.ROUNDED)


def turn_footer(usage: dict, context_tokens: int) -> Text:
    parts = [fmt_duration(usage["seconds"])]
    if usage["tools"]:
        parts.append(f"{usage['tools']} tool call{'s' if usage['tools'] != 1 else ''}")
    approx = "~" if usage.get("estimated") else ""
    cached = f" ({fmt_tokens(usage['cached'])} cached)" if usage.get("cached") else ""
    parts.append(f"{approx}{fmt_tokens(usage['input'])} in{cached}, {approx}{fmt_tokens(usage['output'])} out")
    if context_tokens:
        parts.append(f"context {min(100, round(100 * usage['context'] / context_tokens))}%")
    return Text("  ✓ " + " · ".join(parts), style=MUTED)


def tasks_block(items: list[dict]) -> Text:
    """The agent's task list: done, in progress, to do."""
    out = Text()
    for n, item in enumerate(items):
        mark, style = {"done": ("✔", MUTED), "active": ("▸", f"bold {ACCENT}")}.get(item.get("status"), ("☐", ""))
        out.append(("\n" if n else "") + f"  {mark} ", style=OK if item.get("status") == "done" else style)
        out.append(str(item.get("text", "")), style=style)
    return out


# ---------------------------------------------------------------- streaming renderer
def _last_block_end(text: str) -> int:
    """Index just after the last blank line that is not inside a ``` fence, or 0."""
    end = 0
    inside = False
    pos = 0
    for line in text.splitlines(keepends=True):
        pos += len(line)
        if line.lstrip().startswith("```"):
            inside = not inside
            continue
        if not inside and not line.strip() and pos < len(text):
            end = pos
    return end


class _Busy:
    """The spinner line: what is happening, for how long, and the tail of a running command."""

    def __init__(self) -> None:
        self.spinner = Spinner("dots", style=ACCENT)
        self.status = "Thinking"
        self.since = time.monotonic()
        self.tail: list[str] = []

    def __rich__(self):
        waited = time.monotonic() - self.since
        text = Text(f"{self.status}…", style=MUTED)
        if waited >= 2:
            text.append(f" {fmt_duration(waited)}", style=MUTED)
        if waited >= 4:
            text.append(" · ctrl+c to stop", style=MUTED)
        self.spinner.update(text=text)
        if not self.tail:
            return self.spinner
        width = max(20, console.width - 8)
        return Group(self.spinner, *[Text("     " + line[:width], style=MUTED) for line in self.tail])


class Renderer:
    """Streams Markdown: finished blocks are printed for good, the open block stays live."""

    def __init__(self) -> None:
        self.buffer = ""
        self.live: Live | None = None
        self.busy = _Busy()
        self.thinking = ""                      # reasoning text of the step in progress
        self.thinking_since: float | None = None

    def _renderable(self):
        return Markdown(self.buffer) if self.buffer.strip() else self.busy

    def start(self, status: str | None = None) -> None:
        if status and status != self.busy.status:
            self.busy.status, self.busy.since, self.busy.tail = status, time.monotonic(), []
        if self.live is None:
            self.live = Live(self._renderable(), console=console, refresh_per_second=12, transient=True,
                             vertical_overflow="crop")
            self.live.start()
        else:
            self.live.update(self._renderable())

    def restart(self, status: str) -> None:
        """Like start(), but the clock starts again even if the status text is the same."""
        self.busy.status, self.busy.since, self.busy.tail = status, time.monotonic(), []
        self.start()

    def output(self, line: str) -> None:
        """One more line from a running command."""
        self.busy.tail = [*self.busy.tail, line][-LIVE_OUTPUT_LINES:]

    def progress(self, status: str) -> None:
        """Change the spinner text without restarting its clock (a tool call being written)."""
        self.busy.status = status
        self.start()

    def thought(self, text: str) -> None:
        """One more piece of the model's thinking: its last lines scroll under the spinner."""
        if self.thinking_since is None:
            self.thinking_since = time.monotonic()
            self.busy.status = "Thinking"
        self.thinking += text
        width = max(20, console.width - 8)
        rows = [row[i:i + width] for row in self.thinking.splitlines() if row.strip() for i in range(0, len(row), width)]
        self.busy.tail = rows[-LIVE_THOUGHT_LINES:]
        self.start()

    def end_thought(self, verbose: bool) -> None:
        """The model stopped thinking: leave one line behind, or the whole text in transcript view."""
        text, began = self.thinking.strip(), self.thinking_since
        self.thinking, self.thinking_since, self.busy.tail = "", None, []
        if not text:
            return
        self.stop()
        took = fmt_duration(time.monotonic() - (began or time.monotonic()))
        if verbose:
            console.print(Text(f"✻ Thinking ({took})", style=f"italic {ACCENT}"))
            console.print(Padding(Text(text, style=f"italic {MUTED}"), (0, 0, 0, 2)))
        else:
            console.print(Text(f"✻ Thought for {took}", style=f"italic {MUTED}"))

    def token(self, text: str) -> None:
        self.buffer += text
        cut = _last_block_end(self.buffer)
        if cut > 0:
            done, self.buffer = self.buffer[:cut], self.buffer[cut:]
            if done.strip():
                console.print(Markdown(done))
        self.start()

    def flush(self) -> None:
        text, self.buffer = self.buffer, ""
        self.stop()
        if text.strip():
            console.print(Markdown(text))

    def stop(self) -> None:
        if self.live is not None:
            self.live.stop()
            self.live = None


# ---------------------------------------------------------------- sessions and logs
def sessions_table(rows: list[dict], current: str = "") -> Table:
    t = Table(box=box.SIMPLE_HEAD, header_style=MUTED, padding=(0, 1))
    room = max(16, min(48, console.width - 64))  # what is left for the title on a narrow terminal
    t.add_column("#", justify="right", style=ACCENT, min_width=2, no_wrap=True)
    t.add_column("id", style=MUTED, min_width=8, no_wrap=True)
    t.add_column("when", style=MUTED, min_width=8, no_wrap=True)
    t.add_column("title", overflow="ellipsis", no_wrap=True, max_width=room)
    t.add_column("folder", style=MUTED, overflow="ellipsis", no_wrap=True, max_width=24)
    t.add_column("msgs", justify="right", style=MUTED, min_width=4, no_wrap=True)
    for i, row in enumerate(rows, start=1):
        mark = " ◀" if row["id"] == current else ""
        t.add_row(str(i), row["id"][:8], fmt_ago(row["updated_at"]), (row["title"] or "(untitled)") + mark,
                  tail_path(row["cwd"], 24), str(row.get("messages", "")))
    return t


def recap(session, row: dict) -> None:
    """What a resumed conversation was about: its title and the last exchange."""
    from langchain_core.messages import AIMessage, HumanMessage

    from vesper.coder import text_of

    console.print(Text.assemble(("  ↺ Resumed ", ACCENT), (row["title"] or "(untitled)", "bold"),
                                (f"  · {len(session.history)} messages · {fmt_ago(row['updated_at'])}", MUTED)))
    last_user = next((m for m in reversed(session.history) if isinstance(m, HumanMessage)), None)
    last_answer = next((m for m in reversed(session.history) if isinstance(m, AIMessage) and text_of(m.content).strip()), None)
    for who, msg in (("you", last_user), ("vesper", last_answer)):
        if msg is None:
            continue
        body = text_of(msg.content).split("\n\n<file path=")[0].strip().splitlines()
        shown = " ".join(body[:4])[:300] + (" …" if len(body) > 4 or len(" ".join(body[:4])) > 300 else "")
        console.print(Text.assemble((f"  {who:>6}  ", MUTED), (shown, MUTED)))


def tool_detail(name: str, args: dict) -> Text | Syntax | None:
    """What a tool call was asked to do, in full: the command, the edit as a diff, the file written."""
    if name == "run_command":
        return Text("$ " + str(args.get("command", "")), style="bold")
    if name == "edit_file":
        diff = "\n".join([*("-" + row for row in str(args.get("old_string", "")).splitlines()),
                          *("+" + row for row in str(args.get("new_string", "")).splitlines())])
        return Syntax(diff, "diff", theme="ansi_dark", word_wrap=True)
    if name == "write_file":
        rows = str(args.get("content", "")).splitlines()
        body = "\n".join("+" + row for row in rows[:VERBOSE_OUTPUT_LINES])
        if len(rows) > VERBOSE_OUTPUT_LINES:
            body += f"\n… {len(rows) - VERBOSE_OUTPUT_LINES} more lines"
        return Syntax(body, "diff", theme="ansi_dark", word_wrap=True)
    return None


def print_transcript(turns: list[dict]) -> None:
    """The whole conversation in detail: prompts, thinking, every tool call with its input and output."""
    for turn in turns:
        console.print()
        if turn["role"] == "user":
            console.print(Text.assemble(("❯ ", f"bold {ACCENT}"), (turn["text"], "bold")))
            continue
        for part in turn["parts"]:
            if part["type"] == "reasoning":
                console.print(Text("✻ Thinking", style=f"italic {ACCENT}"))
                console.print(Padding(Text(part["text"], style=f"italic {MUTED}"), (0, 0, 0, 2)))
            elif part["type"] == "text":
                console.print(Markdown(part["text"]))
            else:
                console.print(tool_start_line(part["name"], part["args"]))
                detail = tool_detail(part["name"], part["args"])
                if detail is not None:
                    console.print(Padding(detail, (0, 0, 0, 5)))
                if part["output"] is None:
                    console.print(Text("  ⎿  (stopped before it finished)", style=WARN))
                else:
                    console.print(tool_end_line(part["name"], part["output"], bool(part["ok"]), None, verbose=True))


def print_events(events: list[dict], full: bool = False, out: Console | None = None) -> None:
    """The activity log, one line per event; `full` adds arguments and output."""
    out = out or console
    for e in events:
        when = dt.datetime.fromtimestamp(e["ts"]).strftime("%H:%M:%S")
        d, kind = e["detail"], e["kind"]
        line = Text(f"  {when}  ", style=MUTED)
        if kind == "prompt":
            line.append("❯ ", style=f"bold {ACCENT}")
            line.append(" ".join(str(d.get("text", "")).split())[:160])
        elif kind == "tool":
            line.append("● ", style=ACCENT)
            line.append(TOOL_LABELS.get(e["name"], e["name"]), style="bold")
            line.append("  " + summarize_args(e["name"], d.get("args", {})), style=MUTED)
            line.append("  ✓" if e["ok"] else "  ✗", style=OK if e["ok"] else ERR)
            if e["ms"] is not None:
                line.append(f" {e['ms']} ms", style=MUTED)
        elif kind == "usage":
            line.append(turn_footer(d, 0).plain.strip(), style=MUTED)
        elif kind == "cwd":
            line.append("→ now in " + tail_path(d.get("path", ""), 70), style=ACCENT_2)
        elif kind == "error":
            line.append("✗ " + str(d.get("error", ""))[:300], style=ERR)
        elif kind == "interrupt":
            line.append("■ stopped by the user", style=WARN)
        else:
            line.append(f"{kind} {e['name']}".strip(), style=MUTED)
        out.print(line)
        if full and kind == "tool":
            args = {k: (v if len(str(v)) <= 400 else str(v)[:400] + "…") for k, v in d.get("args", {}).items()}
            out.print(Text(f"            args    {args}", style=MUTED))
            rows = str(d.get("output", "")).splitlines()
            for row in rows[:VERBOSE_OUTPUT_LINES]:
                out.print(Text("            " + row[:200], style=MUTED))
            if len(rows) > VERBOSE_OUTPUT_LINES:
                out.print(Text(f"            … {len(rows) - VERBOSE_OUTPUT_LINES} more lines", style=MUTED))
