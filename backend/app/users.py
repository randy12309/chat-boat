"""Member profiles and password checks.

When MySQL is enabled the authoritative record is the BMOWNER pair USER_ACCOUNT (login fields, including
ENC_PASSWORD) and users_profile (the display name, which MDM owns in the legacy system). Without MySQL the
same shape is kept in a local JSON file, and the seeded demo logins stay in users.json.
"""
import json
import os
import threading
from pathlib import Path

import bcrypt

from mcp_server import db

_lock = threading.Lock()

# bcrypt hashes only the first 72 bytes; longer passwords are truncated rather than rejected.
MAX_PASSWORD_BYTES = 72


def _file() -> Path:
    return Path(os.getenv("REGISTERED_USERS_FILE") or Path(__file__).parent / "data" / "registered_users.json")


def _read() -> dict:
    path = _file()
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode()[:MAX_PASSWORD_BYTES], bcrypt.gensalt()).decode()


def check(password: str, hashed: str) -> bool:
    """Compare a password against a stored bcrypt hash; False if the hash is not one we can read."""
    try:
        return bcrypt.checkpw(password.encode()[:MAX_PASSWORD_BYTES], hashed.encode())
    except (ValueError, TypeError):
        return False


def find(username: str) -> dict | None:
    """Return {username, email, display_name, password_hash} or None."""
    if db.enabled():
        return db.get_user(username)
    return _read().get(username)


def create(username: str, email: str, display_name: str, password: str) -> bool:
    """Register a member; False if the username or email already exists."""
    hashed = hash_password(password)
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
    if user is None or not user.get("password_hash"):
        return False
    return check(password, user["password_hash"])
