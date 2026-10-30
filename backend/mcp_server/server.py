"""FastMCP tool service. Run standalone with: python -m mcp_server.server

Identity is never a model-supplied argument: account tools take a short-lived
`principal` token minted by the backend and verified here.
"""
import json
import os
import secrets
import time
from pathlib import Path
from typing import Any

import jwt
from dotenv import load_dotenv
from fastmcp import FastMCP

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

ACCOUNTS_FILE = Path(__file__).parent.parent / "app" / "data" / "accounts.json"
MCP_SECRET = os.getenv("MCP_INTERNAL_SECRET", "dev-only-mcp-secret-change-me-0123456789")
PRINCIPAL_TTL_SECONDS = 60
QUOTE_TTL_SECONDS = 300

POLICY_TOPICS = {
    "grace period": "A 30-day grace period applies after the premium due date before coverage lapses.",
    "renewal": "Plans renew annually; renewal options are sent at least 45 days before the plan end date.",
    "dependents": "Eligible dependents can be added within 30 days of a qualifying life event.",
    "cancellation": "You may cancel with 30 days' written notice; unused premium is refunded pro rata.",
}

mcp = FastMCP("plan-assistant-tools")

# Simulated payment state; the authoritative store is the backend in a later phase.
_paid: set[str] = set()
_quotes: dict[str, dict[str, Any]] = {}


def mint_principal(username: str) -> str:
    now = int(time.time())
    return jwt.encode(
        {"sub": username, "aud": "mcp", "iat": now, "exp": now + PRINCIPAL_TTL_SECONDS},
        MCP_SECRET,
        algorithm="HS256",
    )


def _ok(data: dict, source: str) -> dict:
    return {"status": "ok", "source": source, "data": data}


def _err(error_type: str, message: str) -> dict:
    return {"status": "error", "error_type": error_type, "message": message}


def _authenticate(principal: str) -> str | None:
    try:
        claims = jwt.decode(principal, MCP_SECRET, algorithms=["HS256"], audience="mcp")
    except jwt.PyJWTError:
        return None
    return claims.get("sub")


def _premium(username: str) -> dict | None:
    accounts = json.loads(ACCOUNTS_FILE.read_text(encoding="utf-8"))
    account = accounts.get(username)
    if account is None:
        return None
    premium = dict(account["premium"])
    if username in _paid:
        premium["status"] = "Paid"
    return premium


@mcp.tool
def get_balance(principal: str) -> dict:
    """Return the caller's current premium balance and status."""
    user = _authenticate(principal)
    if user is None:
        return _err("unauthorized", "Invalid or expired principal.")
    premium = _premium(user)
    if premium is None:
        return _err("not_found", "No account found.")
    return _ok(premium, "accounts")


@mcp.tool
def get_policy_info(topic: str) -> dict:
    """Return public policy information for a topic. No authentication required."""
    key = topic.strip().lower()[:100]
    for name, text in POLICY_TOPICS.items():
        if name in key or key in name:
            return _ok({"topic": name, "text": text}, f"policy:{name}")
    return _err("not_found", "No policy information for that topic.")


@mcp.tool
def explain_premium(principal: str) -> dict:
    """Return the caller's premium with its component breakdown."""
    user = _authenticate(principal)
    if user is None:
        return _err("unauthorized", "Invalid or expired principal.")
    premium = _premium(user)
    if premium is None:
        return _err("not_found", "No account found.")
    amount = premium["amount"]
    base = round(amount * 0.85, 2)
    breakdown = {"base_coverage": base, "taxes_and_fees": round(amount - base, 2)}
    return _ok({**premium, "breakdown": breakdown}, "accounts")


@mcp.tool
def quote_payment(principal: str) -> dict:
    """Create a single-use quote to pay the caller's outstanding premium. Moves no money."""
    user = _authenticate(principal)
    if user is None:
        return _err("unauthorized", "Invalid or expired principal.")
    premium = _premium(user)
    if premium is None:
        return _err("not_found", "No account found.")
    if premium["status"] == "Paid":
        return _err("nothing_due", "No premium is currently due.")
    quote_id = secrets.token_urlsafe(12)
    _quotes[quote_id] = {"user": user, "amount": premium["amount"], "expires": time.time() + QUOTE_TTL_SECONDS}
    return _ok(
        {"quote_id": quote_id, "amount": premium["amount"], "currency": premium["currency"]},
        "payment-quote",
    )


@mcp.tool
def execute_payment(principal: str, quote_id: str) -> dict:
    """Execute a simulated payment for a quote owned by the caller. No live processor."""
    user = _authenticate(principal)
    if user is None:
        return _err("unauthorized", "Invalid or expired principal.")
    quote = _quotes.pop(quote_id, None)
    if quote is None or quote["user"] != user:
        return _err("invalid_quote", "Quote is unknown or already used.")
    if quote["expires"] < time.time():
        return _err("expired_quote", "Quote has expired.")
    _paid.add(user)
    return _ok({"amount": quote["amount"], "status": "Paid", "simulated": True}, "payment-simulation")


def reset_state() -> None:
    _paid.clear()
    _quotes.clear()


if __name__ == "__main__":
    mcp.run(transport="http", host="127.0.0.1", port=int(os.getenv("MCP_PORT", "8765")))
