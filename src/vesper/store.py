"""Session history in SQLite: `~/.vesper/vesper.db`.

Four tables:

    sessions   one row per conversation (title, working directory, model, when)
    messages   the conversation itself, as LangChain messages, so a session can be resumed exactly
    events     the activity log: every prompt, tool call, result, error and token count
    checkpoints  what a file held before the agent changed it, so a turn's edits can be undone

The terminal app, one-shot mode and the desktop app's Folder mode all write here, so
`vesper sessions`, `/resume` and `/logs` see everything.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path

from langchain_core.messages import BaseMessage, messages_from_dict, messages_to_dict

SCHEMA_VERSION = 2
SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL DEFAULT '',
    cwd         TEXT NOT NULL DEFAULT '',
    provider    TEXT NOT NULL DEFAULT '',
    model       TEXT NOT NULL DEFAULT '',
    source      TEXT NOT NULL DEFAULT 'terminal',   -- terminal | print | desktop
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    session_id  TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    seq         INTEGER NOT NULL,
    role        TEXT NOT NULL,                      -- human | ai | tool | system
    data        TEXT NOT NULL,                      -- the LangChain message as JSON
    PRIMARY KEY (session_id, seq)
);
CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    ts          REAL NOT NULL,
    kind        TEXT NOT NULL,                      -- prompt | tool | answer | usage | error | cwd | approval
    name        TEXT NOT NULL DEFAULT '',           -- tool name, or '' for other kinds
    ok          INTEGER,                            -- 1 / 0 for tools, NULL otherwise
    ms          INTEGER,                            -- how long a tool call or a turn took
    detail      TEXT NOT NULL DEFAULT '{}'          -- JSON: arguments, output, counts...
);
CREATE TABLE IF NOT EXISTS checkpoints (
    session_id  TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    turn        INTEGER NOT NULL,                   -- when the turn started, in milliseconds
    path        TEXT NOT NULL,
    before      BLOB,                               -- the old content; NULL when the file did not exist
    PRIMARY KEY (session_id, turn, path)
);
CREATE INDEX IF NOT EXISTS events_by_session ON events(session_id, id);
CREATE INDEX IF NOT EXISTS sessions_by_time ON sessions(updated_at DESC);
"""
MAX_DETAIL_CHARS = 20_000  # per logged value; the conversation itself is never cut
TITLE_CHARS = 80
KEEP_CHECKPOINT_TURNS = 20


def new_id() -> str:
    return uuid.uuid4().hex[:12]


class Store:
    """One database file. Safe to share between threads (the desktop engine has several)."""

    def __init__(self, path: str | Path | None = None):
        if path is None:
            from vesper import providers as prov

            path = prov.HOME / "vesper.db"
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self.path, check_same_thread=False, timeout=10)
        self._db.row_factory = sqlite3.Row
        with self._lock, self._db:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA foreign_keys=ON")
            self._db.executescript(SCHEMA)
            self._db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # ---------------------------------------------------------------- sessions
    def create_session(self, cwd: str | Path, provider: str = "", model: str = "", source: str = "terminal",
                       session_id: str | None = None, title: str = "") -> str:
        sid = session_id or new_id()
        now = time.time()
        with self._lock, self._db:
            self._db.execute(
                "INSERT OR IGNORE INTO sessions (id, title, cwd, provider, model, source, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (sid, title[:TITLE_CHARS], str(cwd), provider, model, source, now, now),
            )
        return sid

    def update_session(self, session_id: str, **fields) -> None:
        """Change title, cwd, provider or model; always bumps `updated_at`."""
        allowed = {k: v for k, v in fields.items() if k in ("title", "cwd", "provider", "model") and v is not None}
        if "title" in allowed:
            allowed["title"] = " ".join(str(allowed["title"]).split())[:TITLE_CHARS]
        if "cwd" in allowed:
            allowed["cwd"] = str(allowed["cwd"])
        sets = ", ".join(f"{k} = ?" for k in allowed)
        with self._lock, self._db:
            self._db.execute(
                f"UPDATE sessions SET {sets + ', ' if sets else ''}updated_at = ? WHERE id = ?",
                (*allowed.values(), time.time(), session_id),
            )

    def get_session(self, ref: str) -> dict | None:
        """A session by its id or by an unambiguous prefix of it."""
        with self._lock:
            row = self._db.execute("SELECT * FROM sessions WHERE id = ?", (ref,)).fetchone()
            if row is None and ref:
                rows = self._db.execute("SELECT * FROM sessions WHERE id LIKE ? LIMIT 2",
                                        (ref.replace("%", "").replace("_", "") + "%",)).fetchall()
                row = rows[0] if len(rows) == 1 else None
        return dict(row) if row else None

    def list_sessions(self, limit: int = 20, cwd: str | Path | None = None) -> list[dict]:
        """Newest first, with message counts. Sessions that never got a message are left out."""
        where, args = "", []
        if cwd is not None:
            where, args = "WHERE s.cwd = ?", [str(cwd)]
        with self._lock:
            rows = self._db.execute(
                f"SELECT s.*, COUNT(m.seq) AS messages FROM sessions s JOIN messages m ON m.session_id = s.id "
                f"{where} GROUP BY s.id ORDER BY s.updated_at DESC LIMIT ?", (*args, int(limit)),
            ).fetchall()
        return [dict(r) for r in rows]

    def latest(self, cwd: str | Path | None = None) -> dict | None:
        found = self.list_sessions(1, cwd)
        return found[0] if found else None

    def delete_session(self, session_id: str) -> None:
        with self._lock, self._db:
            self._db.execute("DELETE FROM sessions WHERE id = ?", (session_id,))

    # ---------------------------------------------------------------- messages
    def save_messages(self, session_id: str, messages: list[BaseMessage]) -> None:
        """Replace the stored conversation with `messages`."""
        rows = [(session_id, i, m.type, json.dumps(d, ensure_ascii=False))
                for i, (m, d) in enumerate(zip(messages, messages_to_dict(messages), strict=True))]
        with self._lock, self._db:
            self._db.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
            self._db.executemany("INSERT INTO messages (session_id, seq, role, data) VALUES (?, ?, ?, ?)", rows)
            self._db.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (time.time(), session_id))

    def load_messages(self, session_id: str) -> list[BaseMessage]:
        with self._lock:
            rows = self._db.execute("SELECT data FROM messages WHERE session_id = ? ORDER BY seq", (session_id,)).fetchall()
        out = []
        for row in rows:
            try:
                out.extend(messages_from_dict([json.loads(row["data"])]))
            except Exception:  # noqa: BLE001 - one unreadable row should not lose the session
                continue
        return out

    # ---------------------------------------------------------------- undo copies
    def add_checkpoint(self, session_id: str, turn: int, path: str | Path, before: bytes | None) -> None:
        """Keep what `path` held before a turn changed it. The first copy of a turn wins."""
        with self._lock, self._db:
            self._db.execute("INSERT OR IGNORE INTO checkpoints (session_id, turn, path, before) VALUES (?, ?, ?, ?)",
                             (session_id, int(turn), str(path), before))
            # Only the last few turns can be undone; older copies would just grow the file.
            self._db.execute(
                "DELETE FROM checkpoints WHERE session_id = ? AND turn NOT IN (SELECT DISTINCT turn FROM checkpoints "
                "WHERE session_id = ? ORDER BY turn DESC LIMIT ?)", (session_id, session_id, KEEP_CHECKPOINT_TURNS))

    def checkpoints(self, session_id: str) -> list[tuple[int, str, bytes | None]]:
        """(turn, path, old bytes) of the latest turn that changed files."""
        with self._lock:
            rows = self._db.execute(
                "SELECT turn, path, before FROM checkpoints WHERE session_id = ? AND turn = "
                "(SELECT MAX(turn) FROM checkpoints WHERE session_id = ?) ORDER BY path", (session_id, session_id),
            ).fetchall()
        return [(r["turn"], r["path"], None if r["before"] is None else bytes(r["before"])) for r in rows]

    def delete_checkpoints(self, session_id: str, turn: int) -> None:
        with self._lock, self._db:
            self._db.execute("DELETE FROM checkpoints WHERE session_id = ? AND turn = ?", (session_id, int(turn)))

    # ---------------------------------------------------------------- activity log
    def add_event(self, session_id: str, kind: str, name: str = "", detail: dict | None = None,
                  ok: bool | None = None, ms: int | None = None) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO events (session_id, ts, kind, name, ok, ms, detail) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (session_id, time.time(), kind, name, None if ok is None else int(ok), ms,
                 json.dumps(_clip(detail or {}), ensure_ascii=False, default=str)),
            )

    def events(self, session_id: str, limit: int = 200) -> list[dict]:
        """The last `limit` events of a session, oldest first."""
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM (SELECT * FROM events WHERE session_id = ? ORDER BY id DESC LIMIT ?) ORDER BY id",
                (session_id, int(limit)),
            ).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            try:
                item["detail"] = json.loads(item["detail"])
            except ValueError:
                item["detail"] = {}
            item["ok"] = None if item["ok"] is None else bool(item["ok"])
            out.append(item)
        return out


def _clip(value):
    """Keeps log rows small: long strings are cut, containers are handled value by value."""
    if isinstance(value, str):
        return value if len(value) <= MAX_DETAIL_CHARS else value[:MAX_DETAIL_CHARS] + f"… ({len(value)} characters)"
    if isinstance(value, dict):
        return {k: _clip(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clip(v) for v in value]
    return value
