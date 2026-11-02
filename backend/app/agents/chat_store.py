"""MySQL-backed chat sessions: conversations, ordered messages, and per-conversation agent memory.

Everything is scoped by the authenticated user's id, so a session can only be read by its owner.
"""
import json
import re
import uuid

from mcp_server import db

MAX_CONTEXT = 20  # messages handed back to the agent as short-term context
MAX_SESSIONS = 200
TITLE_MAX = 60


def make_title(text: str) -> str:
    title = re.sub(r"\s+", " ", text).strip()
    return (title[: TITLE_MAX - 1] + "…") if len(title) > TITLE_MAX else (title or "New chat")


def _user_id(cur, username: str) -> str | None:
    cur.execute("SELECT id FROM users WHERE username = %s", (username,))
    row = cur.fetchone()
    return row["id"] if row else None


def _conversation(cur, user_id: str, session_id: str, lock: bool = False) -> dict | None:
    cur.execute(
        "SELECT id, title FROM conversations WHERE user_id = %s AND session_id = %s" + (" FOR UPDATE" if lock else ""),
        (user_id, session_id),
    )
    return cur.fetchone()


def load(username: str, session_id: str) -> dict:
    empty = {"history": [], "pending_quote": None}
    with db.connection() as conn, conn.cursor() as cur:
        user_id = _user_id(cur, username)
        conv = _conversation(cur, user_id, session_id) if user_id else None
        if conv is None:
            return empty
        cur.execute(
            "SELECT role, content, sources FROM messages WHERE conversation_id = %s "
            "ORDER BY sequence_number DESC LIMIT %s",
            (conv["id"], MAX_CONTEXT),
        )
        rows = cur.fetchall()[::-1]
        cur.execute("SELECT state FROM conversation_memory WHERE conversation_id = %s", (conv["id"],))
        memory = cur.fetchone()
    history = []
    for r in rows:
        item = {"role": r["role"], "content": r["content"]}
        if r["role"] == "assistant":
            item["sources"] = json.loads(r["sources"]) if r["sources"] else []
        history.append(item)
    state = json.loads(memory["state"]) if memory else {}
    return {"history": history, "pending_quote": state.get("pending_quote")}


def append_turn(username: str, session_id: str, user_text: str, reply: str, sources: list, pending_quote) -> None:
    with db.connection() as conn, conn.cursor() as cur:
        user_id = _user_id(cur, username)
        if user_id is None:
            return  # not a known user: nothing can be persisted for them
        conv = _conversation(cur, user_id, session_id, lock=True)
        if conv is None:
            conv_id = str(uuid.uuid4())
            cur.execute(
                "INSERT INTO conversations (id, user_id, session_id, title) VALUES (%s, %s, %s, %s)",
                (conv_id, user_id, session_id, make_title(user_text)),
            )
        else:
            conv_id = conv["id"]
        cur.execute("SELECT COALESCE(MAX(sequence_number), 0) AS n FROM messages WHERE conversation_id = %s", (conv_id,))
        seq = int(cur.fetchone()["n"])
        cur.execute(
            "INSERT INTO messages (conversation_id, sequence_number, role, content, sources) VALUES "
            "(%s, %s, 'user', %s, NULL), (%s, %s, 'assistant', %s, %s)",
            (conv_id, seq + 1, user_text, conv_id, seq + 2, reply, json.dumps(sources)),
        )
        cur.execute(
            "INSERT INTO conversation_memory (conversation_id, state) VALUES (%s, %s) "
            "ON DUPLICATE KEY UPDATE state = VALUES(state)",
            (conv_id, json.dumps({"pending_quote": pending_quote})),
        )
        cur.execute("UPDATE conversations SET updated_at = NOW() WHERE id = %s", (conv_id,))


def get_history(username: str, session_id: str) -> list[dict]:
    with db.connection() as conn, conn.cursor() as cur:
        user_id = _user_id(cur, username)
        conv = _conversation(cur, user_id, session_id) if user_id else None
        if conv is None:
            return []
        cur.execute(
            "SELECT role, content, sources, UNIX_TIMESTAMP(created_at) AS ts FROM messages "
            "WHERE conversation_id = %s ORDER BY sequence_number",
            (conv["id"],),
        )
        rows = cur.fetchall()
    return [
        {
            "role": r["role"],
            "content": r["content"],
            "sources": (json.loads(r["sources"]) if r["sources"] else []) if r["role"] == "assistant" else [],
            "time": float(r["ts"]),
        }
        for r in rows
    ]


def list_sessions(username: str) -> list[dict]:
    with db.connection() as conn, conn.cursor() as cur:
        user_id = _user_id(cur, username)
        if user_id is None:
            return []
        cur.execute(
            "SELECT session_id, title, UNIX_TIMESTAMP(updated_at) AS ts FROM conversations "
            "WHERE user_id = %s AND status = 'active' ORDER BY updated_at DESC, created_at DESC LIMIT %s",
            (user_id, MAX_SESSIONS),
        )
        rows = cur.fetchall()
    return [{"session_id": r["session_id"], "title": r["title"], "updated_at": float(r["ts"])} for r in rows]


def rename(username: str, session_id: str, title: str) -> bool:
    with db.connection() as conn, conn.cursor() as cur:
        user_id = _user_id(cur, username)
        if user_id is None:
            return False
        cur.execute(
            "UPDATE conversations SET title = %s WHERE user_id = %s AND session_id = %s",
            (make_title(title), user_id, session_id),
        )
        return _conversation(cur, user_id, session_id) is not None


def delete(username: str, session_id: str) -> bool:
    with db.connection() as conn, conn.cursor() as cur:
        user_id = _user_id(cur, username)
        if user_id is None:
            return False
        cur.execute("DELETE FROM conversations WHERE user_id = %s AND session_id = %s", (user_id, session_id))
        return cur.rowcount > 0
