import os
import tempfile

os.environ["LLM_ENABLED"] = "0"  # keep tests offline and deterministic
os.environ["FAQ_ENABLED"] = "0"  # FAQ cache/vector tests enable it explicitly
os.environ["MCP_SERVER_URL"] = ""  # use the in-process MCP server, not a running service
os.environ["VALKEY_URL"] = ""  # never use a developer's real Valkey cache in tests
os.environ["DB_ENABLED"] = "0"  # tests use the JSON dummy data, not MySQL
os.environ["SESSION_DB"] = os.path.join(tempfile.mkdtemp(), "sessions.db")
os.environ["REGISTERED_USERS_FILE"] = os.path.join(tempfile.mkdtemp(), "registered_users.json")
# Tests run against Qdrant's in-memory embedded mode with a deterministic embedder.
os.environ.pop("QDRANT_URL", None)
os.environ["QDRANT_ENABLED"] = "1"
os.environ["QDRANT_PATH"] = ":memory:"
os.environ.pop("KNOWLEDGE_PDF", None)

import re  # noqa: E402
import zlib  # noqa: E402

from mcp_server import embeddings, vectordb  # noqa: E402

STOPWORDS = {
    "what", "is", "the", "my", "i", "do", "how", "a", "an", "to", "for", "if", "me", "in", "of", "and", "can",
    "does", "are", "am", "or", "with", "your", "you", "on", "it", "be",
}


def fake_embed(texts: list[str]) -> list[list[float]]:
    """Hashed bag-of-words so semantic tests run offline: texts sharing words are close in cosine distance."""
    out = []
    for text in texts:
        vec = [0.0] * embeddings.DIMENSIONS
        for word in set(vectordb.normalize(text).split()) - STOPWORDS:
            vec[zlib.crc32(word.encode()) % embeddings.DIMENSIONS] = 1.0
        out.append(vec if any(vec) else [0.0] * (embeddings.DIMENSIONS - 1) + [0.001])
    return out


embeddings.set_embedder(fake_embed)
vectordb.ensure_ready()

