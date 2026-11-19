"""Readable per-question trace for demos: question -> supervisor -> agent -> MCP tool -> answer.

Prints one line per step to the backend console (TRACE_LOG=0 turns that off) and records each step of the turn as
structured flow data (who handed what to whom, which tool function ran, its status) that is saved to MySQL when the
turn ends (see flow_store). Member identity is shown as a username only; principals, tokens and secrets are never
logged or stored.
"""
import concurrent.futures
import contextvars
import itertools
import logging
import os
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field

from app.agents import flow_store

_log = logging.getLogger("plan.trace")
_log.setLevel(logging.INFO)
_log.propagate = False  # keep these lines out of uvicorn's own log format
if not _log.handlers:
    _handler = logging.StreamHandler(sys.stdout)
    _handler.setFormatter(logging.Formatter("%(message)s"))
    _log.addHandler(_handler)

_turn = contextvars.ContextVar("trace_turn", default="-")
_started = contextvars.ContextVar("trace_started", default=0.0)
_flow = contextvars.ContextVar("trace_flow", default=None)
_agent = contextvars.ContextVar("trace_agent", default="supervisor")
_counter = itertools.count(1)
_MAX = 110
MAX_PENDING_SAVES = 1000

# Console labels -> the agent name stored in the flow.
ACTORS = {
    "USER": "user", "SUPERVISOR": "supervisor", "POLICY": "policy_agent", "ACCOUNT": "account_agent",
    "PAYMENT": "payment_agent", "JOIN": "join", "SIGN-IN": "sign_in", "MCP": "mcp_server",
}


@dataclass
class Flow:
    """Everything that happened while answering one question."""

    id: str
    username: str | None
    session_id: str | None
    question: str
    started: float = field(default_factory=time.perf_counter)
    steps: list = field(default_factory=list)
    _next: itertools.count = field(default_factory=lambda: itertools.count(1))
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def add(self, step: dict) -> None:
        with self._lock:
            step["step"] = next(self._next)
            self.steps.append(step)


_executor = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix="flow-store")
_pending: set = set()


def enabled() -> bool:
    return os.getenv("TRACE_LOG", "1") != "0"


def _short(value: object) -> str:
    text = " ".join(str(value).split())
    text = text.encode("ascii", "replace").decode()  # Windows consoles (cp1252) cannot print every character
    return text if len(text) <= _MAX else text[: _MAX - 3] + "..."


def set_agent(name: str) -> None:
    """Name the agent running in this task, so MCP tool calls are attributed to the agent that made them."""
    _agent.set(name)


def current_agent() -> str:
    return _agent.get()


def current_flow() -> Flow | None:
    return _flow.get()


def event(
    actor: str,
    message: str,
    *,
    kind: str | None = None,
    to: str | None = None,
    tool: str | None = None,
    status: str | None = None,
    ms: int | None = None,
    src: str | None = None,
    storage: str | None = None,
    similarity: float | None = None,
    threshold: float | None = None,
    match_type: str | None = None,
) -> None:
    """actor is a short column label such as SUPERVISOR, POLICY, ACCOUNT, PAYMENT, MCP.

    With `kind` the step is also recorded in the turn's flow: `src` (default: the actor) handed it to `to`, optionally
    through the function `tool`, with the outcome `status` after `ms` milliseconds.
    """
    flow = _flow.get()
    if kind and flow is not None:
        flow.add({
            "event_type": kind,
            "from_actor": src or ACTORS.get(actor, actor.lower()),
            "to_actor": to or "",
            "tool_name": tool,
            "status": status,
            "storage_backend": storage,
            "similarity": similarity,
            "threshold": threshold,
            "match_type": match_type,
            "detail": " ".join(message.split()).replace("\x00", "")[:500],
            "elapsed_ms": int((time.perf_counter() - flow.started) * 1000),
            "duration_ms": ms,
        })
    if not enabled():
        return
    elapsed = time.perf_counter() - _started.get() if _started.get() else 0.0
    _log.info("%s  #%-3s %+6.2fs  %-10s %s", time.strftime("%H:%M:%S"), _turn.get(), elapsed, actor, message)


def start_turn(username: str | None, message: str, session_id: str | None = None) -> None:
    _turn.set(str(next(_counter)))
    _started.set(time.perf_counter())
    _flow.set(Flow(id=str(uuid.uuid4()), username=username, session_id=session_id, question=message[:2000]))
    _agent.set("supervisor")
    if enabled():
        _log.info("")
    event("USER", f'{username or "anonymous"} asked: "{_short(message)}"', kind="question", to="supervisor",
          status="received")


def end_turn(result: dict) -> None:
    sources = ", ".join(result.get("sources") or []) or "none"
    agent = result.get("agent") or "supervisor"
    event("ANSWER", f'by {agent} | sources: {sources} | "{_short(result.get("reply", ""))}"',
          kind="answer", src=ACTORS.get(agent.upper(), agent), to="user", status="sent")
    flow = _flow.get()
    if flow is None:
        return
    _submit({
        "id": flow.id, "username": flow.username, "session_id": flow.session_id, "question": flow.question,
        "final_agent": result.get("agent"), "sources": sources[:500] if sources != "none" else None,
        "duration_ms": int((time.perf_counter() - flow.started) * 1000), "steps": list(flow.steps),
    })


def _save(record: dict) -> None:
    try:
        flow_store.save_turn(record)
    except Exception:
        logging.getLogger(__name__).warning("Could not save the agent flow", exc_info=True)


def _submit(record: dict) -> None:
    """Save the flow in the background so the member's answer is never delayed by the database."""
    if not flow_store.enabled():
        return
    if len(_pending) >= MAX_PENDING_SAVES:
        logging.getLogger(__name__).warning("Agent flow backlog is full; dropping one flow")
        return
    future = _executor.submit(_save, record)
    _pending.add(future)
    future.add_done_callback(_pending.discard)


def wait_pending(timeout: float = 10.0) -> None:
    """Block until queued flows are saved (used at shutdown and in tests)."""
    concurrent.futures.wait(list(_pending), timeout=timeout)


def tool_args(args: dict) -> str:
    """Tool arguments for display, without the signed principal."""
    shown = {k: v for k, v in args.items() if k not in ("principal", "quote_id")}
    return _short(shown) if shown else "(for the signed-in member)"
