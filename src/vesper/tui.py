"""Vesper in the terminal.

    vesper                      start a session in the current folder
    vesper -c                   continue the last conversation in this folder
    vesper -r [ID]              resume a conversation (pick from a list, or give its id)
    vesper "question"           ask once and print the answer (no prompts; edits are refused)
    vesper sessions             list saved conversations
    vesper logs [ID]            show what the agent did in a conversation
    vesper app                  open the desktop app
    vesper ask|agent|chat|eval  document question answering from the command line

Inside a session: type a request, `/` for commands, `@path` to attach a file, `!cmd` to run
a shell command yourself, Shift+Tab to change how much Vesper may do without asking.

This file is the session loop. Looks live in `ui.py`, setup questions in `onboarding.py`,
the agent in `coder.py`, its tools in `workspace.py`, saved history in `store.py`.
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import re
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from rich import box
from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from vesper import __version__
from vesper.onboarding import (
    ask_key,
    first_run,
    home,
    load_config,
    open_app,
    pick_model,
    provider_ready,
    save_config,
    trust_folder,
)
from vesper.ui import (
    ACCENT,
    ACCENT_2,
    AUTO_DIFF_LINES,
    ERR,
    MODES,
    MUTED,
    OK,
    TOOL_LABELS,
    WARN,
    Renderer,
    banner,
    console,
    diff_panel,
    err_console,
    error_panel,
    fmt_tokens,
    model_label,
    note,
    print_events,
    print_transcript,
    recap,
    sessions_table,
    short_path,
    summarize_args,
    summarize_output,
    tail_path,
    tool_end_line,
    tool_start_line,
    turn_footer,
)

__all__ = ["main", "App", "console", "banner", "TOOL_LABELS", "summarize_args", "summarize_output",
           "ACCENT", "MUTED", "OK", "ERR", "WARN", "load_config"]

SLASH = {
    "/help": "show commands and shortcuts",
    "/cd": "go to another folder: /cd ../other-project",
    "/new": "start a new conversation (the old one is saved)",
    "/resume": "go back to an earlier conversation",
    "/sessions": "list saved conversations",
    "/logs": "what the agent did in this conversation (/logs full for details)",
    "/verbose": "transcript view: show thinking and full tool output while working (ctrl+o)",
    "/transcript": "print this whole conversation in detail: thinking, commands, edits, output",
    "/status": "model, folder, session and token use",
    "/model": "switch provider and model",
    "/key": "set the API key for the current provider",
    "/mode": "ask | auto-edit | yolo",
    "/docs": "re-read documents for search_docs",
    "/app": "open the desktop app",
    "/exit": "quit (or ctrl+d)",
}
ALIASES = {"/clear": "/new", "/quit": "/exit", "/q": "/exit", "/history": "/sessions", "/log": "/logs"}
SHORTCUTS = [
    ("@path", "attach a file to your message"),
    ("!command", "run a shell command yourself (!cd folder moves Vesper too)"),
    ("shift+tab", "cycle mode: ask → auto-edit → yolo"),
    ("ctrl+o", "transcript view on or off (thinking and full tool output)"),
    ("esc then enter", "new line"),
    ("ctrl+c", "stop the answer · twice to quit"),
    ("↑ ↓", "previous messages"),
]
MAX_MENTION_CHARS = 60_000
MAX_PIPED_CHARS = 200_000  # text piped into one-shot mode
STDIN_WAIT_S = 2.0         # how long one-shot mode waits for piped text when it already has a question
MENTION = re.compile(r"(?<![\w/])@([\w./~\-]+)")


def open_store():
    """The session database, or None when it cannot be opened (Vesper still works, without history)."""
    from vesper.store import Store

    try:
        return Store()
    except Exception as exc:  # noqa: BLE001
        err_console.print(f"Session history is off: cannot open {home() / 'vesper.db'} ({exc}).", style=WARN)
        return None


def make_llm(cfg: dict):
    from vesper import providers as prov

    return prov.get_llm(cfg.get("provider"), cfg.get("model") or None)


def context_tokens(cfg: dict) -> int:
    from vesper import providers as prov

    return prov.context_tokens(cfg.get("provider"))


def friendly(exc: BaseException, cfg: dict) -> str:
    from vesper.server import friendly_error

    msg = friendly_error(exc, cfg.get("provider", ""))
    msg = msg.replace("Open Settings and paste a valid key", "Run /key to paste a valid key")
    return msg.replace("Pick another one in the model menu", "Run /model to pick another one")


# ---------------------------------------------------------------- the session
class App:
    """One terminal session. The agent runs in a worker thread; this thread draws and asks."""

    def __init__(self, root: Path, mode: str, cfg: dict, store=None) -> None:
        self.cfg = cfg
        self.mode = mode
        self.store = store
        self.verbose = bool(cfg.get("verbose"))
        self.renderer = Renderer()
        self.workspace = None
        self.session = None
        self._start_root = root
        self._events: queue.Queue = queue.Queue()
        self._stop = threading.Event()
        self._worker: threading.Thread | None = None
        self._shown_cwd = root

    @property
    def root(self) -> Path:
        return self.workspace.root if self.workspace is not None else self._start_root

    def build(self, resume: dict | None = None) -> None:
        from vesper import providers as prov
        from vesper.workspace import Workspace

        emb = self.cfg.get("embeddings") or "local"

        def embeddings():
            self._events.put(("status", {"text": "Reading the documents in this folder (first search only)"}))
            return prov.get_embeddings(emb)

        self.workspace = Workspace(
            self._start_root, approve=self._approve_from_tool, mode=self.mode, embeddings_factory=embeddings,
            on_auto=lambda action: self._events.put(("auto", action)),
            on_output=lambda line: self._events.put(("output", {"line": line})),
        )
        self.new_session(resume)

    def new_session(self, resume: dict | None = None) -> None:
        from vesper.coder import Session

        self.session = Session(
            make_llm(self.cfg), self.workspace, self.store, session_id=resume["id"] if resume else None,
            source="terminal", provider=self.cfg.get("provider", ""), model=self.cfg.get("model", ""),
            context_tokens=context_tokens(self.cfg),
        )

    # ----- approvals -----
    def _approve_from_tool(self, action: dict) -> str:
        """Called by a tool, in the agent's thread: hand the question to the main thread and wait."""
        if self._stop.is_set():
            return "no"
        reply: queue.Queue = queue.Queue(maxsize=1)
        self._events.put(("approval", {"action": action, "reply": reply}))
        return reply.get()

    def approve(self, action: dict) -> str:
        self.renderer.stop()
        strict = action.get("strict")
        if action["kind"] == "edit":
            title = ("Create " if action.get("new") else "Edit ") + action.get("path", "")
            console.print(diff_panel(action, title, ERR if strict else WARN))
        else:
            body = Group(Text("$ " + action.get("command", ""), style="bold"),
                         Text("in " + short_path(action.get("cwd", self.root)), style=MUTED))
            console.print(Panel(body, title="Run a command", title_align="left",
                                border_style=ERR if strict else WARN, box=box.ROUNDED))
        if strict:
            note("This can do serious damage or touches credentials, so it needs a yes every time.", ERR)
        what = "edits" if action["kind"] == "edit" else "commands"
        try:
            while True:
                parts = [("  Allow?  ", "bold"), ("y", f"bold {OK}"), (" yes  ", MUTED)]
                if not strict:
                    parts += [("a", f"bold {ACCENT}"), (f" always allow {what} this session  ", MUTED)]
                parts += [("n", f"bold {ERR}"), (" no  ", MUTED), ("[n] " if strict else "[y] ", MUTED)]
                console.print(Text.assemble(*parts), end="")
                answer = input().strip().lower()
                if answer in ("y", "yes") or (answer == "" and not strict):
                    return "yes"
                if answer in ("a", "always") and not strict:
                    return "always"
                if answer in ("n", "no", ""):
                    return "no"
        except (KeyboardInterrupt, EOFError):
            console.print()
            return "no"
        finally:
            self.renderer.restart("Working")

    # ----- one turn -----
    def run_turn(self, text: str, shown: str | None = None) -> None:
        self.wait_idle()
        events = self._events = queue.Queue()
        stop = self._stop = threading.Event()
        session = self.session

        def work() -> None:
            gen = session.ask(text, shown=shown)
            try:
                for item in gen:
                    if stop.is_set():
                        break
                    events.put(item)
            except BaseException as exc:  # noqa: BLE001 - shown to the user by the main thread
                events.put(("error", {"exc": exc}))
            finally:
                gen.close()
                events.put(("end", {}))

        self._worker = threading.Thread(target=work, daemon=True, name="vesper-turn")
        self._worker.start()
        r = self.renderer
        r.restart("Thinking")
        try:
            while True:
                try:
                    event, data = events.get(timeout=0.2)
                except queue.Empty:
                    continue
                if event == "end":
                    break
                self.draw(event, data)
        except KeyboardInterrupt:
            stop.set()
            self.workspace.cancel()
            r.end_thought(self.verbose)
            r.flush()
            note("⎿  Stopped. Tell Vesper what to do instead.", WARN)
        finally:
            r.flush()

    def draw(self, event: str, data: dict) -> None:
        r = self.renderer
        if event not in ("reasoning", "output"):
            r.end_thought(self.verbose)
        if event == "token":
            r.token(data["text"])
        elif event == "reasoning":
            r.flush()
            r.thought(data["text"])
        elif event == "tool_draft":  # the model is still writing the call: say which one, and how far
            r.flush()
            label = TOOL_LABELS.get(data["name"], data["name"])
            r.progress(f"Preparing {label} ({fmt_tokens(data['chars'])} characters)")
        elif event == "tool_start":
            r.flush()
            console.print(tool_start_line(data["name"], data["args"]))
            if self.verbose and data["name"] == "run_command":
                console.print(Text("     $ " + str(data["args"].get("command", "")), style=MUTED))
            r.restart(TOOL_LABELS.get(data["name"], data["name"]))
        elif event == "tool_end":
            r.stop()
            console.print(tool_end_line(data["name"], data["output"], data["ok"], data.get("ms"), self.verbose))
            r.restart("Thinking")
        elif event == "output":
            r.output(data["line"])
        elif event == "approval":
            data["reply"].put(self.approve(data["action"]))
        elif event == "auto":
            if data["kind"] == "edit":  # the edit did not need a yes; still show what changed
                r.stop()
                title = ("Created " if data.get("new") else "Edited ") + data.get("path", "")
                console.print(diff_panel(data, title, MUTED, None if self.verbose else AUTO_DIFF_LINES))
                r.start()
        elif event == "status":
            r.restart(data["text"])
        elif event == "cwd":
            if Path(data["path"]) != self._shown_cwd:
                self._shown_cwd = Path(data["path"])
                r.stop()
                note("→ now in " + tail_path(data["path"], 70), ACCENT_2)
                r.start()
        elif event == "notice":
            r.stop()
            note(data["text"], WARN)
            r.start()
        elif event == "usage":
            r.flush()
            console.print(turn_footer(data, self.session.context_tokens))
        elif event == "error":
            r.flush()
            self.show_error(data["exc"])

    def wait_idle(self) -> None:
        """A stopped request may still be finishing its last model call; let it end first."""
        worker = self._worker
        if worker is None or not worker.is_alive():
            return
        with console.status(Text("Finishing the stopped request…", style=MUTED), spinner="dots", spinner_style=ACCENT):
            while worker.is_alive():
                try:
                    event, data = self._events.get(timeout=0.2)
                except queue.Empty:
                    continue
                if event == "approval":
                    data["reply"].put("no")

    def show_error(self, exc: BaseException) -> None:
        error_panel(friendly(exc, self.cfg), "/model switches provider or model · /key sets the API key")

    # ----- input -----
    def _complete_path(self, frag: str, only_dirs: bool = False):
        """Entries that complete a path typed so far (relative to the working directory, absolute or ~)."""
        from prompt_toolkit.completion import Completion

        head, _, prefix = frag.rpartition("/")
        head = head + "/" if "/" in frag else ""
        try:
            base = self.workspace.resolve(head or ".")
            entries = sorted(base.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except OSError:
            return
        shown = 0
        for p in entries:
            if not p.name.startswith(prefix) or (p.name.startswith(".") and not prefix.startswith(".")):
                continue
            if only_dirs and not p.is_dir():
                continue
            yield Completion(head + p.name + ("/" if p.is_dir() else ""), start_position=-len(frag))
            shown += 1
            if shown >= 200:
                return

    def make_prompt(self):
        from prompt_toolkit import PromptSession
        from prompt_toolkit.completion import Completer, Completion
        from prompt_toolkit.formatted_text import HTML
        from prompt_toolkit.history import FileHistory, InMemoryHistory
        from prompt_toolkit.key_binding import KeyBindings
        from prompt_toolkit.styles import Style

        app = self

        class Complete(Completer):
            def get_completions(self, document, complete_event):
                text = document.text_before_cursor
                if text.startswith("/") and " " not in text:
                    for cmd, desc in SLASH.items():
                        if cmd.startswith(text):
                            yield Completion(cmd, start_position=-len(text), display_meta=desc)
                    return
                moving = re.match(r"^(/cd|!cd)\s+(\S*)$", text)
                if moving:
                    yield from app._complete_path(moving.group(2), only_dirs=True)
                    return
                m = re.search(r"@([\w./~\-]*)$", text)
                if m:
                    yield from app._complete_path(m.group(1))

        kb = KeyBindings()

        @kb.add("s-tab")
        def _(event):
            order = list(MODES)
            app.set_mode(order[(order.index(app.mode) + 1) % len(order)], quiet=True)
            event.app.invalidate()

        @kb.add("c-o")
        def _(event):
            app.set_verbose(not app.verbose, quiet=True)
            event.app.invalidate()

        @kb.add("escape", "enter")
        def _(event):
            event.current_buffer.insert_text("\n")

        @kb.add("c-j")
        def _(event):
            event.current_buffer.insert_text("\n")

        def esc(text: str) -> str:
            return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

        def toolbar():
            color = {"ask": "ansigreen", "auto-edit": "ansiyellow", "yolo": "ansired"}[app.mode]
            folder = tail_path(app.root, 34)
            used = app.session.tokens["context"] if app.session else 0
            ctx = f"  ·  context {min(100, round(100 * used / app.session.context_tokens))}%" if used else ""
            loud = "  ·  <style fg='ansicyan'>transcript</style>" if app.verbose else ""
            return HTML(f" <b>{esc(folder)}</b>  ·  {esc(model_label(app.cfg))}  ·  "
                        f"<style fg='{color}'><b>{app.mode}</b></style> <style fg='ansigray'>(shift+tab)</style>"
                        f"{ctx}{loud}  ·  <style fg='ansigray'>/help</style>")

        try:
            home().mkdir(parents=True, exist_ok=True)
            history = FileHistory(str(home() / "history"))
        except OSError:
            history = InMemoryHistory()
        return PromptSession(
            message=HTML(f"<style fg='{ACCENT}'><b>❯</b></style> "),
            history=history, completer=Complete(), complete_while_typing=True, key_bindings=kb,
            bottom_toolbar=toolbar, enable_history_search=False,
            style=Style.from_dict({"bottom-toolbar": "noreverse"}),
        )

    def set_mode(self, mode: str, quiet: bool = False) -> None:
        self.mode = mode
        if self.workspace is not None:
            self.workspace.mode = mode
            self.workspace.always.clear()
        self._remember({"agent_mode": mode})
        if not quiet:
            console.print(Text.assemble(("  mode: ", MUTED), (mode, "bold"), (f" · {MODES[mode]}", MUTED)))

    def set_verbose(self, on: bool, quiet: bool = False) -> None:
        self.verbose = on
        self._remember({"verbose": on})
        if not quiet:
            note("transcript view on: thinking and full tool output are shown." if on
                 else "transcript view off: thinking and tool output are summarized.")

    @staticmethod
    def _remember(patch: dict) -> None:
        try:
            save_config(patch)
        except OSError:
            pass

    def expand_mentions(self, text: str) -> str:
        """`@path` attaches that file to the message."""
        from vesper.workspace import ToolError

        attached = []
        for m in MENTION.finditer(text):
            ref = m.group(1).rstrip(".,;:")
            try:
                if not self.workspace.resolve(ref).is_file():
                    continue
                body = self.workspace.read_file(ref, 1, 2000)
            except ToolError as exc:
                note(f"⎿  {ref} not attached: {exc}", WARN)
                continue
            except Exception:  # noqa: BLE001
                continue
            attached.append(f'<file path="{ref}">\n{body[:MAX_MENTION_CHARS]}\n</file>')
            console.print(Text.assemble(("  ⎿  attached ", MUTED), (ref, ACCENT)))
        return text + ("\n\n" + "\n\n".join(attached) if attached else "")

    # ----- moving around -----
    def cd(self, target: str) -> None:
        from vesper.workspace import ToolError

        if not target.strip():
            note(short_path(self.root))
            return
        try:
            self.workspace.change_directory(target.strip())
        except ToolError as exc:
            note(str(exc), ERR)
            return
        self._shown_cwd = self.root
        note("→ now in " + tail_path(self.root, 70), ACCENT_2)

    # ----- history -----
    def show_sessions(self) -> list[dict]:
        rows = self.store.list_sessions(15) if self.store is not None else []
        if not rows:
            note("No saved conversations yet.")
        else:
            console.print(sessions_table(rows, self.session.id))
        return rows

    def resume(self, ref: str) -> None:
        if self.store is None:
            note("Session history is off.", WARN)
            return
        ref = ref.strip()
        rows = self.store.list_sessions(15)
        if not ref:
            if not self.show_sessions():
                return
            try:
                ref = input("  number or id (enter to cancel): ").strip()
            except (KeyboardInterrupt, EOFError):
                console.print()
                return
            if not ref:
                return
        row = rows[int(ref) - 1] if ref.isdigit() and 1 <= int(ref) <= len(rows) else self.store.get_session(ref)
        if row is None:
            note(f"No conversation matches {ref!r}. /sessions lists them.", WARN)
            return
        self.open_session(row)

    def open_session(self, row: dict) -> None:
        self.wait_idle()
        folder = Path(row["cwd"])
        if folder.is_dir() and folder != self.root:
            self.workspace.change_directory(str(folder))
        elif not folder.is_dir():
            note(f"Its folder {short_path(folder)} is gone; staying in {short_path(self.root)}.", WARN)
        self.new_session(row)
        self._shown_cwd = self.root
        recap(self.session, row)
        note("in " + tail_path(self.root, 70))

    def show_logs(self, arg: str) -> None:
        words = arg.split()
        limit = next((int(w) for w in words if w.isdigit()), 40)
        events = self.store.events(self.session.id, limit) if self.store is not None else []
        if not events:
            note("Nothing has happened in this conversation yet.")
            return
        print_events(events, full="full" in words or self.verbose)
        note(f"session {self.session.id} · stored in {tail_path(self.store.path, 50)}")

    def show_status(self) -> None:
        s = self.session
        t = Table.grid(padding=(0, 2))
        t.add_column(style=MUTED, justify="right")
        t.add_column()
        t.add_row("version", __version__)
        t.add_row("model", model_label(self.cfg))
        t.add_row("folder", tail_path(self.root, 70))
        t.add_row("mode", f"{self.mode} · {MODES[self.mode]}")
        t.add_row("session", f"{s.id} · {len(s.history)} messages" + (f" · {s.title}" if s.title else ""))
        t.add_row("tokens", f"{fmt_tokens(s.tokens['input'])} in, {fmt_tokens(s.tokens['output'])} out")
        t.add_row("context", f"{fmt_tokens(s.tokens['context'])} of {fmt_tokens(s.context_tokens)} "
                             f"({min(100, round(100 * s.tokens['context'] / s.context_tokens))}%)")
        t.add_row("history", tail_path(self.store.path, 70) if self.store is not None else "off")
        console.print(Panel(t, title="status", title_align="left", border_style=ACCENT, box=box.ROUNDED, expand=False))

    # ----- slash commands -----
    def command(self, line: str) -> bool:
        """Runs a slash command. Returns False to quit."""
        cmd, _, arg = line.strip().partition(" ")
        cmd = ALIASES.get(cmd.lower(), cmd.lower())
        arg = arg.strip()
        if cmd == "/exit":
            return False
        if cmd == "/help":
            t = Table(box=None, padding=(0, 2), show_header=False)
            t.add_column(style=f"bold {ACCENT}")
            t.add_column(style=MUTED)
            for c, d in SLASH.items():
                t.add_row(c, d)
            t.add_row("", "")
            for k, d in SHORTCUTS:
                t.add_row(k, d)
            console.print(Panel(t, title="help", title_align="left", border_style=ACCENT, box=box.ROUNDED, expand=False))
        elif cmd == "/cd":
            self.cd(arg)
        elif cmd == "/new":
            self.wait_idle()
            self.session.clear()
            console.clear()
            banner(self.root, self.cfg, self.mode)
        elif cmd == "/resume":
            self.resume(arg)
        elif cmd == "/sessions":
            if self.show_sessions():
                note("/resume NUMBER goes back to one.")
        elif cmd == "/logs":
            self.show_logs(arg)
        elif cmd == "/verbose":
            self.set_verbose(not self.verbose)
        elif cmd == "/transcript":
            from vesper.coder import transcript

            if self.session.history:
                print_transcript(transcript(self.session.history))
            else:
                note("Nothing has happened in this conversation yet.")
        elif cmd == "/status":
            self.show_status()
        elif cmd == "/mode":
            if arg in MODES:
                self.set_mode(arg)
            else:
                note("/mode ask | auto-edit | yolo")
        elif cmd == "/model":
            if pick_model(self.cfg):
                self._reload_llm()
        elif cmd == "/key":
            if ask_key(self.cfg.get("provider", "")):
                self._reload_llm()
        elif cmd == "/docs":
            self.workspace.forget_index()
            n = len(self.workspace.doc_files())
            note(f"{n} document{'s' if n != 1 else ''} (PDF, Markdown, text) will be searched.")
        elif cmd == "/app":
            open_app()
        else:
            note(f"Unknown command {cmd}. Type /help.", WARN)
        return True

    def _reload_llm(self) -> None:
        try:
            self.wait_idle()
            self.session.set_llm(make_llm(self.cfg), self.cfg.get("provider", ""), self.cfg.get("model", ""),
                                 context_tokens(self.cfg))
            console.print(Text.assemble(("  model: ", MUTED), (model_label(self.cfg), "bold")))
        except Exception as exc:  # noqa: BLE001
            self.show_error(exc)

    def shell(self, command: str) -> None:
        """`!command`: typed by the user, so it runs without an approval prompt."""
        command = command.strip()
        if not command:
            return
        moved = re.fullmatch(r"cd(\s+(?P<to>.+))?", command)
        if moved:  # a child shell's `cd` would be lost; move Vesper itself
            self.cd(moved.group("to") or "~")
            return
        try:
            proc = subprocess.run(command, shell=True, cwd=self.root)
            if proc.returncode:
                note(f"exit code {proc.returncode}", WARN)
        except KeyboardInterrupt:
            note("stopped", WARN)

    # ----- main loop -----
    def loop(self) -> None:
        prompt = self.make_prompt()
        last_interrupt = 0.0
        while True:
            try:
                line = prompt.prompt()
            except KeyboardInterrupt:
                if time.monotonic() - last_interrupt < 2:
                    break
                last_interrupt = time.monotonic()
                note("Press Ctrl+C again to quit.")
                continue
            except EOFError:
                break
            line = line.strip()
            if not line:
                continue
            word = line.split()[0].lower()
            # "/Users/me/notes.txt what is this?" is a message about a path, not a command.
            if line.startswith("/") and (word in SLASH or word in ALIASES or not Path(line.split()[0]).exists()):
                if not self.command(line):
                    break
                continue
            if line.startswith("!"):
                self.shell(line[1:])
                continue
            console.print()
            self.run_turn(self.expand_mentions(line), shown=line)
            console.print()
        if self.session is not None and self.session.history and self.store is not None:
            note(f"Saved. Continue later with: vesper -r {self.session.id[:8]}")
        console.print(Text("  See you ✦", style=ACCENT))


# ---------------------------------------------------------------- one-shot mode
def print_mode(root: Path, question: str, cfg: dict, mode: str, store=None, resume: dict | None = None) -> int:
    """`vesper "question"`: the answer on stdout, tool activity on stderr, no prompts.

    Nobody can answer an approval prompt here, so in "ask" mode edits and commands are refused.
    """
    from vesper import providers as prov
    from vesper.coder import Session
    from vesper.workspace import Workspace

    if not prov.fake_llm_enabled() and (not cfg.get("provider") or not provider_ready(cfg["provider"])):
        err_console.print("No model is set up yet. Run `vesper` once to choose one.", style=ERR)
        return 2
    ws = Workspace(root, mode=mode,
                   embeddings_factory=lambda: prov.get_embeddings(cfg.get("embeddings") or "local"))

    def on_sigint(signum, frame):  # stop a running command first, or it would outlive us
        ws.cancel()
        raise KeyboardInterrupt

    previous = signal.signal(signal.SIGINT, on_sigint)
    answer = []
    try:
        session = Session(make_llm(cfg), ws, store, session_id=resume["id"] if resume else None, source="print",
                          provider=cfg.get("provider", ""), model=cfg.get("model", ""),
                          context_tokens=context_tokens(cfg))
        for event, data in session.ask(question):
            if event == "token":
                answer.append(data["text"])
            elif event == "tool_start":
                if answer and not answer[-1].endswith("\n"):
                    answer.append("\n\n")  # text before and after a tool call are separate paragraphs
                err_console.print(tool_start_line(data["name"], data["args"]).plain, style=MUTED)
            elif event == "tool_end" and not data["ok"]:
                err_console.print(tool_end_line(data["name"], data["output"], False, data.get("ms")).plain, style=MUTED)
            elif event == "notice":
                err_console.print(data["text"], style=WARN)
    except KeyboardInterrupt:
        err_console.print("Stopped.", style=WARN)
        return 130
    except Exception as exc:  # noqa: BLE001
        err_console.print(friendly(exc, cfg), style=ERR)
        return 1
    finally:
        signal.signal(signal.SIGINT, previous)
    print("".join(answer).strip())
    return 0


# ---------------------------------------------------------------- `vesper sessions` and `vesper logs`
def sessions_command(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="vesper sessions", description="List saved conversations, newest first.")
    ap.add_argument("-n", type=int, default=20, help="how many to show (default 20)")
    ap.add_argument("--here", action="store_true", help="only conversations in the current folder")
    ap.add_argument("--json", action="store_true", help="print JSON instead of a table")
    args = ap.parse_args(argv)
    store = open_store()
    if store is None:
        return 1
    rows = store.list_sessions(args.n, Path.cwd().resolve() if args.here else None)
    if args.json:
        print(json.dumps(rows, indent=2))
    elif not rows:
        note("No saved conversations yet. Start one with: vesper")
    else:
        console.print(sessions_table(rows))
        note("vesper -r ID resumes one · vesper logs ID shows what the agent did")
    return 0


def logs_command(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="vesper logs", description="Show what the agent did in a conversation.")
    ap.add_argument("id", nargs="?", help="session id or its first characters (default: the latest conversation)")
    ap.add_argument("-n", type=int, default=200, help="how many events to show (default 200)")
    ap.add_argument("--full", action="store_true", help="include tool arguments and output")
    ap.add_argument("--json", action="store_true", help="print one JSON object per line")
    args = ap.parse_args(argv)
    store = open_store()
    if store is None:
        return 1
    row = store.get_session(args.id) if args.id else store.latest()
    if row is None:
        err_console.print(f"No conversation matches {args.id!r}." if args.id else "No saved conversations yet.", style=ERR)
        return 1
    events = store.events(row["id"], args.n)
    if args.json:
        for e in events:
            print(json.dumps(e, ensure_ascii=False))
        return 0
    console.print(Text.assemble(("  " + (row["title"] or "(untitled)"), "bold"),
                                (f"  · {row['id']} · {tail_path(row['cwd'], 40)} · {row['model'] or 'model unknown'}", MUTED)))
    print_events(events, full=args.full)
    return 0


# ---------------------------------------------------------------- entry point
def read_piped(have_question: bool) -> str:
    """Text piped into `vesper "question"`, or "" when nothing is piped.

    A program that starts vesper with an open but silent stdin (a script, a CI step, another
    agent) would make a plain read() wait forever. So when the question is already on the
    command line, wait only STDIN_WAIT_S seconds for the first byte.
    """
    if sys.stdin is None or sys.stdin.isatty():
        return ""
    if have_question and os.name == "posix":
        import select

        try:
            ready, _, _ = select.select([sys.stdin], [], [], STDIN_WAIT_S)
        except (OSError, ValueError):  # stdin is not a real file (a test, an embedded interpreter)
            ready = [sys.stdin]
        if not ready:
            return ""
    return sys.stdin.read(MAX_PIPED_CHARS + 1).strip()


def pick_resume(store, ref: str | None, here: Path, interactive: bool) -> dict | None:
    """The session for `-c` (ref is None: the latest one in this folder) or `-r [ID]`."""
    if store is None:
        return None
    if ref is None:
        return store.latest(here)
    if ref:
        return store.get_session(ref)
    rows = store.list_sessions(15)
    if not rows or not interactive:
        return rows[0] if rows else None
    console.print(sessions_table(rows))
    try:
        raw = input("  number or id: ").strip()
    except (KeyboardInterrupt, EOFError):
        return None
    return rows[int(raw) - 1] if raw.isdigit() and 1 <= int(raw) <= len(rows) else store.get_session(raw)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    if argv and argv[0] in ("ask", "agent", "chat", "eval"):
        from vesper.cli import main as lab_main

        return lab_main(argv) or 0
    if argv and argv[0] == "app":
        open_app()
        return 0
    if argv and argv[0] == "sessions":
        return sessions_command(argv[1:])
    if argv and argv[0] == "logs":
        return logs_command(argv[1:])

    ap = argparse.ArgumentParser(
        prog="vesper", description="Vesper: an AI coding agent for your terminal.",
        epilog="More: vesper sessions · vesper logs [ID] · vesper app · vesper ask | agent | chat | eval",
    )
    ap.add_argument("prompt", nargs="*", help="ask once and print the answer instead of starting a session")
    ap.add_argument("-C", "--dir", default=".", help="folder to start in (default: the current folder)")
    ap.add_argument("-c", "--continue", dest="cont", action="store_true",
                    help="continue the last conversation in this folder")
    ap.add_argument("-r", "--resume", nargs="?", const="", metavar="ID",
                    help="resume a conversation by id (no id: pick from a list)")
    ap.add_argument("--mode", choices=list(MODES), help="ask (default), auto-edit or yolo")
    ap.add_argument("--model", help="provider/model, e.g. openai/gpt-4.1-mini or ollama/llama3.2")
    ap.add_argument("-v", "--version", action="version", version=f"vesper {__version__}")
    args = ap.parse_args(argv)

    root = Path(args.dir).expanduser().resolve()
    if not root.is_dir():
        err_console.print(f"{root} is not a folder.", style=ERR)
        return 2
    cfg = load_config()
    if args.model:
        from vesper import providers as prov

        pid, _, model = args.model.partition("/")
        if pid not in prov.PROVIDERS:
            err_console.print(f"Unknown provider {pid!r}. Choose one of: {', '.join(prov.PROVIDERS)}", style=ERR)
            return 2
        cfg.update(provider=pid, model=model)
    mode = args.mode or (cfg.get("agent_mode") if cfg.get("agent_mode") in MODES else "ask")

    piped = read_piped(bool(args.prompt))
    if len(piped) > MAX_PIPED_CHARS:
        piped = piped[:MAX_PIPED_CHARS] + "\n[... input cut off]"
    one_shot = bool(args.prompt or piped)

    store = open_store()
    resume = None
    if args.cont or args.resume is not None:
        resume = pick_resume(store, None if args.cont else args.resume, root, interactive=not one_shot)
        if resume is None:
            if args.resume:
                err_console.print(f"No conversation matches {args.resume!r}. See: vesper sessions", style=ERR)
                return 2
            err_console.print("No earlier conversation to continue; starting a new one.", style=MUTED)
        elif Path(resume["cwd"]).is_dir():
            root = Path(resume["cwd"])

    if one_shot:
        question = " ".join(args.prompt)
        if piped:  # `git diff | vesper "write a commit message"`: the piped text comes with the question
            question = f"{question}\n\n<stdin>\n{piped}\n</stdin>" if question else piped
        return print_mode(root, question, cfg, mode, store, resume)

    console.print()
    if not trust_folder(root):
        return 1
    if not first_run(cfg):
        note("No model chosen. Run vesper again when you are ready.")
        return 1
    app = App(root, mode, cfg, store)
    banner(root, cfg, mode)
    with console.status(Text("Starting…", style=MUTED), spinner="dots", spinner_style=ACCENT):
        try:
            app.build(resume)
        except Exception as exc:  # noqa: BLE001
            app.show_error(exc)
            return 1
    if resume:
        recap(app.session, resume)
    app.loop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
