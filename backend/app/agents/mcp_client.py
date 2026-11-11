import asyncio
import os
import time
from typing import Any

from fastmcp import Client

from app.agents import trace

CALL_TIMEOUT_SECONDS = float(os.getenv("MCP_TIMEOUT_SECONDS", "10"))

# Only these tools may be invoked from the graph.
# The last four read the BMOWNER tables directly: USER_ACCOUNT, EPAYMENT_METHOD and EPAYMENT.
ALLOWED_TOOLS = frozenset(
    {
        "get_balance", "get_policy_info", "explain_premium", "get_eligibility",
        "get_due_payments", "quote_payment", "execute_payment",
        "get_user_account", "list_payment_methods", "list_epayments",
    }
)


def _target():
    url = os.getenv("MCP_SERVER_URL")
    if url:
        return url
    from mcp_server.server import mcp  # in-process default for local dev and tests

    return mcp


def _error(error_type: str, message: str) -> dict:
    return {"status": "error", "error_type": error_type, "message": message}


def _valid(result: Any) -> bool:
    if not isinstance(result, dict):
        return False
    if result.get("status") == "ok":
        return isinstance(result.get("data"), dict) and isinstance(result.get("source"), str)
    return result.get("status") == "error" and isinstance(result.get("error_type"), str)


def _storage_backend(source: str) -> str:
    """Classify a tool result's storage source for audit-flow display."""
    source = source.lower()
    if "cache" in source:
        return "valkey"
    if source.startswith(("policy:", "sag-aftra-reference:")):
        return "qdrant"
    if source.startswith(("epayment", "eligibility", "user_account")):
        return "mysql"
    return "none"


async def call_tool(name: str, args: dict[str, Any]) -> dict:
    """Call an allowlisted MCP tool; always returns a structured ok/error dict."""
    agent = trace.current_agent()
    trace.event("MCP", f"call  {name} {trace.tool_args(args)}", kind="tool_call", src=agent, to="mcp_server",
                tool=name, status="called")
    started = time.perf_counter()
    result = await _call_tool(name, args)
    took = int((time.perf_counter() - started) * 1000)
    if result.get("status") == "ok":
        trace.event("MCP", f"ok    {name} -> source={result['source']} ({took} ms)", kind="tool_result",
                    src="mcp_server", to=agent, tool=name, status="ok", ms=took,
                    storage=_storage_backend(result["source"]))
    else:
        trace.event("MCP", f"error {name} -> {result.get('error_type')} ({took} ms)", kind="tool_result",
                    src="mcp_server", to=agent, tool=name, status=str(result.get("error_type"))[:30], ms=took)
    return result


async def _call_tool(name: str, args: dict[str, Any]) -> dict:
    if name not in ALLOWED_TOOLS:
        return _error("tool_not_allowed", f"Tool {name} is not allowed.")
    try:
        async with asyncio.timeout(CALL_TIMEOUT_SECONDS):
            async with Client(_target()) as client:
                result = await client.call_tool(name, args, raise_on_error=False)
    except TimeoutError:
        return _error("timeout", "Tool call timed out.")
    except Exception:
        return _error("unavailable", "Tool service is unavailable.")
    if getattr(result, "is_error", False):
        return _error("tool_error", "Tool call failed.")
    data = result.structured_content
    if not _valid(data):
        return _error("malformed_output", "Tool returned an unexpected response.")
    return data
