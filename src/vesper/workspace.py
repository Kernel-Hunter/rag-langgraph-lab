"""The agent's tools: look around, read, search, edit files and run commands.

`Workspace.root` is the working directory. It is where relative paths start and where
commands run, and the agent can move it with `change_directory`. Paths are not confined to
it: absolute paths and `~` work, like in a shell.

Reading, listing and searching run freely. Writing a file, editing a file and running a
shell command go through `approve`, which the terminal UI turns into a y/n prompt and the
desktop app into an Allow/Deny card. Three things are stricter than the approval mode:
commands that can wipe a disk and edits to credential folders always ask, and files that
hold secrets are never read.

Tool errors come back as text starting with "Error:" so the model can recover.
"""

from __future__ import annotations

import difflib
import fnmatch
import os
import re
import signal
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from langchain.tools import tool

IGNORED_DIRS = {
    ".git", ".hg", ".svn", "node_modules", ".venv", "venv", "env", "__pycache__", ".mypy_cache",
    ".pytest_cache", ".ruff_cache", ".tox", "dist", "build", ".next", ".nuxt", "target", ".idea",
    ".vscode", ".gradle", ".cache", ".DS_Store", "Pods", ".terraform",
}
DOC_SUFFIXES = {".pdf", ".md", ".txt"}  # what vesper.indexing can load
MAX_READ_BYTES = 2_000_000          # text files larger than this are read in part only
MAX_LINE_CHARS = 2000
DEFAULT_READ_LINES = 400
MAX_LIST_ENTRIES = 500
MAX_MATCHES = 200
MAX_GREP_FILE_BYTES = 2_000_000
MAX_OUTPUT_CHARS = 20_000
MAX_WRITE_CHARS = 1_000_000
MAX_INDEX_FILES = 300
MAX_INDEX_BYTES = 150_000_000
MAX_SCAN_FILES = 50_000             # glob and grep stop walking after this many files

# What `approve` returns: "yes" (this call), "always" (this kind of action for the session), "no".
Approver = Callable[[dict], str]
MODES = ("ask", "auto-edit", "yolo")

# Commands that can destroy a disk or a home folder. These ask even in yolo mode.
DANGEROUS_COMMAND = re.compile(
    r"""(?ix)
    \brm\s+(-[a-z]*\s+)*(/|~/?|\$HOME/?|/\*|~/\*)\s*($|[;&|])  # rm -rf / or ~
    | \bmkfs(\.\w+)?\b | \bdd\b[^|;&]*\bof=/dev/ | >\s*/dev/(sd|disk|nvme)
    | \bdiskutil\s+(erase|partition|zero) | \bformat\s+[a-z]:
    | :\(\)\s*\{.*\};\s*:                                    # fork bomb
    | (^|[;&|]\s*)(sudo\s+)?(shutdown|reboot|halt)(\s|$)
    """
)
# Folders whose contents are credentials. Edits there always ask.
CREDENTIAL_DIRS = (".ssh", ".aws", ".gnupg", ".kube", ".config/gcloud", ".docker")
# Files that hold secrets. They are never read into the conversation.
SECRET_NAMES = re.compile(r"(?i)^(id_(rsa|dsa|ecdsa|ed25519)|.*\.(pem|key|p12|pfx|keystore)|\.netrc|credentials)$")


class ToolError(Exception):
    """A problem the model should see and work around (bad path, no match, refused)."""


def _deny_all(_action: dict) -> str:
    return "no"


@dataclass
class Workspace:
    root: Path
    approve: Approver = _deny_all
    mode: str = "ask"  # ask | auto-edit (edits run without asking) | yolo (nothing asks)
    embeddings_factory: Callable[[], object] | None = None
    k: int = 4
    on_sources: Callable[[str, list], None] | None = None
    on_cwd: Callable[[Path], None] | None = None        # the working directory changed
    on_auto: Callable[[dict], None] | None = None       # an edit or command ran without asking
    on_output: Callable[[str], None] | None = None      # a line of output from a running command
    always: set[str] = field(default_factory=set)
    _index: object | None = field(default=None, repr=False)
    _index_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _running: list = field(default_factory=list, repr=False)  # stop functions of commands in progress
    waited: float = 0.0  # seconds spent waiting for the user to answer approvals
    stopped: bool = False  # the user stopped the turn: tools refuse to start until the next one
    _turn: threading.RLock = field(default_factory=threading.RLock, repr=False)

    def __post_init__(self) -> None:
        self.root = Path(self.root).expanduser().resolve()
        if not self.root.is_dir():
            raise ValueError(f"{self.root} is not a folder")
        if self.mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")

    # ---------------------------------------------------------------- paths
    def resolve(self, path: str = ".") -> Path:
        """Absolute form of `path`. Relative paths start at the working directory; `~` is home."""
        text = str(path or ".").strip().strip('"\'')
        raw = Path(os.path.expanduser(text or "."))
        return (raw if raw.is_absolute() else self.root / raw).resolve()

    def rel(self, path: Path) -> str:
        """How a path is shown: relative inside the working directory, `~/...` or absolute outside."""
        try:
            return path.relative_to(self.root).as_posix() or "."
        except ValueError:
            pass
        try:
            return "~/" + path.relative_to(Path.home()).as_posix()
        except ValueError:
            return str(path)

    def change_directory(self, path: str) -> str:
        target = self.resolve(path)
        if not target.exists():
            raise ToolError(f"{path} does not exist.")
        if not target.is_dir():
            raise ToolError(f"{path} is a file, not a folder.")
        if target != self.root:
            self.root = target
            self.forget_index()
            if self.on_cwd is not None:
                self.on_cwd(target)
        try:
            names = sorted(p.name + ("/" if p.is_dir() else "") for p in target.iterdir() if p.name not in IGNORED_DIRS)
        except OSError as exc:
            raise ToolError(f"Cannot read {target}: {exc.strerror}") from None
        shown = ", ".join(names[:40]) + (f", ... {len(names) - 40} more" if len(names) > 40 else "")
        return f"Working directory is now {target}\nContains: {shown or '(empty)'}"

    @staticmethod
    def _is_secret(p: Path) -> bool:
        home = Path.home()
        if SECRET_NAMES.match(p.name):
            return True
        return p.name == ".env" and p.parent.name in (".vesper", ".pagewise", ".rag-lab") and p.parent.parent == home

    @staticmethod
    def _is_credential_path(p: Path) -> bool:
        home = Path.home()
        return any(p == home / d or (home / d) in p.parents for d in CREDENTIAL_DIRS)

    def _walk(self, start: Path, budget: list[int] | None = None):
        """Files under `start`, skipping ignored folders, in a stable order.

        `budget` is a one-item list holding how many files may still be visited; the walk
        stops when it reaches zero, so a search started in a huge folder cannot hang.
        """
        for dirpath, dirnames, filenames in os.walk(start):
            dirnames[:] = sorted(d for d in dirnames if d not in IGNORED_DIRS)
            for name in sorted(filenames):
                if name in IGNORED_DIRS:
                    continue
                if budget is not None:
                    if budget[0] <= 0:
                        return
                    budget[0] -= 1
                yield Path(dirpath) / name

    # ---------------------------------------------------------------- approval
    def _gate(self, kind: str, action: dict, strict: bool = False) -> None:
        """kind: "edit" (write/edit a file) or "run" (shell command). Raises ToolError if refused.

        `strict` actions (see the module docstring) ask whatever the mode, and "always" does not cover them.
        """
        action = {**action, "kind": kind, "strict": strict}
        free = self.mode == "yolo" or kind in self.always or (self.mode == "auto-edit" and kind == "edit")
        if free and not strict:
            if self.on_auto is not None:
                self.on_auto(action)
            return
        asked = time.monotonic()
        try:
            answer = (self.approve(action) or "no").lower()
        finally:
            self.waited += time.monotonic() - asked
        if answer == "always" and not strict:
            self.always.add(kind)
            return
        if answer in ("yes", "always"):
            return
        raise ToolError("The user declined this action. Do not retry it; ask what they want instead.")

    # ---------------------------------------------------------------- read side
    def list_files(self, path: str = ".", depth: int = 2) -> str:
        base = self.resolve(path)
        if not base.is_dir():
            raise ToolError(f"{path} is not a folder.")
        depth = max(1, min(int(depth), 6))
        lines: list[str] = []
        total = 0

        def walk(d: Path, level: int) -> None:
            nonlocal total
            try:
                entries = sorted(d.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
            except OSError as exc:
                lines.append(f"{'  ' * level}(cannot read: {exc.strerror})")
                return
            for p in entries:
                if p.name in IGNORED_DIRS:
                    continue
                total += 1
                if len(lines) >= MAX_LIST_ENTRIES:
                    continue
                if p.is_dir():
                    lines.append(f"{'  ' * level}{p.name}/")
                    if level + 1 < depth:
                        walk(p, level + 1)
                else:
                    try:
                        size = p.stat().st_size
                    except OSError:
                        size = 0
                    lines.append(f"{'  ' * level}{p.name}  ({_size(size)})")

        walk(base, 0)
        head = f"{self.rel(base)}/"
        if not lines:
            return f"{head}\n(empty)"
        more = f"\n... {total - len(lines)} more entries; list a subfolder to see them." if total > len(lines) else ""
        return head + "\n" + "\n".join(lines) + more

    def read_file(self, path: str, offset: int = 1, limit: int = DEFAULT_READ_LINES) -> str:
        p = self.resolve(path)
        if p.is_dir():
            raise ToolError(f"{path} is a folder. Use list_files.")
        if not p.exists():
            raise ToolError(f"{path} does not exist.")
        if self._is_secret(p):
            raise ToolError(f"{self.rel(p)} holds secrets (keys or passwords), so Vesper does not read it. "
                            "Ask the user for the specific value you need.")
        offset, limit = max(1, int(offset)), max(1, min(int(limit), 2000))
        if p.suffix.lower() == ".pdf":
            return self._read_pdf(p, offset, limit)
        with open(p, "rb") as f:
            raw = f.read(MAX_READ_BYTES + 1)
        if b"\x00" in raw[:8192]:
            return f"{self.rel(p)} is a binary file ({_size(p.stat().st_size)}); it cannot be shown as text."
        text = raw[:MAX_READ_BYTES].decode("utf-8", errors="replace")
        lines = text.splitlines()
        chunk = lines[offset - 1: offset - 1 + limit]
        if not chunk:
            return f"{self.rel(p)} has {len(lines)} lines; nothing at line {offset}."
        body = "\n".join(f"{i:>6}\t{_clip(line)}" for i, line in enumerate(chunk, start=offset))
        end = offset + len(chunk) - 1
        note = ""
        if end < len(lines):
            note = f"\n(lines {offset}-{end} of {len(lines)}; call read_file with offset={end + 1} for more)"
        if len(raw) > MAX_READ_BYTES:
            note += f"\n(file is {_size(p.stat().st_size)}; only the first {_size(MAX_READ_BYTES)} were read)"
        return body + note

    def _read_pdf(self, p: Path, first_page: int, pages: int) -> str:
        from pypdf import PdfReader

        try:
            reader = PdfReader(str(p))
            total = len(reader.pages)
        except Exception as exc:
            raise ToolError(f"Could not read {self.rel(p)} as a PDF: {exc}") from None
        pages = min(pages, 30)
        out = []
        for n in range(first_page, min(total, first_page + pages - 1) + 1):
            try:
                text = reader.pages[n - 1].extract_text() or ""
            except Exception:
                text = "(this page could not be read)"
            out.append(f"[page {n}]\n{text.strip()}")
        if not out:
            return f"{self.rel(p)} has {total} pages; nothing at page {first_page}."
        last = first_page + len(out) - 1
        more = f"\n(pages {first_page}-{last} of {total}; call read_file with offset={last + 1} for more)" if last < total else ""
        return "\n\n".join(out) + more

    def glob(self, pattern: str, path: str = ".") -> str:
        pattern = (pattern or "").strip()
        if not pattern:
            raise ToolError("Give a pattern such as **/*.py.")
        base = self.resolve(path)
        # A pattern with a fixed folder in front ("~/notes/*.md", "/tmp/**/*.log") searches that folder.
        fixed = []
        parts = Path(os.path.expanduser(pattern)).parts
        while parts and not any(ch in parts[0] for ch in "*?["):
            fixed.append(parts[0])
            parts = parts[1:]
        if fixed and parts and (Path(*fixed).is_absolute() or ".." in fixed):
            base, pattern = self.resolve(str(Path(*fixed))), Path(*parts).as_posix()
        if not base.is_dir():
            raise ToolError(f"{self.rel(base)} is not a folder.")
        hits = []
        budget = [MAX_SCAN_FILES]
        for p in self._walk(base, budget):
            r = p.relative_to(base).as_posix()
            if fnmatch.fnmatch(r, pattern) or fnmatch.fnmatch(r, pattern.removeprefix("**/")) or fnmatch.fnmatch(p.name, pattern):
                hits.append(p)
        cut = _SCAN_NOTE if budget[0] <= 0 else ""
        if not hits:
            return f"No files match {pattern}.{cut}"
        hits.sort(key=lambda p: _mtime(p), reverse=True)
        shown = [self.rel(p) for p in hits[:MAX_MATCHES]]
        more = f"\n... and {len(hits) - MAX_MATCHES} more" if len(hits) > MAX_MATCHES else ""
        return "\n".join(shown) + more + cut

    def grep(self, pattern: str, path: str = ".", file_glob: str = "", ignore_case: bool = False) -> str:
        try:
            rx = re.compile(pattern, re.IGNORECASE if ignore_case else 0)
        except re.error as exc:
            raise ToolError(f"Bad regular expression: {exc}") from None
        base = self.resolve(path)
        budget = [MAX_SCAN_FILES]
        files = [base] if base.is_file() else self._walk(base, budget)
        out: list[str] = []
        count = 0
        for p in files:
            if file_glob and not fnmatch.fnmatch(p.name, file_glob) and not fnmatch.fnmatch(self.rel(p), file_glob):
                continue
            try:
                if p.stat().st_size > MAX_GREP_FILE_BYTES:
                    continue
                data = p.read_bytes()
            except OSError:
                continue
            if b"\x00" in data[:8192]:
                continue
            for n, line in enumerate(data.decode("utf-8", errors="replace").splitlines(), start=1):
                if rx.search(line):
                    count += 1
                    if len(out) < MAX_MATCHES:
                        out.append(f"{self.rel(p)}:{n}: {_clip(line.strip(), 300)}")
        cut = _SCAN_NOTE if budget[0] <= 0 else ""
        if not out:
            return f"No matches for {pattern!r}.{cut}"
        more = f"\n... {count - len(out)} more matches; narrow the search." if count > len(out) else ""
        return "\n".join(out) + more + cut

    # ---------------------------------------------------------------- write side
    def write_file(self, path: str, content: str) -> str:
        p = self.resolve(path)
        if p.is_dir():
            raise ToolError(f"{path} is a folder.")
        if len(content) > MAX_WRITE_CHARS:
            raise ToolError("That file is too large to write in one call.")
        old = _read_text(p) if p.exists() else None
        if old is not None and old == content:
            return f"{self.rel(p)} already has this content; nothing changed."
        self._gate("edit", {
            "tool": "write_file", "path": self.rel(p), "new": old is None,
            "diff": _diff(self.rel(p), old or "", content),
        }, strict=self._is_credential_path(p))
        p.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(p, content)
        verb = "Created" if old is None else "Wrote"
        return f"{verb} {self.rel(p)} ({len(content.splitlines())} lines)."

    def edit_file(self, path: str, old_string: str, new_string: str, replace_all: bool = False) -> str:
        p = self.resolve(path)
        if not p.is_file():
            raise ToolError(f"{path} does not exist. Use write_file to create it.")
        text = _read_text(p)
        if text is None:
            raise ToolError(f"{path} is not a text file.")
        if not old_string:
            raise ToolError("old_string is empty. Use write_file to replace a whole file.")
        if old_string == new_string:
            raise ToolError("old_string and new_string are the same.")
        hits = text.count(old_string)
        if hits == 0:
            raise ToolError(f"old_string was not found in {path}. Read the file again and copy the exact text, including spaces.")
        if hits > 1 and not replace_all:
            raise ToolError(f"old_string appears {hits} times in {path}. Add surrounding lines to make it unique, or set replace_all.")
        new_text = text.replace(old_string, new_string) if replace_all else text.replace(old_string, new_string, 1)
        self._gate("edit", {"tool": "edit_file", "path": self.rel(p), "diff": _diff(self.rel(p), text, new_text)},
                   strict=self._is_credential_path(p))
        _atomic_write(p, new_text)
        n = hits if replace_all else 1
        return f"Edited {self.rel(p)} ({n} change{'s' if n > 1 else ''})."

    def run_command(self, command: str, timeout: int = 120) -> str:
        command = (command or "").strip()
        if not command:
            raise ToolError("Empty command.")
        timeout = max(1, min(int(timeout), 600))
        moved = re.fullmatch(r"cd(\s+(?P<to>[^;&|<>]+))?", command)
        if moved:  # a bare `cd`: make it last, like the user expects
            return self.change_directory((moved.group("to") or "~").strip())
        self._gate("run", {"tool": "run_command", "command": command, "cwd": str(self.root)},
                   strict=bool(DANGEROUS_COMMAND.search(command)))
        code, text = self._run(command, timeout)
        if self.stopped:
            raise ToolError(f"The user stopped this command.\n{_middle(text)}".rstrip())
        if code is None:
            return f"Error: the command did not finish within {timeout} s and was stopped.\n{_middle(text)}".rstrip()
        text = _middle(text).strip()
        return f"exit code {code}\n{text}" if text else f"exit code {code} (no output)"

    def _run(self, command: str, timeout: int) -> tuple[int | None, str]:
        """Runs `command` in a shell. Returns (exit code, output); the code is None after a timeout.

        Output is read as it arrives so the UI can show it live (`on_output`). The command gets
        its own process group, so a timeout or Ctrl+C stops everything it started.
        """
        posix = os.name == "posix"
        proc = subprocess.Popen(
            command, shell=True, cwd=self.root, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=posix,
            env={**os.environ, "PAGER": "cat", "GIT_PAGER": "cat", "TERM": "dumb"},
        )
        chunks: list[str] = []

        def pump() -> None:
            for raw in iter(proc.stdout.readline, b""):
                line = raw.decode("utf-8", errors="replace")
                chunks.append(line)
                if self.on_output is not None:
                    try:
                        self.on_output(line.rstrip("\r\n"))
                    except Exception:  # noqa: BLE001 - drawing must never break the command
                        pass

        reader = threading.Thread(target=pump, daemon=True)
        reader.start()

        def stop() -> None:
            try:
                if posix:
                    os.killpg(proc.pid, signal.SIGKILL)
                else:
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, check=False)
            except (OSError, ProcessLookupError):
                proc.kill()

        self._running.append(stop)
        try:
            code = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            stop()
            code = None
        except BaseException:  # Ctrl+C: stop the command, then let the interrupt through
            stop()
            raise
        finally:
            self._running.remove(stop)
            reader.join(timeout=2)
            proc.stdout.close()
        return code, "".join(chunks)

    def cancel(self) -> None:
        """The user pressed Ctrl+C or Stop: end running commands and let no new tool start.

        `stopped` is cleared when the next turn begins (`Session.ask`).
        """
        self.stopped = True
        for stop in list(self._running):
            stop()

    # ---------------------------------------------------------------- documents (RAG)
    def doc_files(self) -> list[Path]:
        files, total = [], 0
        for p in self._walk(self.root, [MAX_SCAN_FILES]):
            if p.suffix.lower() not in DOC_SUFFIXES:
                continue
            try:
                size = p.stat().st_size
            except OSError:
                continue
            if total + size > MAX_INDEX_BYTES or len(files) >= MAX_INDEX_FILES:
                break
            files.append(p)
            total += size
        return files

    def search_docs(self, query: str) -> str:
        store = self._ensure_index()
        if store is None:
            return "There are no PDF, Markdown or text documents in this folder to search."
        found = store.similarity_search(query, k=self.k)
        if self.on_sources is not None:
            self.on_sources(query, found)
        if not found:
            return "Nothing relevant found in the documents."
        return "\n\n".join(
            f"[{d.metadata.get('source', '?')}, page {d.metadata.get('page', '?')}] {d.page_content}" for d in found
        )

    def _ensure_index(self):
        with self._index_lock:
            if self._index is not None:
                return self._index or None
            if self.embeddings_factory is None:
                raise ToolError("Document search is not set up.")
            from langchain_core.vectorstores import InMemoryVectorStore

            from vesper.indexing import load_documents, split_documents

            docs = []
            for p in self.doc_files():
                try:
                    loaded = load_documents(p, warnings=[])
                except Exception:
                    continue
                for d in loaded:
                    d.metadata["source"] = self.rel(p)
                    docs.append(d)
            if not docs:
                self._index = False
                return None
            chunks = split_documents(docs, chunk_size=800, chunk_overlap=120)
            store = InMemoryVectorStore(self.embeddings_factory())
            store.add_documents(chunks)
            self._index = store
            return store

    def forget_index(self) -> None:
        with self._index_lock:
            self._index = None

    # ---------------------------------------------------------------- LangChain tools
    def tools(self) -> list:
        ws = self

        def safe(fn, *args):
            # One tool at a time, in the order the model asked: a model may call several at
            # once, and "go to folder" next to "edit file" must not race.
            with ws._turn:
                if ws.stopped:
                    return "Error: the user stopped this request. Do not continue; wait for the next message."
                try:
                    return fn(*args)
                except ToolError as exc:
                    return f"Error: {exc}"
                except (OSError, ValueError) as exc:
                    return f"Error: {getattr(exc, 'strerror', None) or exc}"

        @tool
        def list_files(path: str = ".", depth: int = 2) -> str:
            """List files and folders (a tree) under `path`. Relative paths start at the working directory; absolute paths and ~ work too. Start here to see what is in a folder."""
            return safe(ws.list_files, path, depth)

        @tool
        def read_file(path: str, offset: int = 1, limit: int = DEFAULT_READ_LINES) -> str:
            """Read a text file with line numbers, or a PDF page by page (offset and limit count pages for PDFs). Read a file before editing it."""
            return safe(ws.read_file, path, offset, limit)

        @tool
        def glob(pattern: str, path: str = ".") -> str:
            """Find files by name pattern under `path` (default: the working directory), e.g. '**/*.py' or 'docs/*.md'. Newest first."""
            return safe(ws.glob, pattern, path)

        @tool
        def change_directory(path: str) -> str:
            """Change the working directory, like `cd`. Use it when the user asks to go to, enter, open or leave a folder or project. Accepts relative paths, absolute paths, ~ and '..'. Later tool calls and commands run there."""
            return safe(ws.change_directory, path)

        @tool
        def grep(pattern: str, path: str = ".", file_glob: str = "", ignore_case: bool = False) -> str:
            """Search file contents with a regular expression. Returns path:line: text. `file_glob` filters files, e.g. '*.ts'."""
            return safe(ws.grep, pattern, path, file_glob, ignore_case)

        @tool
        def search_docs(query: str) -> str:
            """Semantic search over the PDF, Markdown and text documents under the working directory. Use it for questions about what the documents say; cite results as [file, page N]."""
            return safe(ws.search_docs, query)

        @tool
        def write_file(path: str, content: str) -> str:
            """Create a file or replace its whole content. Prefer edit_file for changes to an existing file."""
            return safe(ws.write_file, path, content)

        @tool
        def edit_file(path: str, old_string: str, new_string: str, replace_all: bool = False) -> str:
            """Replace exact text in a file. old_string must match the file exactly (read it first) and be unique unless replace_all is true."""
            return safe(ws.edit_file, path, old_string, new_string, replace_all)

        @tool
        def run_command(command: str, timeout: int = 120) -> str:
            """Run a shell command in the working directory (tests, builds, git, scripts...). `cd` inside a command does not last: use change_directory to move. Output is cut after 20,000 characters; `timeout` is in seconds (max 600)."""
            return safe(ws.run_command, command, timeout)

        return [list_files, read_file, glob, grep, search_docs, change_directory, write_file, edit_file, run_command]


# -------------------------------------------------------------------- helpers
def _size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n} B"


_SCAN_NOTE = f"\n(stopped after {MAX_SCAN_FILES:,} files; give a narrower path to search the rest)"


def _middle(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    """Long output keeps its start and end; the middle is cut."""
    if len(text) <= limit:
        return text
    half = limit // 2
    return text[:half] + f"\n... ({len(text) - limit} characters cut) ...\n" + text[-half:]


def _clip(line: str, limit: int = MAX_LINE_CHARS) -> str:
    return line if len(line) <= limit else line[:limit] + " …"


def _mtime(p: Path) -> float:
    try:
        return p.stat().st_mtime
    except OSError:
        return 0.0


def _read_text(p: Path) -> str | None:
    data = p.read_bytes()
    if b"\x00" in data[:8192]:
        return None
    return data.decode("utf-8", errors="replace")


def _atomic_write(p: Path, content: str) -> None:
    tmp = p.with_name(f".{p.name}.vesper-tmp")
    try:
        with open(tmp, "w", encoding="utf-8", newline="") as f:
            f.write(content)
        if p.exists():
            try:
                os.chmod(tmp, p.stat().st_mode & 0o7777)
            except OSError:
                pass
        os.replace(tmp, p)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def _diff(name: str, old: str, new: str, limit: int = 400) -> str:
    lines = list(difflib.unified_diff(old.splitlines(), new.splitlines(), f"a/{name}", f"b/{name}", lineterm="", n=2))
    if len(lines) > limit:
        lines = lines[:limit] + [f"... ({len(lines) - limit} more diff lines)"]
    return "\n".join(lines)
