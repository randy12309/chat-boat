"""MySQL storage for the agent flow of each chat turn (tables agent_turns and agent_events)."""
import logging
import os

from mcp_server import db

logger = logging.getLogger(__name__)

# Which tool functions each agent may use, and who it can hand work to. Shown in the admin console.
AGENT_REGISTRY = {
    "supervisor": {
        "role": "Reads the question and assigns it to an agent, or answers small talk itself.",
        "tools": ["route_request"],
        "assigns_to": ["policy_agent", "account_agent", "payment_agent", "sign_in"],
    },
    "policy_agent": {
        "role": "Answers plan-policy questions from the cache and Qdrant, then the policy tool.",
        "tools": ["get_policy_info"],
        "assigns_to": [],
    },
    "account_agent": {
        "role": "Reads the signed-in member's own account data: premiums, eligibility, saved payment methods and payment history.",
        "tools": ["get_balance", "explain_premium", "get_eligibility", "list_payment_methods", "list_epayments"],
        "assigns_to": [],
    },
    "payment_agent": {
        "role": "Checks what is due and asks the member to confirm. Never moves money.",
        "tools": ["get_due_payments"],
        "assigns_to": [],
    },
}


def enabled() -> bool:
    return db.enabled()


def save_turn(record: dict) -> None:
    """Insert one turn and its ordered steps in a single transaction."""
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agent_turns (id, username, session_id, question, final_agent, sources, duration_ms) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (
                record["id"], record["username"], record["session_id"], record["question"],
                record["final_agent"], record["sources"], record["duration_ms"],
            ),
        )
        if record["steps"]:
            cur.executemany(
                "INSERT INTO agent_events (turn_id, step, event_type, from_actor, to_actor, tool_name, status, storage_backend, detail, "
                "elapsed_ms, duration_ms) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                [
                    (
                        record["id"], s["step"], s["event_type"], s["from_actor"], s["to_actor"], s["tool_name"],
                        s["status"], s.get("storage_backend"), s["detail"], s["elapsed_ms"], s["duration_ms"],
                    )
                    for s in record["steps"]
                ],
            )


def purge(days: int | None = None) -> int:
    """Delete flows older than the retention period (AGENT_FLOW_RETENTION_DAYS, default 90). Returns rows removed."""
    days = int(os.getenv("AGENT_FLOW_RETENTION_DAYS", "90")) if days is None else days
    if days <= 0:
        return 0
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM agent_turns WHERE created_at < NOW() - INTERVAL %s DAY", (days,))
        return cur.rowcount
