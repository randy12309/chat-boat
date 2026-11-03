"""Question cache backed by PostgreSQL + pgvector.

Lookup order for each chat message:
  1. in-memory cache - holds the most frequently asked public questions (by hit count in the database), plus
     phrasings that were asked often enough to be promoted, plus recently learned phrasings with a TTL;
  2. exact question match in the database;
  3. semantic search over the embedded FAQ (pgvector, cosine distance);
  4. miss -> the LLM agent.

Only public (non-personal) answers are ever cached or returned. FAQ entries with no answer are member-specific
questions: they are indexed too, so a message whose closest match is one of them goes to the agent (live data)
instead of a similar public answer. Every answered question is counted, so the hot cache follows real usage.
"""
import asyncio
import logging
import os
import threading
import time

from mcp_server import pgvec
from mcp_server.pgvec import normalize

logger = logging.getLogger(__name__)

MAX_QUERY_CHARS = 300
MAX_LEARNED_ENTRIES = 500


def enabled() -> bool:
    return os.getenv("FAQ_ENABLED", "1") != "0"


class FaqService:
    def __init__(
        self,
        *,
        max_distance: float = 0.45,
        cache_ttl: float = 3600,
        hot_size: int = 100,
        promote_min_hits: int = 3,
        vector_enabled: bool = True,
    ):
        self.max_distance = max_distance
        self.cache_ttl = cache_ttl
        self.hot_size = hot_size
        self.promote_min_hits = promote_min_hits
        self.vector_enabled = vector_enabled
        # key -> (answer entry, expiry); hot entries never expire
        self._cache: dict[str, tuple[dict, float | None]] = {}
        self._settings: dict[str, str] = {}
        self._lock = threading.Lock()

    def _public(self, row: dict) -> dict:
        return {"id": row["id"], "answer": pgvec.render(row["answer"], self._settings), "source": row["source"]}

    def _cache_get(self, key: str) -> dict | None:
        item = self._cache.get(key)
        if item is None:
            return None
        value, expires = item
        if expires is not None and expires < time.time():
            self._cache.pop(key, None)
            return None
        return value

    def _cache_put(self, key: str, value: dict, *, hot: bool = False) -> None:
        with self._lock:
            if not hot:
                learned = [k for k, (_, exp) in self._cache.items() if exp is not None]
                if len(learned) >= MAX_LEARNED_ENTRIES:
                    self._cache.pop(learned[0], None)
            self._cache[key] = (value, None if hot else time.time() + self.cache_ttl)

    def refresh_hot(self) -> None:
        """Reload the most frequent public questions and promoted phrasings from the database into the cache."""
        self._settings = pgvec.get_settings()
        rows = pgvec.faq_top_public(self.hot_size)
        rows += pgvec.faq_frequent_phrasings(self.promote_min_hits, self.hot_size)
        for row in rows:
            self._cache_put(row["key"], self._public(row), hot=True)

    def warm_up(self) -> None:
        try:
            pgvec.ensure_ready()
            self.refresh_hot()
            if self.vector_enabled:
                from mcp_server import embeddings

                embeddings.embed(["warm up"])  # load the embedding model before the first chat
        except Exception:
            logger.exception("Vector DB warm-up failed")

    def _db_lookup(self, key: str, message: str) -> dict | None:
        """Exact match, then nearest embedded question. Returns None for a miss or a member-specific match."""
        if not pgvec.enabled():
            return None
        row = pgvec.faq_exact(key)
        layer = "db"
        if row is None:
            if not self.vector_enabled:
                return None
            row = pgvec.faq_nearest(message)
            layer = "vector"
            # A closest match that is a member-specific question means live data is needed.
            if row is None or row["distance"] > self.max_distance:
                return None
        if not row["answer"]:
            return None
        if not self._settings:
            self._settings = pgvec.get_settings()
        return {**self._public(row), "layer": layer}

    def _record(self, hit: dict, key: str, message: str) -> None:
        try:
            count = pgvec.record_faq_hit(hit["id"], key, message)
        except Exception:
            logger.exception("Could not record FAQ hit")
            return
        if count is not None and count >= self.promote_min_hits:
            self._cache_put(key, {k: hit[k] for k in ("id", "answer", "source")}, hot=True)

    async def lookup(self, message: str) -> dict | None:
        """Return {id, answer, source, layer} from the cache or the vector DB, else None."""
        message = message.strip()
        key = normalize(message)
        if not key or len(message) > MAX_QUERY_CHARS:
            return None
        cached = self._cache_get(key)
        if cached:
            hit = {**cached, "layer": "cache"}
        else:
            try:
                hit = await asyncio.to_thread(self._db_lookup, key, message)
            except Exception:
                logger.exception("Vector DB lookup failed; falling through to the agent")
                return None
            if hit is None:
                return None
            self._cache_put(key, {k: hit[k] for k in ("id", "answer", "source")})
        await asyncio.to_thread(self._record, hit, key, message)
        return hit


_service: FaqService | None = None


def get_service() -> FaqService:
    global _service
    if _service is None:
        _service = FaqService(
            max_distance=float(os.getenv("VECTOR_MAX_DISTANCE", "0.45")),
            cache_ttl=float(os.getenv("FAQ_CACHE_TTL_SECONDS", "3600")),
            hot_size=int(os.getenv("FAQ_HOT_CACHE_SIZE", "100")),
            promote_min_hits=int(os.getenv("FAQ_PROMOTE_MIN_HITS", "3")),
            vector_enabled=os.getenv("VECTOR_DB_ENABLED", "1") != "0",
        )
    return _service


def set_service(service: FaqService | None) -> None:
    global _service
    _service = service


async def lookup(message: str) -> dict | None:
    return await get_service().lookup(message)
