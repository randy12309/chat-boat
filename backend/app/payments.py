"""Outstanding premiums and (simulated) payment for the signed-in member.

Outstanding premiums are EPAYMENT rows still staged (EPMT_STATUS 'P'). Paying one confirms it as synced
('S') and approved ('A'), as the web payment flow does before the backend sweep picks it up.
"""
import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.auth import current_user
from mcp_server import cache, db, server

ACCOUNTS_FILE = Path(__file__).parent / "data" / "accounts.json"

router = APIRouter(prefix="/api/v1/payments", tags=["payments"])

# Only used when MySQL is off (demo fallback to accounts.json); shared with the MCP server so both agree.
_paid_fallback: set[str] = server._paid


def reset_fallback() -> None:
    _paid_fallback.clear()


def list_due(username: str) -> list[dict]:
    if db.enabled():
        return db.list_due(username)
    account = json.loads(ACCOUNTS_FILE.read_text(encoding="utf-8")).get(username)
    premium = (account or {}).get("premium")
    if not premium or premium.get("status") == "Paid" or username in _paid_fallback:
        return []
    return [{**premium, "payment_id": f"demo-{username}", "billing_period": premium.get("billing_period", "")}]


def pay(username: str, payment_ids: list[str]) -> list[dict]:
    """Pay the member's own outstanding items. Unknown, foreign or already-paid ids are rejected."""
    due = {d["payment_id"]: d for d in list_due(username)}
    if not payment_ids or any(pid not in due for pid in payment_ids):
        raise ValueError("invalid payment selection")
    paid = []
    for pid in dict.fromkeys(payment_ids):
        if db.enabled():
            reference = db.mark_paid_id(username, pid, db.payment_reference())
            if reference is None:
                raise ValueError("payment is no longer due")
        else:
            _paid_fallback.add(username)
            reference = db.payment_reference()
        cache.invalidate_account(username)
        paid.append({
            "payment_id": pid, "amount": due[pid]["amount"], "currency": due[pid]["currency"], "reference": reference
        })
    return paid


class PayRequest(BaseModel):
    payment_ids: list[str] = Field(min_length=1, max_length=24)
    authorized: bool


@router.get("/due")
def due(username: str = Depends(current_user)):
    return {"items": list_due(username)}


@router.post("/pay")
def pay_selected(body: PayRequest, username: str = Depends(current_user)):
    if not body.authorized:
        raise HTTPException(status_code=422, detail="Payment must be authorized")
    try:
        paid = pay(username, body.payment_ids)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"paid": paid, "total": round(sum(p["amount"] for p in paid), 2), "simulated": True}
