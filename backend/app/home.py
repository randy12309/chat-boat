import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from app import payments, users
from app.auth import current_user

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
        account = {
            "display_name": user["display_name"],
            "inbox": {"unread_count": 0, "latest_subject": None},
        }
    outstanding = payments.list_due(username)
    return {
        "welcome": {
            "display_name": account["display_name"],
            "message": "I'm your Plan Assistant. Ask me about premiums, coverage, or payments.",
        },
        # The action card appears only while something is actually outstanding; paid members see none.
        "premium_alert": outstanding[0] if outstanding else None,
        "inbox": account["inbox"],
        "suggested_questions": SIGNED_IN_QUESTIONS,
    }
