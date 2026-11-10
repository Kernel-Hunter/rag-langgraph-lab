"""Vesper in the terminal.

    vesper                      start an agent session in the current folder
    vesper "question"           ask once and print the answer (no prompts; edits are refused)
    vesper app                  open the desktop app
    vesper ask|agent|chat|eval  the Chapter 5B lab commands (same as `rag-lab`)

Inside a session: type a request, `/` for commands, `@path` to attach a file, `!cmd` to run
a shell command yourself, Shift+Tab to change how much Vesper may do without asking.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from rich import box
from rich.console import Console, Group
from rich.live import Live
from rich.markdown import Markdown
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
}
TOOL_LABELS = {
    "list_files": "List", "read_file": "Read", "glob": "Find", "grep": "Search",
    "search_docs": "Search docs", "write_file": "Write", "edit_file": "Edit", "run_command": "Run",
}
SLASH = {
    "/help": "show commands and shortcuts",
    "/model": "switch provider and model",
    "/key": "set the API key for the current provider",
    "/mode": "ask | auto-edit | yolo",
    "/clear": "start a new conversation",
    "/docs": "re-read documents for search_docs",
    "/app": "open the desktop app",
    "/exit": "quit (or Ctrl+D)",
}
MAX_MENTION_CHARS = 60_000
MAX_PIPED_CHARS = 200_000  # text piped into one-shot mode

console = Console(highlight=False)
err_console = Console(stderr=True, highlight=False)


# ---------------------------------------------------------------- config shared with the desktop app
def _home() -> Path:
    from vesper import providers as prov

    return prov.HOME


def load_config() -> dict:
    try:
        data = json.loads((_home() / "config.json").read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_config(patch: dict) -> None:
    home = _home()
    home.mkdir(parents=True, exist_ok=True)
    data = {**load_config(), **patch}
    tmp = home / "config.json.tmp"
    tmp.write_text(json.dumps(data, indent=2))
    os.replace(tmp, home / "config.json")


def ollama_running() -> bool:
    import urllib.request

    try:
        with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=0.6):
            return True
    except Exception:
        return False


def provider_ready(pid: str) -> bool:
    from vesper import providers as prov

    p = prov.PROVIDERS.get(pid)
    if p is None:
        return False
    if prov.fake_llm_enabled():
        return True
    if p.id == "ollama":
        return ollama_running()
    if p.local:
        return True
    if p.id == "custom":
        return bool(os.getenv("CUSTOM_BASE_URL"))
    return bool(prov._key(p))


# ---------------------------------------------------------------- small UI helpers
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


def short_path(p: Path) -> str:
    home = Path.home()
    if p == home:
        return "~"
    try:
        return "~/" + p.relative_to(home).as_posix()
    except ValueError:
        return str(p)


def model_label(cfg: dict) -> str:
    from vesper import providers as prov

    p = prov.PROVIDERS.get(cfg.get("provider", ""))
    if p is None:
        return "no model"
    return f"{p.label} · {cfg.get('model') or p.chat_model}"


def banner(root: Path, cfg: dict, mode: str) -> None:
    title = Text.assemble(("✦ ", f"bold {ACCENT}"), gradient("Vesper"), (f"  v{__version__}", MUTED))
    rows = Table.grid(padding=(0, 2))
    rows.add_column(style=MUTED, justify="right")
    rows.add_column()
    rows.add_row("model", model_label(cfg))
    rows.add_row("folder", short_path(root))
    rows.add_row("mode", f"{mode} · {MODES[mode]}")
    tips = Text.assemble(
        ("Ask anything about this folder.  ", ""), ("/", f"bold {ACCENT}"), (" commands  ", MUTED),
        ("@file", f"bold {ACCENT}"), (" attach  ", MUTED), ("!cmd", f"bold {ACCENT}"), (" shell  ", MUTED),
        ("shift+tab", f"bold {ACCENT}"), (" mode", MUTED),
    )
    console.print(Panel(Group(title, Text(""), rows, Text(""), tips), box=box.ROUNDED,
                        border_style=ACCENT, padding=(1, 2), expand=False))


def error_panel(message: str, hint: str = "") -> None:
    body = Text(message)
    if hint:
        body.append("\n" + hint, style=MUTED)
    console.print(Panel(body, title="error", title_align="left", border_style=ERR, box=box.ROUNDED, expand=False))


def summarize_args(name: str, args: dict) -> str:
    key = {"read_file": "path", "write_file": "path", "edit_file": "path", "list_files": "path",
           "glob": "pattern", "grep": "pattern", "search_docs": "query", "run_command": "command"}.get(name)
    value = str(args.get(key, "")) if key else ""
    if name == "grep" and args.get("path") not in (None, "", "."):
        value += f"  in {args['path']}"
    value = value.replace("\n", " ")
    return value if len(value) <= 90 else value[:87] + "…"


def summarize_output(name: str, output: str, ok: bool) -> Text:
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
        n = 0 if output.startswith("No ") else len([line for line in lines if not line.startswith("...")])
        return Text(f"{n} result{'s' if n != 1 else ''}", style=MUTED)
    if name == "list_files":
        return Text(f"{max(0, len(lines) - 1)} entries", style=MUTED)
    if name == "search_docs":
        n = output.count("\n\n") + 1 if output.startswith("[") else 0
        return Text(f"{n} passage{'s' if n != 1 else ''}" if n else output[:120], style=MUTED)
    if name == "run_command":
        head = lines[0] if lines else ""
        rest = [line for line in lines[1:] if line.strip()][:6]
        t = Text(head, style=OK if head.startswith("exit code 0") else WARN)
        for line in rest:
            t.append("\n     " + line[:160], style=MUTED)
        if len(lines) > 7:
            t.append(f"\n     … {len(lines) - 7} more lines", style=MUTED)
        return t
    return Text(lines[0][:200] if lines else "done", style=MUTED)


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


class Renderer:
    """Streams Markdown: finished blocks are printed for good, the open block stays live."""

    def __init__(self) -> None:
        self.buffer = ""
        self.live: Live | None = None
        self.status = "Thinking"

    def _renderable(self):
        if self.buffer.strip():
            return Markdown(self.buffer)
        return Spinner("dots", text=Text(f"{self.status}…", style=MUTED), style=ACCENT)

    def start(self, status: str | None = None) -> None:
        if status:
            self.status = status
        if self.live is None:
            self.live = Live(self._renderable(), console=console, refresh_per_second=12, transient=True,
                             vertical_overflow="crop")
            self.live.start()
        else:
            self.live.update(self._renderable())

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


# ---------------------------------------------------------------- the session
class App:
    def __init__(self, root: Path, mode: str, cfg: dict) -> None:
        self.root = root
        self.cfg = cfg
        self.mode = mode
        self.renderer = Renderer()
        self.workspace = None
        self.session = None

    def build(self) -> None:
        from vesper import providers as prov
        from vesper.coder import Session
        from vesper.workspace import Workspace

        emb = self.cfg.get("embeddings") or "local"

        def embeddings():
            self.renderer.start("Reading the documents in this folder (first search only)")
            return prov.get_embeddings(emb)

        self.workspace = Workspace(self.root, approve=self.approve, mode=self.mode, embeddings_factory=embeddings)
        self.session = Session(self.llm(), self.workspace)

    def llm(self):
        from vesper import providers as prov

        return prov.get_llm(self.cfg.get("provider"), self.cfg.get("model") or None)

    # ----- approvals (called from inside a tool, while the answer is streaming) -----
    def approve(self, action: dict) -> str:
        self.renderer.stop()
        if action["kind"] == "edit":
            title = ("Create " if action.get("new") else "Edit ") + action.get("path", "")
            body = Syntax(action.get("diff") or "(no changes)", "diff", theme="ansi_dark", word_wrap=True)
        else:
            title = "Run a command"
            body = Group(Text("$ " + action.get("command", ""), style="bold"),
                         Text("in " + short_path(Path(action.get("cwd", self.root))), style=MUTED))
        console.print(Panel(body, title=title, title_align="left", border_style=WARN, box=box.ROUNDED))
        what = "edits" if action["kind"] == "edit" else "commands"
        try:
            while True:
                console.print(Text.assemble(
                    ("  Allow?  ", "bold"), ("y", f"bold {OK}"), (" yes  ", MUTED),
                    ("a", f"bold {ACCENT}"), (f" always allow {what} this session  ", MUTED),
                    ("n", f"bold {ERR}"), (" no  ", MUTED), ("[y] ", MUTED)), end="")
                answer = input().strip().lower()
                if answer in ("", "y", "yes"):
                    return "yes"
                if answer in ("a", "always"):
                    return "always"
                if answer in ("n", "no"):
                    return "no"
        except (KeyboardInterrupt, EOFError):
            return "no"
        finally:
            self.renderer.start("Working")

    # ----- one turn -----
    def run_turn(self, text: str) -> None:
        r = self.renderer
        r.start("Thinking")
        try:
            for event, data in self.session.ask(text):
                if event == "token":
                    r.token(data["text"])
                elif event == "tool_start":
                    r.flush()
                    label = TOOL_LABELS.get(data["name"], data["name"])
                    console.print(Text.assemble(("● ", ACCENT), (label, "bold"),
                                                ("  " + summarize_args(data["name"], data["args"]), MUTED)))
                    r.start(label)
                elif event == "tool_end":
                    r.stop()
                    console.print(Text("  ⎿  ", style=MUTED) + summarize_output(data["name"], data["output"], data["ok"]))
                    r.start("Thinking")
                elif event == "done":
                    r.flush()
        except KeyboardInterrupt:
            r.flush()
            console.print(Text("  ⎿  Interrupted. Tell Vesper what to do instead.", style=WARN))
        except Exception as exc:
            r.flush()
            self.show_error(exc)
        finally:
            r.stop()

    def show_error(self, exc: Exception) -> None:
        from vesper.server import friendly_error

        msg = friendly_error(exc, self.cfg.get("provider", ""))
        msg = msg.replace("Open Settings and paste a valid key", "Run /key to paste a valid key")
        msg = msg.replace("Pick another one in the model menu", "Run /model to pick another one")
        error_panel(msg, "/model switches provider or model · /key sets the API key")

    # ----- input -----
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
                m = re.search(r"@([\w./\-]*)$", text)
                if not m:
                    return
                frag = m.group(1)
                base = (app.root / frag).parent if "/" in frag else app.root
                prefix = frag.rsplit("/", 1)[-1]
                try:
                    base = base.resolve()
                    if base != app.root and app.root not in base.parents:
                        return
                    entries = sorted(base.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
                except OSError:
                    return
                for p in entries[:200]:
                    if p.name.startswith(prefix) and not p.name.startswith("."):
                        rel = p.relative_to(app.root).as_posix() + ("/" if p.is_dir() else "")
                        yield Completion(rel, start_position=-len(frag))

        kb = KeyBindings()

        @kb.add("s-tab")
        def _(event):
            order = list(MODES)
            app.set_mode(order[(order.index(app.mode) + 1) % len(order)], quiet=True)
            event.app.invalidate()

        @kb.add("escape", "enter")
        def _(event):
            event.current_buffer.insert_text("\n")

        @kb.add("c-j")
        def _(event):
            event.current_buffer.insert_text("\n")

        def toolbar():
            color = {"ask": "ansigreen", "auto-edit": "ansiyellow", "yolo": "ansired"}[app.mode]
            label = model_label(app.cfg).replace("&", "&amp;").replace("<", "&lt;")
            return HTML(f" {label}  ·  <style fg='{color}'><b>{app.mode}</b></style>"
                        f" <style fg='ansigray'>(shift+tab)</style>  ·  <style fg='ansigray'>/help</style>")

        try:
            _home().mkdir(parents=True, exist_ok=True)
            history = FileHistory(str(_home() / "history"))
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
        try:
            save_config({"agent_mode": mode})
        except OSError:
            pass
        if not quiet:
            console.print(Text.assemble(("  mode: ", MUTED), (mode, "bold"), (f" · {MODES[mode]}", MUTED)))

    def expand_mentions(self, text: str) -> str:
        """`@path` attaches that file (read through the workspace, so it stays inside the folder)."""
        attached = []
        for m in re.finditer(r"(?<![\w/])@([\w./\-]+)", text):
            rel = m.group(1).rstrip(".,;:")
            try:
                p = self.workspace.resolve(rel)
                if not p.is_file():
                    continue
                body = self.workspace.read_file(rel, 1, 2000)
            except Exception:
                continue
            attached.append(f'<file path="{rel}">\n{body[:MAX_MENTION_CHARS]}\n</file>')
            console.print(Text.assemble(("  ⎿  attached ", MUTED), (rel, ACCENT)))
        return text + ("\n\n" + "\n\n".join(attached) if attached else "")

    # ----- slash commands -----
    def command(self, line: str) -> bool:
        """Runs a slash command. Returns False to quit."""
        cmd, _, arg = line.strip().partition(" ")
        cmd = cmd.lower()
        if cmd in ("/exit", "/quit", "/q"):
            return False
        if cmd == "/help":
            t = Table(box=None, padding=(0, 2), show_header=False)
            t.add_column(style=f"bold {ACCENT}")
            t.add_column(style=MUTED)
            for c, d in SLASH.items():
                t.add_row(c, d)
            t.add_row("", "")
            for k, d in [("@path", "attach a file to your message"), ("!command", "run a shell command yourself"),
                         ("shift+tab", "cycle mode: ask → auto-edit → yolo"), ("esc then enter", "new line"),
                         ("ctrl+c", "stop the answer · twice to quit"), ("↑ ↓", "previous messages")]:
                t.add_row(k, d)
            console.print(Panel(t, title="help", title_align="left", border_style=ACCENT, box=box.ROUNDED, expand=False))
        elif cmd == "/clear":
            self.session.clear()
            console.clear()
            banner(self.root, self.cfg, self.mode)
        elif cmd == "/mode":
            if arg.strip() in MODES:
                self.set_mode(arg.strip())
            else:
                console.print(Text("  /mode ask | auto-edit | yolo", style=MUTED))
        elif cmd == "/model":
            if pick_model(self.cfg):
                self._reload_llm()
        elif cmd == "/key":
            if ask_key(self.cfg.get("provider", "")):
                self._reload_llm()
        elif cmd == "/docs":
            self.workspace.forget_index()
            n = len(self.workspace.doc_files())
            console.print(Text(f"  {n} document{'s' if n != 1 else ''} (PDF, Markdown, text) will be searched.", style=MUTED))
        elif cmd == "/app":
            open_app()
        else:
            console.print(Text(f"  Unknown command {cmd}. Type /help.", style=WARN))
        return True

    def _reload_llm(self) -> None:
        try:
            self.session.set_llm(self.llm())
            console.print(Text.assemble(("  model: ", MUTED), (model_label(self.cfg), "bold")))
        except Exception as exc:
            self.show_error(exc)

    def shell(self, command: str) -> None:
        """`!command`: typed by the user, so it runs without an approval prompt."""
        if not command.strip():
            return
        try:
            proc = subprocess.run(command, shell=True, cwd=self.root)
            if proc.returncode:
                console.print(Text(f"  exit code {proc.returncode}", style=WARN))
        except KeyboardInterrupt:
            console.print(Text("  stopped", style=WARN))

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
                console.print(Text("  Press Ctrl+C again to quit.", style=MUTED))
                continue
            except EOFError:
                break
            line = line.strip()
            if not line:
                continue
            if line.startswith("/"):
                if not self.command(line):
                    break
                continue
            if line.startswith("!"):
                self.shell(line[1:])
                continue
            console.print()
            self.run_turn(self.expand_mentions(line))
            console.print()
        console.print(Text("  See you ✦", style=ACCENT))


# ---------------------------------------------------------------- setup flows
def ask_key(pid: str) -> bool:
    from prompt_toolkit import prompt as pt_prompt

    from vesper import providers as prov

    p = prov.PROVIDERS.get(pid)
    if p is None or not p.key_env:
        console.print(Text("  This provider needs no key.", style=MUTED))
        return False
    if p.key_url:
        console.print(Text.assemble(("  Get a key at ", MUTED), (p.key_url, f"underline {ACCENT_2}")))
    try:
        key = pt_prompt(f"  {p.label} API key (hidden): ", is_password=True).strip()
    except (KeyboardInterrupt, EOFError):
        return False
    if not key:
        return False
    if any(c in key for c in "\r\n") or len(key) > 500:
        error_panel("That key has a line break or is too long.")
        return False
    prov.save_user_settings({p.key_env: key})
    console.print(Text(f"  ✓ Saved in {short_path(prov.USER_ENV)} (only your user can read it).", style=OK))
    return True


def _choose(title: str, options: list[tuple[str, str]], default: int = 0) -> int | None:
    """Numbered menu. Returns the chosen index, or None if cancelled."""
    console.print(Text(title, style="bold"))
    for i, (label, note) in enumerate(options, start=1):
        console.print(Text.assemble((f"  {i:>2}  ", ACCENT), (label, ""), (f"  {note}" if note else "", MUTED)))
    while True:
        try:
            raw = input(f"  number [{default + 1}]: ").strip()
        except (KeyboardInterrupt, EOFError):
            return None
        if not raw:
            return default
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return int(raw) - 1
        console.print(Text("  Type a number from the list.", style=WARN))


def pick_model(cfg: dict) -> bool:
    from vesper import providers as prov

    ids = list(prov.PROVIDERS)
    current = ids.index(cfg["provider"]) if cfg.get("provider") in ids else 0
    opts = []
    for pid in ids:
        p = prov.PROVIDERS[pid]
        status = "ready" if provider_ready(pid) else ("not running" if p.local else "needs a key")
        opts.append((p.label, status))
    i = _choose("Provider", opts, current)
    if i is None:
        return False
    pid = ids[i]
    p = prov.PROVIDERS[pid]
    if pid == "custom" and not os.getenv("CUSTOM_BASE_URL"):
        try:
            url = input("  Base URL of your OpenAI-compatible server (e.g. http://localhost:8000/v1): ").strip()
        except (KeyboardInterrupt, EOFError):
            return False
        if not re.match(r"^https?://[^\s@]+$", url):
            error_panel("That is not an http(s) URL.")
            return False
        prov.save_user_settings({"CUSTOM_BASE_URL": url})
    if p.key_env and not p.local and pid != "custom" and not prov._key(p) and not ask_key(pid):
        return False
    with console.status(Text("Fetching models…", style=MUTED), spinner="dots", spinner_style=ACCENT):
        try:
            live = prov.list_models(pid)
        except Exception:
            live = []
    models = list(dict.fromkeys([*p.suggestions, *[m for m in live if "embed" not in m.lower()]]))[:30]
    if not models and p.chat_model:
        models = [p.chat_model]
    opts = [(m, "default" if m == p.chat_model else "") for m in models] + [("Type another model id", "")]
    j = _choose("Model", opts, 0)
    if j is None:
        return False
    if j == len(models):
        try:
            model = input("  model id: ").strip()
        except (KeyboardInterrupt, EOFError):
            return False
        if not re.match(r"^[\w.:/@+\-]{1,200}$", model):
            error_panel("That does not look like a model id.")
            return False
    else:
        model = models[j]
    cfg.update(provider=pid, model=model)
    save_config({"provider": pid, "model": model})
    return True


def first_run(cfg: dict) -> bool:
    """Make sure a usable model is configured. Returns False if the user gave up."""
    if cfg.get("provider") and provider_ready(cfg["provider"]):
        return True
    console.print(Panel(Text.assemble(
        gradient("Welcome to Vesper"), "\n\n",
        "Pick the AI model Vesper should use. Cloud providers need an API key, stored only on this computer.\n",
        ("Ollama", "bold"), " runs models on your own machine for free, with no key.",
    ), box=box.ROUNDED, border_style=ACCENT, padding=(1, 2), expand=False))
    if not cfg.get("provider") and ollama_running():
        cfg["provider"] = "ollama"
    return pick_model(cfg)


def open_app() -> None:
    found: Path | None = None
    if sys.platform == "darwin":
        found = next((c for c in (Path("/Applications/Vesper.app"), Path.home() / "Applications" / "Vesper.app")
                      if c.exists()), None)
        if found:
            subprocess.Popen(["open", str(found)])
    elif os.name == "nt":
        exe = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Vesper" / "Vesper.exe"
        if exe.exists():
            found = exe
            subprocess.Popen([str(exe)])
    else:
        img = Path.home() / ".local" / "share" / "vesper" / "Vesper.AppImage"
        if img.exists():
            found = img
            subprocess.Popen([str(img)], start_new_session=True)
    if found:
        console.print(Text("  Opening the Vesper app…", style=MUTED))
        return
    console.print(Text("  The desktop app is not installed; opening the same interface in your browser…", style=MUTED))
    subprocess.Popen([sys.executable, "-m", "vesper.desktop"], start_new_session=True)


# ---------------------------------------------------------------- entry points
def trust_folder(root: Path) -> bool:
    """Ask once per folder: text inside files can try to steer an AI model."""
    path = _home() / "trusted.json"
    try:
        trusted = set(json.loads(path.read_text()))
    except (OSError, ValueError, TypeError):
        trusted = set()
    if str(root) in trusted:
        return True
    warn = ""
    if root == Path.home() or root == Path(root.anchor):
        warn = "\nThis is your whole home folder or drive. Starting inside a project folder is safer.\n"
    console.print(Panel(Text.assemble(
        ("Do you trust the files in this folder?\n\n", "bold"),
        (short_path(root) + "\n", ACCENT),
        (warn, WARN),
        ("\nVesper reads files here. Text inside files can try to trick an AI model, "
         "so Vesper asks before it edits a file or runs a command.", MUTED),
    ), box=box.ROUNDED, border_style=WARN, padding=(1, 2), expand=False))
    try:
        answer = input("  Trust this folder? [Y/n] ").strip().lower()
    except (KeyboardInterrupt, EOFError):
        return False
    if answer not in ("", "y", "yes"):
        return False
    trusted.add(str(root))
    try:
        _home().mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(sorted(trusted), indent=1))
    except OSError:
        pass
    return True


def print_mode(root: Path, question: str, cfg: dict, mode: str) -> int:
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
    answer = []
    try:
        session = Session(prov.get_llm(cfg.get("provider"), cfg.get("model") or None), ws)
        for event, data in session.ask(question):
            if event == "token":
                answer.append(data["text"])
            elif event == "tool_start":
                if answer and not answer[-1].endswith("\n"):
                    answer.append("\n\n")  # text before and after a tool call are separate paragraphs
                label = TOOL_LABELS.get(data["name"], data["name"])
                err_console.print(f"● {label} {summarize_args(data['name'], data['args'])}", style=MUTED)
    except Exception as exc:
        from vesper.server import friendly_error

        err_console.print(friendly_error(exc, cfg.get("provider", "")), style=ERR)
        return 1
    print("".join(answer).strip())
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    if argv and argv[0] in ("ask", "agent", "chat", "eval"):
        from vesper.cli import main as lab_main

        lab_main(argv)
        return 0
    if argv and argv[0] == "app":
        open_app()
        return 0

    ap = argparse.ArgumentParser(
        prog="vesper", description="Vesper: an AI agent that works in your folder.",
        epilog="Desktop app: vesper app.  Lab commands: vesper ask | agent | chat | eval.",
    )
    ap.add_argument("prompt", nargs="*", help="ask once and print the answer instead of starting a session")
    ap.add_argument("-C", "--dir", default=".", help="folder to work in (default: the current folder)")
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

    piped = "" if sys.stdin is None or sys.stdin.isatty() else sys.stdin.read(MAX_PIPED_CHARS + 1).strip()
    if len(piped) > MAX_PIPED_CHARS:
        piped = piped[:MAX_PIPED_CHARS] + "\n[... input cut off]"
    if args.prompt or piped:
        question = " ".join(args.prompt)
        if piped:  # `git diff | vesper "write a commit message"`: the piped text comes with the question
            question = f"{question}\n\n<stdin>\n{piped}\n</stdin>" if question else piped
        return print_mode(root, question, cfg, mode)

    console.print()
    if not trust_folder(root):
        return 1
    if not first_run(cfg):
        console.print(Text("  No model chosen. Run vesper again when you are ready.", style=MUTED))
        return 1
    app = App(root, mode, cfg)
    banner(root, cfg, mode)
    with console.status(Text("Starting…", style=MUTED), spinner="dots", spinner_style=ACCENT):
        try:
            app.build()
        except Exception as exc:
            app.show_error(exc)
            return 1
    app.loop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
