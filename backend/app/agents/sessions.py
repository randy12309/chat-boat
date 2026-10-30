"""Durable per-user conversation sessions. Rows are keyed by (username, session_id),
so a session can only ever be loaded by the authenticated user who owns it."""
import json
from contextlib import closing
import os
import re
import sqlite3
import time
from pathlib import Path

MAX_HISTORY = 20
SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
DEFAULT_SESSION = "default"


def _db_path() -> str:
    return os.getenv("SESSION_DB", str(Path(__file__).parent.parent / "data" / "sessions.db"))


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path())
    conn.execute(
        "CREATE TABLE IF NOT EXISTS sessions ("
        "username TEXT NOT NULL, session_id TEXT NOT NULL, state TEXT NOT NULL, updated_at REAL NOT NULL, "
        "PRIMARY KEY (username, session_id))"
    )
    return conn


def valid_session_id(session_id: str) -> bool:
    return bool(SESSION_ID_RE.match(session_id))


def load(username: str, session_id: str) -> dict:
    with closing(_connect()) as conn, conn:
        row = conn.execute(
            "SELECT state FROM sessions WHERE username=? AND session_id=?", (username, session_id)
        ).fetchone()
    return json.loads(row[0]) if row else {"history": [], "pending_quote": None}


def save(username: str, session_id: str, state: dict) -> None:
    state = {**state, "history": state["history"][-MAX_HISTORY:]}
    with closing(_connect()) as conn, conn:
        conn.execute(
            "INSERT INTO sessions (username, session_id, state, updated_at) VALUES (?,?,?,?) "
            "ON CONFLICT(username, session_id) DO UPDATE SET state=excluded.state, updated_at=excluded.updated_at",
            (username, session_id, json.dumps(state), time.time()),
        )


def list_sessions(username: str) -> list[dict]:
    with closing(_connect()) as conn, conn:
        rows = conn.execute(
            "SELECT session_id, updated_at FROM sessions WHERE username=? ORDER BY updated_at DESC", (username,)
        ).fetchall()
    return [{"session_id": r[0], "updated_at": r[1]} for r in rows]


def delete(username: str, session_id: str) -> bool:
    with closing(_connect()) as conn, conn:
        return conn.execute(
            "DELETE FROM sessions WHERE username=? AND session_id=?", (username, session_id)
        ).rowcount > 0
