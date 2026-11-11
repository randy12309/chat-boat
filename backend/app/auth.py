import hmac
import json
import logging
import os
import time
from pathlib import Path

import jwt
from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from app import users
from mcp_server import db

logger = logging.getLogger(__name__)

USERS_FILE = Path(__file__).parent / "data" / "users.json"
JWT_SECRET = os.getenv("JWT_SECRET", "dev-only-secret-change-me-0123456789")
TOKEN_TTL_SECONDS = int(os.getenv("TOKEN_TTL_SECONDS", "3600"))

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
bearer = HTTPBearer(auto_error=False)


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    username: str


def load_users() -> dict:
    return json.loads(USERS_FILE.read_text(encoding="utf-8"))


def validate_credentials(username: str, password: str) -> bool:
    """Authenticate against USER_ACCOUNT.ENC_PASSWORD, then the demo and self-registered JSON stores.

    The participant table is authoritative when MySQL is configured; the JSON stores keep local development
    and the offline demo working without a database.
    """
    if db.enabled():
        try:
            member = db.get_user(username)
        except Exception:
            logger.warning("Could not read the participant table", exc_info=True)
            member = None
        if member and member.get("password_hash"):
            return users.check(password, member["password_hash"])
    stored = load_users().get(username)
    if stored is not None:
        return hmac.compare_digest(stored.encode(), password.encode())
    return users.verify(username, password)


def create_token(username: str) -> str:
    now = int(time.time())
    return jwt.encode(
        {"sub": username, "iat": now, "exp": now + TOKEN_TTL_SECONDS},
        JWT_SECRET,
        algorithm="HS256",
    )


def current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> str:
    if creds is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        return jwt.decode(creds.credentials, JWT_SECRET, algorithms=["HS256"])["sub"]
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")


def admin_usernames() -> set[str]:
    return {a.strip().lower() for a in os.getenv("ADMIN_USERS", "").split(",") if a.strip()}


def is_admin(username: str) -> bool:
    """An administrator is a username in ADMIN_USERS or a row in the MySQL admins table."""
    if username.lower() in admin_usernames():
        return True
    if not db.enabled():
        return False
    try:
        return db.is_admin(username.lower())
    except Exception:
        logger.warning("Could not read the admins table", exc_info=True)
        return False


def admin_user(username: str = Depends(current_user)) -> str:
    """Admin endpoints expose or change shared data, so only administrators may call them."""
    if not is_admin(username):
        raise HTTPException(status_code=403, detail="Administrator access required")
    return username


@router.post("/login", response_model=LoginResponse)
def login(body: LoginRequest):
    if not validate_credentials(body.username, body.password):
        raise HTTPException(status_code=401, detail="Invalid username or password")
    return LoginResponse(
        access_token=create_token(body.username),
        expires_in=TOKEN_TTL_SECONDS,
        username=body.username,
    )


class RegisterRequest(BaseModel):
    username: str = Field(pattern=r"^[A-Za-z0-9_]{3,30}$")
    email: str = Field(max_length=255, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    display_name: str = Field(min_length=1, max_length=50)
    password: str = Field(min_length=8, max_length=72)


@router.post("/register", response_model=LoginResponse, status_code=201)
def register(body: RegisterRequest):
    username = body.username.lower()
    display_name = body.display_name.strip()
    if not display_name:
        raise HTTPException(status_code=422, detail="Display name is required")
    if username in load_users() or not users.create(username, body.email.strip().lower(), display_name, body.password):
        raise HTTPException(status_code=409, detail="That username or email is already registered")
    return LoginResponse(access_token=create_token(username), expires_in=TOKEN_TTL_SECONDS, username=username)


@router.get("/me")
def me(username: str = Depends(current_user)):
    return {"username": username}
