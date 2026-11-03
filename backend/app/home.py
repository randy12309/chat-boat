import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from app import users
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


@router.get("")
def home(username: str = Depends(current_user)):
    account = json.loads(ACCOUNTS_FILE.read_text(encoding="utf-8")).get(username)
    if account is None:
        # Self-registered member: no seeded account, so show their own (empty) summary.
        user = users.find(username)
        if user is None:
            raise HTTPException(status_code=404, detail="Account not found")
        premium = db.get_premium(username) if db.enabled() else None
        account = {
            "display_name": user["display_name"],
            "premium": premium if premium and premium.get("due_date") else None,
            "inbox": {"unread_count": 0, "latest_subject": None},
        }
    return {
        "welcome": {
            "display_name": account["display_name"],
            "message": "I'm your Plan Assistant. Ask me about premiums, coverage, or payments.",
        },
        "premium_alert": account["premium"],
        "inbox": account["inbox"],
        "suggested_questions": SIGNED_IN_QUESTIONS,
    }
