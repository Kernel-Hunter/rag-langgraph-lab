"""Settings shared with the desktop app, and the questions the terminal app asks before a
session starts: which model, which API key, and whether to trust the folder.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

from rich import box
from rich.panel import Panel
from rich.text import Text

from vesper.ui import (
    ACCENT,
    ACCENT_2,
    MUTED,
    OK,
    WARN,
    console,
    error_panel,
    gradient,
    short_path,
)


# ---------------------------------------------------------------- config.json
def home() -> Path:
    from vesper import providers as prov

    return prov.HOME


def load_config() -> dict:
    try:
        data = json.loads((home() / "config.json").read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_config(patch: dict) -> None:
    folder = home()
    folder.mkdir(parents=True, exist_ok=True)
    data = {**load_config(), **patch}
    tmp = folder / "config.json.tmp"
    tmp.write_text(json.dumps(data, indent=2))
    os.replace(tmp, folder / "config.json")


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


# ---------------------------------------------------------------- model and key
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


def choose(title: str, options: list[tuple[str, str]], default: int = 0) -> int | None:
    """Numbered menu. Returns the chosen index, or None if cancelled."""
    console.print(Text(title, style="bold"))
    for i, (label, hint) in enumerate(options, start=1):
        console.print(Text.assemble((f"  {i:>2}  ", ACCENT), (label, ""), (f"  {hint}" if hint else "", MUTED)))
    while True:
        try:
            raw = input(f"  number [{default + 1}]: ").strip()
        except (KeyboardInterrupt, EOFError):
            console.print()
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
    i = choose("Provider", opts, current)
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
    start = models.index(cfg["model"]) if pid == cfg.get("provider") and cfg.get("model") in models else 0
    opts = [(m, "default" if m == p.chat_model else "") for m in models] + [("Type another model id", "")]
    j = choose("Model", opts, start)
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
    from vesper import providers as prov

    if prov.fake_llm_enabled() or (cfg.get("provider") and provider_ready(cfg["provider"])):
        return True
    console.print(Panel(Text.assemble(
        gradient("Welcome to Vesper"), "\n\n",
        "Pick the AI model Vesper should use. Cloud providers need an API key, stored only on this computer.\n",
        ("Ollama", "bold"), " runs models on your own machine for free, with no key.",
    ), box=box.ROUNDED, border_style=ACCENT, padding=(1, 2), expand=False))
    if not cfg.get("provider") and ollama_running():
        cfg["provider"] = "ollama"
    return pick_model(cfg)


# ---------------------------------------------------------------- trust and the desktop app
def trust_folder(root: Path) -> bool:
    """Ask once per starting folder: text inside files can try to steer an AI model."""
    path = home() / "trusted.json"
    try:
        trusted = set(json.loads(path.read_text()))
    except (OSError, ValueError, TypeError):
        trusted = set()
    if str(root) in trusted:
        return True
    console.print(Panel(Text.assemble(
        ("Start Vesper in this folder?\n\n", "bold"),
        (short_path(root) + "\n", ACCENT),
        ("\nVesper can read files, here and elsewhere on this computer when you ask. Text inside "
         "files can try to trick an AI model, so Vesper asks before it edits a file or runs a command.", MUTED),
    ), box=box.ROUNDED, border_style=WARN, padding=(1, 2), expand=False))
    try:
        answer = input("  Continue? [Y/n] ").strip().lower()
    except (KeyboardInterrupt, EOFError):
        return False
    if answer not in ("", "y", "yes"):
        return False
    trusted.add(str(root))
    try:
        home().mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(sorted(trusted), indent=1))
    except OSError:
        pass
    return True


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
