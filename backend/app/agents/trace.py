"""Readable per-question trace for demos: question -> supervisor -> agent -> MCP tool -> answer.

Prints one line per step to the backend console. Set TRACE_LOG=0 to turn it off.
Member identity is shown as a username only; principals, tokens and secrets are never logged.
"""
import contextvars
import itertools
import logging
import os
import sys
import time

_log = logging.getLogger("plan.trace")
_log.setLevel(logging.INFO)
_log.propagate = False  # keep these lines out of uvicorn's own log format
if not _log.handlers:
    _handler = logging.StreamHandler(sys.stdout)
    _handler.setFormatter(logging.Formatter("%(message)s"))
    _log.addHandler(_handler)

_turn = contextvars.ContextVar("trace_turn", default="-")
_started = contextvars.ContextVar("trace_started", default=0.0)
_counter = itertools.count(1)
_MAX = 110


def enabled() -> bool:
    return os.getenv("TRACE_LOG", "1") != "0"


def _short(value: object) -> str:
    text = " ".join(str(value).split())
    text = text.encode("ascii", "replace").decode()  # Windows consoles (cp1252) cannot print every character
    return text if len(text) <= _MAX else text[: _MAX - 3] + "..."


def event(actor: str, message: str) -> None:
    """actor is a short column label such as SUPERVISOR, POLICY, ACCOUNT, PAYMENT, MCP."""
    if not enabled():
        return
    elapsed = time.perf_counter() - _started.get() if _started.get() else 0.0
    _log.info("%s  #%-3s %+6.2fs  %-10s %s", time.strftime("%H:%M:%S"), _turn.get(), elapsed, actor, message)


def start_turn(username: str | None, message: str) -> None:
    _turn.set(str(next(_counter)))
    _started.set(time.perf_counter())
    if enabled():
        _log.info("")
    event("USER", f'{username or "anonymous"} asked: "{_short(message)}"')


def end_turn(result: dict) -> None:
    sources = ", ".join(result.get("sources") or []) or "none"
    event("ANSWER", f'by {result.get("agent") or "supervisor"} | sources: {sources} | "{_short(result.get("reply", ""))}"')


def tool_args(args: dict) -> str:
    """Tool arguments for display, without the signed principal."""
    shown = {k: v for k, v in args.items() if k != "principal"}
    return _short(shown) if shown else "(for the signed-in member)"
