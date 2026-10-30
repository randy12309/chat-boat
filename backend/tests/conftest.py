import os
import tempfile

os.environ["LLM_ENABLED"] = "0"  # keep tests offline and deterministic
os.environ["MCP_SERVER_URL"] = ""  # use the in-process MCP server, not a running service
os.environ["SESSION_DB"] = os.path.join(tempfile.mkdtemp(), "sessions.db")
