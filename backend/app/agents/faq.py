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
from app.agents import trace

logger = logging.getLogger(__name__)
_NO_REVISION_GUARD = object()

MAX_QUERY_CHARS = 300
MAX_LEARNED_ENTRIES = 500
SEMANTIC_MIN_SIMILARITY = float(os.getenv("FAQ_SEMANTIC_MIN_SIMILARITY", "0.9"))


def enabled() -> bool:
    return os.getenv("FAQ_ENABLED", "1") != "0"


def cache_ttl_seconds() -> float:
    """Convert the optional day-based setting; retain the seconds fallback."""
    days = os.getenv("FAQ_CACHE_TTL_DAYS")
    if days is not None and days.strip():
        return max(0.0, float(days) * 86400)
    return max(0.0, float(os.getenv("FAQ_CACHE_TTL_SECONDS", "3600")))


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
        self._revision: str | None = None

    def _cache_get(self, key: str) -> dict | None:
        with self._lock:
            item = self._cache.get(key)
            if item is None:
                return None
            value, expires = item
            if expires is not None and expires < time.time():
                self._cache.pop(key, None)
                return None
            return value

    def _cache_put(self, key: str, value: dict, *, hot: bool = False, revision=_NO_REVISION_GUARD) -> None:
        with self._lock:
            if revision is not _NO_REVISION_GUARD and revision != self._revision:
                return
            if not hot:
                learned = [k for k, (_, exp) in self._cache.items() if exp is not None]
                if key not in self._cache and len(learned) >= min(MAX_LEARNED_ENTRIES, max(1, self.hot_size)):
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
        if valkey_hit and not valkey_cache.is_retrieved_policy_context(valkey_hit):
            logger.debug("Valkey FAQ cache hit for %s", key)
            return {**valkey_hit, "layer": "valkey"}
        return None

    async def lookup(self, message: str) -> dict | None:
        """Return {id, answer, source, layer} from Valkey cache or in-memory cache, else None."""
        message = message.strip()
        key = normalize(message)
        if not key or len(message) > MAX_QUERY_CHARS:
            return None

        revision = await asyncio.to_thread(valkey_cache.faq_revision)
        with self._lock:
            if revision != self._revision:
                self._cache.clear()
                self._revision = revision

        # Layer 1: Valkey distributed cache
        # Valkey uses a synchronous client; keep network waits off the event loop.
        valkey_hit = await asyncio.to_thread(self._valkey_lookup, key)
        if valkey_hit:
            hit = valkey_hit
            if os.getenv("FAQ_LOG_EACH_SIMILARITY", "0") == "1":
                await self._semantic_lookup(key, diagnostic_only=True)
        else:
            # Layer 2: in-memory hot cache
            cached = self._cache_get(key)
            if cached and not valkey_cache.is_retrieved_policy_context(cached):
                hit = {**cached, "layer": "cache"}
                if os.getenv("FAQ_LOG_EACH_SIMILARITY", "0") == "1":
                    await self._semantic_lookup(key, diagnostic_only=True)
            else:
                hit = await self._semantic_lookup(key)
                if hit is None:
                    return None

        # Store in in-memory cache for faster subsequent access
        self._cache_put(key, {k: hit[k] for k in ("id", "answer", "source", "answer_type") if k in hit}, revision=revision)
        return hit

    async def _semantic_lookup(self, key: str, *, diagnostic_only: bool = False) -> dict | None:
        """Embed the question and look for a similar cached question in Valkey."""
        scores: list[float] = []
        verbose = os.getenv("FAQ_LOG_EACH_SIMILARITY", "0") == "1"
        threshold = SEMANTIC_MIN_SIMILARITY

        def record_score(entry: dict, score: float) -> None:
            scores.append(score)
            if verbose:
                label = str(entry.get("answer", {}).get("id") or entry.get("q") or "unknown")[:100]
                trace.event("POLICY", f"FAQ {label}: similarity={score:.6f} ({score * 100:.2f}%), threshold={threshold:.4f} ({threshold * 100:.2f}%)")

        try:
            embedding = (await asyncio.to_thread(embeddings.embed, [key]))[0]
            found = await asyncio.to_thread(valkey_cache.find_faq_semantic, embedding, threshold, record_score)
            if found and valkey_cache.is_retrieved_policy_context(found):
                found = None
        except Exception:
            logger.warning("Semantic FAQ lookup failed", exc_info=True)
            trace.event("POLICY", f"FAQ similarity unavailable; threshold={threshold:.4f}", kind="retrieval",
                        to="valkey_faq_cache", status="unavailable", storage="valkey", threshold=threshold)
            return None
        best = max(scores) if scores else None
        summary = f"best similarity={best:.6f} ({best * 100:.2f}%)" if best is not None else "similarity=not calculated (no compatible cached embeddings)"
        status = "compared" if diagnostic_only else ("hit" if found else "miss")
        trace.event("POLICY", f"FAQ comparison: {summary}; threshold={threshold:.4f} ({threshold * 100:.2f}%); compared={len(scores)}" + ("; exact cache entry already selected" if diagnostic_only else ""),
                    kind="retrieval", to="valkey_faq_cache", status=status, storage="valkey", similarity=best, threshold=threshold, match_type="semantic")
        return {**found, "layer": "valkey"} if found else None

_service: FaqService | None = None


def get_service() -> FaqService:
    global _service
    if _service is None:
        _service = FaqService(
            cache_ttl=cache_ttl_seconds(),
            hot_size=int(os.getenv("FAQ_HOT_CACHE_SIZE", "100")),
        )
    return _service


def set_service(service: FaqService | None) -> None:
    global _service
    _service = service


async def lookup(message: str) -> dict | None:
    return await get_service().lookup(message)
