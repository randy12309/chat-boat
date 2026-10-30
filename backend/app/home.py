import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

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
        raise HTTPException(status_code=404, detail="Account not found")
    return {
        "welcome": {
            "display_name": account["display_name"],
            "message": "I'm your Plan Assistant. Ask me about premiums, coverage, or payments.",
        },
        "premium_alert": account["premium"],
        "inbox": account["inbox"],
        "suggested_questions": SIGNED_IN_QUESTIONS,
    }
