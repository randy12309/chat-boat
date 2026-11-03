"""FastMCP tool service. Run standalone with: python -m mcp_server.server

Identity is never a model-supplied argument: account tools take a short-lived
`principal` token minted by the backend and verified here.
"""
import json
import math
import os
import secrets
import time
from pathlib import Path
from typing import Any

import jwt
from dotenv import load_dotenv
from fastmcp import FastMCP

from mcp_server import db, pgvec

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

ACCOUNTS_FILE = Path(__file__).parent.parent / "app" / "data" / "accounts.json"
MCP_SECRET = os.getenv("MCP_INTERNAL_SECRET", "dev-only-mcp-secret-change-me-0123456789")
PRINCIPAL_TTL_SECONDS = 60
QUOTE_TTL_SECONDS = 300
POLICY_MAX_DISTANCE = float(os.getenv("POLICY_MAX_DISTANCE", "0.55"))

mcp = FastMCP("plan-assistant-tools")

# Simulated payment state; the authoritative store is the backend in a later phase.
_paid: set[str] = set()
_quotes: dict[str, dict[str, Any]] = {}


def _plan_settings() -> dict[str, Any] | None:
    """Plan numbers from the database (pgvector database); None if unavailable or incomplete."""
    try:
        raw = pgvec.get_settings()
        return {
            "eligibility_threshold": float(raw["eligibility_threshold"]),
            "quarterly_premium": float(raw["quarterly_premium"]),
            "base_coverage_ratio": float(raw["base_coverage_ratio"]),
            "currency": raw["currency"],
        }
    except Exception:
        return None


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
    if db.enabled():
        return db.get_premium(username)
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
    """Return public policy information for a topic, matched by name or by meaning. No authentication required."""
    try:
        found = pgvec.find_policy(topic, POLICY_MAX_DISTANCE)
    except Exception:
        return _err("unavailable", "Policy store is unavailable.")
    if found is None:
        return _err("not_found", "No policy information for that topic.")
    return _ok({"topic": found["topic"], "text": found["text"]}, f"policy:{found['topic']}")


@mcp.tool
def explain_premium(principal: str) -> dict:
    """Return the caller's premium with its component breakdown."""
    user = _authenticate(principal)
    if user is None:
        return _err("unauthorized", "Invalid or expired principal.")
    premium = _premium(user)
    if premium is None:
        return _err("not_found", "No account found.")
    plan = _plan_settings()
    if plan is None:
        return _err("unavailable", "Plan settings are unavailable.")
    amount = premium["amount"]
    base = round(amount * plan["base_coverage_ratio"], 2)
    breakdown = {"base_coverage": base, "taxes_and_fees": round(amount - base, 2)}
    return _ok({**premium, "breakdown": breakdown}, "accounts")


@mcp.tool
def get_eligibility(principal: str) -> dict:
    """Return whether the caller's total paid premiums reach the insurance and pension threshold."""
    user = _authenticate(principal)
    if user is None:
        return _err("unauthorized", "Invalid or expired principal.")
    plan = _plan_settings()
    if plan is None:
        return _err("unavailable", "Plan settings are unavailable.")
    threshold, quarterly = plan["eligibility_threshold"], plan["quarterly_premium"]
    if db.enabled():
        minor = db.total_paid_minor(user)
        if minor is None:
            return _err("not_found", "No account found.")
        total_paid = minor / 100
    else:
        premium = _premium(user)
        if premium is None:
            return _err("not_found", "No account found.")
        total_paid = premium["amount"] if user in _paid else 0.0
    remaining = max(threshold - total_paid, 0.0)
    return _ok(
        {
            "eligible": total_paid >= threshold,
            "total_paid": total_paid,
            "threshold": threshold,
            "remaining": remaining,
            "quarters_remaining": math.ceil(remaining / quarterly),
            "quarterly_premium": quarterly,
            "currency": plan["currency"],
        },
        "eligibility",
    )


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
    if db.enabled():
        reference = db.mark_paid(user, quote_id)
        if reference is None:
            return _err("nothing_due", "No premium is currently due.")
        return _ok(
            {"amount": quote["amount"], "status": "Paid", "simulated": True, "reference": reference},
            "payment-simulation",
        )
    _paid.add(user)
    return _ok({"amount": quote["amount"], "status": "Paid", "simulated": True}, "payment-simulation")


def reset_state() -> None:
    _paid.clear()
    _quotes.clear()


if __name__ == "__main__":
    if pgvec.enabled():
        try:
            pgvec.ensure_ready()
        except Exception as exc:
            print(f"Warning: could not prepare the pgvector database: {exc}")
    mcp.run(transport="http", host="127.0.0.1", port=int(os.getenv("MCP_PORT", "8765")))
