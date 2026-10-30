from typing import Literal, TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from app.agents.llm import classify_intent
from app.agents.mcp_client import call_tool
from mcp_server.server import POLICY_TOPICS, mint_principal

Intent = Literal["balance", "policy", "explain", "pay", "confirm", "cancel", "unknown"]

SIGN_IN_REPLY = "Please sign in to access your account. I can still answer general policy questions."
FALLBACK_REPLY = "Sorry, I couldn't complete that right now. Please try again shortly."
UNKNOWN_REPLY = (
    "I can help with your premium balance, explaining your premium, policy questions "
    "(grace period, renewal, dependents, cancellation), and paying your premium."
)


class AgentState(TypedDict, total=False):
    # Per-turn input/output
    message: str
    username: str | None
    intent: Intent
    reply: str
    sources: list[str]
    # Persisted across turns by the checkpointer
    pending_quote: dict | None


def classify(message: str, has_pending: bool) -> Intent:
    text = message.lower().strip()
    if has_pending:
        if text in {"confirm", "yes", "y", "confirm payment", "yes, pay", "yes pay"}:
            return "confirm"
        if text in {"cancel", "no", "n", "stop"}:
            return "cancel"
    if "pay" in text:
        return "pay"
    if any(w in text for w in ("explain", "breakdown", "why")):
        return "explain"
    if "policy" in text or any(t in text for t in POLICY_TOPICS):
        return "policy"
    if any(w in text for w in ("balance", "premium", "due", "owe")):
        return "balance"
    return "unknown"


async def supervisor(state: AgentState) -> dict:
    has_pending = bool(state.get("pending_quote"))
    intent = classify(state["message"], has_pending)
    # Confirm/cancel stay deterministic; the model only picks among the four read/start intents.
    if intent in ("unknown", "balance", "policy", "explain") and not (has_pending and intent in ("confirm", "cancel")):
        intent = await classify_intent(state["message"]) or intent
    update: dict = {"intent": intent, "sources": [], "reply": ""}
    # Any message other than an explicit confirm/cancel invalidates a pending payment.
    if intent not in ("confirm", "cancel") and state.get("pending_quote"):
        update["pending_quote"] = None
    return update


ACCOUNT_INTENTS = {"balance", "explain", "pay", "confirm", "cancel"}


def route(state: AgentState) -> str:
    intent = state["intent"]
    if intent in ACCOUNT_INTENTS and not state.get("username"):
        return "sign_in"
    return {"unknown": "fallback"}.get(intent, intent)


def _principal(state: AgentState) -> dict:
    return {"principal": mint_principal(state["username"])}


def _failed(result: dict) -> dict:
    if result["error_type"] == "nothing_due":
        return {"reply": "You have no premium due right now."}
    return {"reply": FALLBACK_REPLY}


async def balance_node(state: AgentState) -> dict:
    r = await call_tool("get_balance", _principal(state))
    if r["status"] != "ok":
        return _failed(r)
    d = r["data"]
    return {
        "reply": f"Your premium is {d['amount']:.2f} {d['currency']}, due {d['due_date']} (status: {d['status']}).",
        "sources": [r["source"]],
    }


async def explain_node(state: AgentState) -> dict:
    r = await call_tool("explain_premium", _principal(state))
    if r["status"] != "ok":
        return _failed(r)
    d = r["data"]
    b = d["breakdown"]
    return {
        "reply": (
            f"Your premium of {d['amount']:.2f} {d['currency']} is made up of "
            f"{b['base_coverage']:.2f} base coverage and {b['taxes_and_fees']:.2f} taxes and fees."
        ),
        "sources": [r["source"]],
    }


async def policy_node(state: AgentState) -> dict:
    r = await call_tool("get_policy_info", {"topic": state["message"]})
    if r["status"] != "ok":
        if r["error_type"] == "not_found":
            return {"reply": "I don't have policy information on that topic."}
        return _failed(r)
    return {"reply": r["data"]["text"], "sources": [r["source"]]}


async def pay_node(state: AgentState) -> dict:
    r = await call_tool("quote_payment", _principal(state))
    if r["status"] != "ok":
        return _failed(r)
    d = r["data"]
    return {
        "pending_quote": {"quote_id": d["quote_id"], "amount": d["amount"], "currency": d["currency"]},
        "reply": f"You are about to pay {d['amount']:.2f} {d['currency']}. Reply 'confirm' to proceed or 'cancel' to stop.",
        "sources": [r["source"]],
    }


async def confirm_node(state: AgentState) -> dict:
    quote = state["pending_quote"]
    r = await call_tool("execute_payment", {**_principal(state), "quote_id": quote["quote_id"]})
    if r["status"] != "ok":
        return {**_failed(r), "pending_quote": None}
    return {
        "pending_quote": None,
        "reply": f"Payment of {quote['amount']:.2f} {quote['currency']} completed (demo payment).",
        "sources": [r["source"]],
    }


def cancel_node(state: AgentState) -> dict:
    return {"pending_quote": None, "reply": "Payment cancelled. Nothing was charged."}


def sign_in_node(state: AgentState) -> dict:
    return {"reply": SIGN_IN_REPLY}


def fallback_node(state: AgentState) -> dict:
    return {"reply": UNKNOWN_REPLY}


def build_graph(checkpointer=None):
    g = StateGraph(AgentState)
    g.add_node("supervisor", supervisor)
    nodes = {
        "balance": balance_node,
        "explain": explain_node,
        "policy": policy_node,
        "pay": pay_node,
        "confirm": confirm_node,
        "cancel": cancel_node,
        "sign_in": sign_in_node,
        "fallback": fallback_node,
    }
    for name, fn in nodes.items():
        g.add_node(name, fn)
        g.add_edge(name, END)
    g.add_edge(START, "supervisor")
    g.add_conditional_edges("supervisor", route, list(nodes))
    return g.compile(checkpointer=checkpointer)


graph = build_graph(MemorySaver())


async def run_agent(username: str | None, message: str) -> dict:
    # Thread id derives only from the authenticated identity, so a checkpoint can't be loaded across users.
    thread = f"user:{username}" if username else "anonymous"
    config = {"configurable": {"thread_id": thread}, "recursion_limit": 10}
    if username is None:
        # Anonymous requests never share or restore state.
        result = await build_graph().ainvoke({"message": message, "username": None}, config=config)
    else:
        result = await graph.ainvoke({"message": message, "username": username}, config=config)
    return {"reply": result["reply"], "sources": result.get("sources", [])}
