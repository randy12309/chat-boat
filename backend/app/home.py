import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from app import payments, users
from app.auth import current_user
from mcp_server import db

ACCOUNTS_FILE = Path(__file__).parent / "data" / "accounts.json"

router = APIRouter(prefix="/api/v1/home", tags=["home"])

SIGNED_IN_QUESTIONS = [
    "What is my current premium balance?",
    "Why did my premium change?",
    "I want to pay my premium",
    "What does my plan cover?",
]

EMPTY_INBOX = {"unread_count": 0, "latest_subject": None}


def _account(username: str) -> dict | None:
    """The member's display name and inbox, or None when they are unknown.

    The name comes from USER_ACCOUNT + users_profile. There is no legacy inbox table, so the demo inbox in
    accounts.json is shown when present and an empty one otherwise.
    """
    demo = json.loads(ACCOUNTS_FILE.read_text(encoding="utf-8")).get(username)
    if db.enabled():
        profile = db.get_profile(username)
        if profile is None:
            return None
        return {
            "display_name": profile.get("display_name") or profile.get("username") or username,
            "inbox": (demo or {}).get("inbox") or EMPTY_INBOX,
        }
    if demo is None:
        member = users.find(username)
        if member is None:
            return None
        return {"display_name": member["display_name"], "inbox": EMPTY_INBOX}
    return {"display_name": demo["display_name"], "inbox": demo["inbox"]}


@router.get("")
def home(username: str = Depends(current_user)):
    account = _account(username)
    if account is None:
        # Self-registered members with no participant row must get a clear account-not-found answer.
        raise HTTPException(status_code=404, detail="Account not found")
    outstanding = payments.list_due(username)
    return {
        "welcome": {
            "display_name": account["display_name"],
            "message": "I'm your Plan Assistant. Ask me about premiums, coverage, or payments.",
        },
        # The action card appears only while a premium is actually outstanding; otherwise the member sees none.
        "premium_alert": outstanding[0] if outstanding else None,
        "inbox": account["inbox"],
        "suggested_questions": SIGNED_IN_QUESTIONS,
    }
