import os
import tempfile

os.environ["LLM_ENABLED"] = "0"  # keep tests offline and deterministic
os.environ["FAQ_ENABLED"] = "0"  # FAQ cache/vector tests enable it explicitly
os.environ["MCP_SERVER_URL"] = ""  # use the in-process MCP server, not a running service
os.environ["DB_ENABLED"] = "0"  # tests use the JSON dummy data, not MySQL
os.environ["SESSION_DB"] = os.path.join(tempfile.mkdtemp(), "sessions.db")
os.environ["REGISTERED_USERS_FILE"] = os.path.join(tempfile.mkdtemp(), "registered_users.json")
# Tests run against a throwaway local PostgreSQL + pgvector (stopped at exit) with a deterministic embedder.
os.environ.pop("PG_DSN", None)
os.environ.pop("PG_HOST", None)
os.environ["PG_ENABLED"] = "1"
os.environ["PG_EMBEDDED"] = "1"
os.environ["PG_EMBEDDED_STOP"] = "1"
os.environ["PG_DATA_DIR"] = os.path.join(tempfile.mkdtemp(), "pgdata")
os.environ["PG_DATABASE"] = "plan_assistant_test"

import re  # noqa: E402
import zlib  # noqa: E402

from mcp_server import embeddings, pgvec  # noqa: E402

STOPWORDS = {
    "what", "is", "the", "my", "i", "do", "how", "a", "an", "to", "for", "if", "me", "in", "of", "and", "can",
    "does", "are", "am", "or", "with", "your", "you", "on", "it", "be",
}


def fake_embed(texts: list[str]) -> list[list[float]]:
    """Hashed bag-of-words so semantic tests run offline: texts sharing words are close in cosine distance."""
    out = []
    for text in texts:
        vec = [0.0] * embeddings.DIMENSIONS
        for word in set(pgvec.normalize(text).split()) - STOPWORDS:
            vec[zlib.crc32(word.encode()) % embeddings.DIMENSIONS] = 1.0
        out.append(vec if any(vec) else [0.0] * (embeddings.DIMENSIONS - 1) + [0.001])
    return out


embeddings.set_embedder(fake_embed)
pgvec.ensure_ready()

