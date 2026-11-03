"""Self-registered users. Stored in MySQL (users table) when enabled, else in a local JSON file.

Passwords are only ever stored as bcrypt hashes. The seeded demo users in users.json are separate and unchanged.
"""
import json
import os
import threading
from pathlib import Path

import bcrypt

from mcp_server import db

_lock = threading.Lock()


def _file() -> Path:
    return Path(os.getenv("REGISTERED_USERS_FILE") or Path(__file__).parent / "data" / "registered_users.json")


def _read() -> dict:
    path = _file()
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def find(username: str) -> dict | None:
    """Return {username, email, display_name, password_hash} or None."""
    if db.enabled():
        return db.get_user(username)
    return _read().get(username)


def create(username: str, email: str, display_name: str, password: str) -> bool:
    """Create a user; False if the username or email already exists."""
    hashed = bcrypt.hashpw(password.encode()[:72], bcrypt.gensalt()).decode()
    if db.enabled():
        return db.create_user(username, email, display_name, hashed)
    with _lock:
        users = _read()
        if username in users or any(u["email"] == email for u in users.values()):
            return False
        users[username] = {"username": username, "email": email, "display_name": display_name, "password_hash": hashed}
        path = _file()
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(users, indent=2), encoding="utf-8")
        tmp.replace(path)
    return True


def verify(username: str, password: str) -> bool:
    user = find(username)
    if user is None:
        return False
    try:
        return bcrypt.checkpw(password.encode()[:72], user["password_hash"].encode())
    except ValueError:
        return False
