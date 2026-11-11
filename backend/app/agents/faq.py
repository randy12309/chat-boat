"""FAQ lookup backed by Valkey distributed cache with in-memory hot cache layer.

Lookup order for each chat message:
  1. Valkey cache - distributed cache for FAQ answers (populated from Excel import)
  2. in-memory hot cache - holds recently accessed questions
  3. miss -> the LLM agent.

FAQ entries are loaded from Excel into Valkey cache via import script.
Only public (non-personal) answers are ever cached or returned.
"""
import asyncio
import logging
import os
import threading
import time

from mcp_server.vectordb import normalize
from mcp_server import cache as valkey_cache
from mcp_server import embeddings

logger = logging.getLogger(__name__)

MAX_QUERY_CHARS = 300
MAX_LEARNED_ENTRIES = 500
SEMANTIC_MIN_SIMILARITY = float(os.getenv("FAQ_SEMANTIC_MIN_SIMILARITY", "0.75"))


def enabled() -> bool:
    return os.getenv("FAQ_ENABLED", "1") != "0"


class FaqService:
    def __init__(
        self,
        *,
        cache_ttl: float = 3600,
        hot_size: int = 100,
    ):
        self.cache_ttl = cache_ttl
        self.hot_size = hot_size
        # key -> (answer entry, expiry); hot entries never expire
        self._cache: dict[str, tuple[dict, float | None]] = {}
        self._lock = threading.Lock()

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

    def warm_up(self) -> None:
        """Load the FAQ spreadsheet into Valkey (embedding each question) if it is not there yet."""
        try:
            from mcp_server import faq_excel

            faq_excel.load()
        except Exception:
            logger.exception("FAQ warm-up failed")

    def _valkey_lookup(self, key: str) -> dict | None:
        """Check Valkey cache for FAQ answer."""
        valkey_hit = valkey_cache.get_faq_answer(key)
        if valkey_hit:
            logger.debug("Valkey FAQ cache hit for %s", key)
            return {**valkey_hit, "layer": "valkey"}
        return None

    async def lookup(self, message: str) -> dict | None:
        """Return {id, answer, source, layer} from Valkey cache or in-memory cache, else None."""
        message = message.strip()
        key = normalize(message)
        if not key or len(message) > MAX_QUERY_CHARS:
            return None

        # Layer 1: Valkey distributed cache
        valkey_hit = self._valkey_lookup(key)
        if valkey_hit:
            hit = valkey_hit
        else:
            # Layer 2: in-memory hot cache
            cached = self._cache_get(key)
            if cached:
                hit = {**cached, "layer": "cache"}
            else:
                hit = await self._semantic_lookup(key)
                if hit is None:
                    return None

        # Store in in-memory cache for faster subsequent access
        self._cache_put(key, {k: hit[k] for k in ("id", "answer", "source")})
        return hit

    async def _semantic_lookup(self, key: str) -> dict | None:
        """Embed the question and look for a similar cached question in Valkey."""
        try:
            embedding = (await asyncio.to_thread(embeddings.embed, [key]))[0]
            found = await asyncio.to_thread(valkey_cache.find_faq_semantic, embedding, SEMANTIC_MIN_SIMILARITY)
        except Exception:
            logger.warning("Semantic FAQ lookup failed", exc_info=True)
            return None
        return {**found, "layer": "valkey"} if found else None

    async def remember(self, message: str, answer: str, source: str) -> None:
        """Store a public answer in Valkey under the question's embedding (and its exact key) for later hits."""
        key = normalize(message.strip())
        if not key or len(message.strip()) > MAX_QUERY_CHARS:
            return
        entry = {"id": source, "answer": answer, "source": source}
        try:
            embedding = (await asyncio.to_thread(embeddings.embed, [key]))[0]
            await asyncio.to_thread(valkey_cache.put_faq_semantic, key, embedding, entry)
            await asyncio.to_thread(valkey_cache.set_faq_answer, key, entry)
        except Exception:
            logger.warning("Could not cache the question embedding", exc_info=True)


_service: FaqService | None = None


def get_service() -> FaqService:
    global _service
    if _service is None:
        _service = FaqService(
            cache_ttl=float(os.getenv("FAQ_CACHE_TTL_SECONDS", "3600")),
            hot_size=int(os.getenv("FAQ_HOT_CACHE_SIZE", "100")),
        )
    return _service


def set_service(service: FaqService | None) -> None:
    global _service
    _service = service


async def lookup(message: str) -> dict | None:
    return await get_service().lookup(message)