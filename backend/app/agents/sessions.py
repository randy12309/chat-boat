"""Durable per-user conversation sessions. Rows are keyed by (username, session_id),
so a session can only ever be loaded by the authenticated user who owns it.

MySQL (app.agents.chat_store) is used when configured; otherwise a local SQLite file keeps the
same behaviour for offline development and tests."""
import json
from contextlib import closing
import os
import re
import sqlite3
import time
from pathlib import Path

from app.agents import chat_store
from mcp_server import db

MAX_HISTORY = 20
MAX_STORED = 500
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


def _read(username: str, session_id: str) -> dict:
    with closing(_connect()) as conn, conn:
        row = conn.execute(
            "SELECT state FROM sessions WHERE username=? AND session_id=?", (username, session_id)
        ).fetchone()
    return json.loads(row[0]) if row else {"history": [], "pending_quote": None}


def _write(username: str, session_id: str, state: dict) -> None:
    state = {**state, "history": state["history"][-MAX_STORED:]}
    with closing(_connect()) as conn, conn:
        conn.execute(
            "INSERT INTO sessions (username, session_id, state, updated_at) VALUES (?,?,?,?) "
            "ON CONFLICT(username, session_id) DO UPDATE SET state=excluded.state, updated_at=excluded.updated_at",
            (username, session_id, json.dumps(state), time.time()),
        )


def load(username: str, session_id: str) -> dict:
    """Short-term context for the agent: the last MAX_HISTORY messages and any pending payment quote."""
    if db.enabled():
        return chat_store.load(username, session_id)
    state = _read(username, session_id)
    history = [{k: v for k, v in m.items() if k != "time"} for m in state["history"][-MAX_HISTORY:]]
    return {"history": history, "pending_quote": state.get("pending_quote")}


def append_turn(username: str, session_id: str, user_text: str, reply: str, sources: list, pending_quote) -> None:
    if db.enabled():
        return chat_store.append_turn(username, session_id, user_text, reply, sources, pending_quote)
    state = _read(username, session_id)
    now = time.time()
    state["history"] += [
        {"role": "user", "content": user_text, "time": now},
        {"role": "assistant", "content": reply, "sources": sources, "time": now},
    ]
    state["pending_quote"] = pending_quote
    state.setdefault("title", chat_store.make_title(user_text))
    _write(username, session_id, state)


def get_history(username: str, session_id: str) -> list[dict]:
    """Full saved conversation, oldest first, for display."""
    if db.enabled():
        return chat_store.get_history(username, session_id)
    return _read(username, session_id)["history"]


def list_sessions(username: str) -> list[dict]:
    if db.enabled():
        return chat_store.list_sessions(username)
    with closing(_connect()) as conn, conn:
        rows = conn.execute(
            "SELECT session_id, state, updated_at FROM sessions WHERE username=? ORDER BY updated_at DESC", (username,)
        ).fetchall()
    return [
        {"session_id": r[0], "title": json.loads(r[1]).get("title") or "New chat", "updated_at": r[2]} for r in rows
    ]


def rename(username: str, session_id: str, title: str) -> bool:
    if db.enabled():
        return chat_store.rename(username, session_id, title)
    state = _read(username, session_id)
    if not state["history"]:
        return False
    state["title"] = chat_store.make_title(title)
    _write(username, session_id, state)
    return True


def delete(username: str, session_id: str) -> bool:
    if db.enabled():
        return chat_store.delete(username, session_id)
    with closing(_connect()) as conn, conn:
        return conn.execute(
            "DELETE FROM sessions WHERE username=? AND session_id=?", (username, session_id)
        ).rowcount > 0
