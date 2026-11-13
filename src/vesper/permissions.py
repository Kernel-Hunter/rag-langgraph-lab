"""Which shell commands and web hosts may run without asking.

Three levels, checked in `Workspace._gate`:

    safe commands    read-only commands such as `ls` or `git status`: never ask
    saved rules      prefixes the user answered "always" to, kept in config.json:
                     "git commit" allows `git commit -m "..."` in every later session
    everything else  asks (unless the mode says otherwise)

A rule is a command prefix and matches whole words: "npm test" allows `npm test -- -t x`
but not `npm testx`. A command that chains, pipes, redirects or substitutes (`;`, `&&`, `|`,
`>`, `$(...)`) is never matched by a rule or the safe list, because the part after the
operator could be anything.
"""

from __future__ import annotations

import json
import os
import re
import shlex
from pathlib import Path

# Tools whose second word decides what they do: the rule keeps both words ("git push").
TWO_WORD = {
    "git", "npm", "pnpm", "yarn", "bun", "npx", "cargo", "go", "docker", "kubectl", "pip", "pip3", "uv",
    "poetry", "make", "brew", "gh", "dotnet", "mvn", "gradle", "./gradlew", "swift", "terraform", "apt",
    "apt-get", "conda", "deno", "composer", "bundle", "rails", "php", "flutter", "dart", "mix",
}
# `python -m pytest` and friends: the module is part of the rule.
MODULE_RUNNERS = {"python", "python3", "py"}
# Never offered as an "always" rule: too easy to do damage with different arguments.
NO_RULE = {
    "rm", "rmdir", "sudo", "su", "dd", "mkfs", "chmod", "chown", "kill", "killall", "pkill", "mv", "shutdown",
    "reboot", "curl", "wget", "ssh", "scp", "eval", "exec", "sh", "bash", "zsh", "fish", "powershell", "cmd",
    "del", "format", "diskutil", "launchctl", "systemctl", "open", "osascript", "xargs", "find", "env",
}
SAFE_COMMANDS = (
    "ls", "pwd", "whoami", "which", "wc", "uname", "du", "df", "file", "stat",
    "git status", "git diff", "git log", "git show", "git rev-parse", "git blame", "git ls-files",
    "git stash list", "git branch --show-current", "git remote -v",
)
WRITES_A_FILE = re.compile(r"(^|\s)(-o|--output)(\s|=|$)")  # `git diff --output=file`
OPERATORS = re.compile(r"[;&|<>`\n]|\$\(|\$\{")
MAX_RULES = 200


def is_simple(command: str) -> bool:
    """One plain command: nothing chained, piped, redirected or substituted."""
    return bool(command.strip()) and not OPERATORS.search(command)


def command_prefix(command: str) -> str | None:
    """The rule an "always" answer saves for this command, or None when no rule is safe to offer."""
    if not is_simple(command):
        return None
    try:
        words = shlex.split(command)
    except ValueError:
        return None
    if not words or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", words[0]):  # VAR=value cmd: no rule
        return None
    first = words[0]
    if first in NO_RULE or first.startswith("-"):
        return None
    if first in MODULE_RUNNERS:
        if len(words) >= 3 and words[1] == "-m":
            return " ".join(words[:3])
        return None  # `python script.py` and `python -c` run arbitrary code
    if first in TWO_WORD:
        if len(words) >= 2 and not words[1].startswith("-"):
            return f"{first} {words[1]}"
        return None
    return first


def matches(command: str, rule: str) -> bool:
    command = " ".join(command.split())
    return is_simple(command) and (command == rule or command.startswith(rule + " "))


def is_safe(command: str) -> bool:
    """Read-only commands that never need a yes."""
    return any(matches(command, safe) for safe in SAFE_COMMANDS) and not WRITES_A_FILE.search(command)


def allowed(command: str, rules) -> bool:
    return is_safe(command) or any(matches(command, rule) for rule in rules)


# ---------------------------------------------------------------- saved rules (config.json)
def _config_path() -> Path:
    from vesper import providers as prov

    return prov.HOME / "config.json"


def _read() -> dict:
    try:
        data = json.loads(_config_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(patch: dict) -> None:
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name("config.json.tmp")
    tmp.write_text(json.dumps({**_read(), **patch}, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def load_rules(key: str = "allow_commands") -> list[str]:
    """Saved rules: `allow_commands` (command prefixes) or `allow_hosts` (web hosts)."""
    value = _read().get(key)
    return [r for r in value if isinstance(r, str) and r.strip()][:MAX_RULES] if isinstance(value, list) else []


def save_rule(rule: str, key: str = "allow_commands") -> None:
    rules = load_rules(key)
    if rule and rule not in rules:
        try:
            _write({key: [*rules, rule][-MAX_RULES:]})
        except OSError:
            pass  # the rule still holds for this session


def remove_rule(rule: str, key: str = "allow_commands") -> bool:
    rules = load_rules(key)
    if rule not in rules:
        return False
    _write({key: [r for r in rules if r != rule]})
    return True
