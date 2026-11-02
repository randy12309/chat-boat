"""Question cache with a vector-database fallback.

Lookup order for each chat message: (1) exact match in the in-memory cache, seeded with the
public FAQ answers; (2) semantic search in a local Chroma collection; (3) miss -> the LLM agent.

Only public (non-personal) answers are ever cached or returned from the vector store. FAQ entries
with no answer are the member-specific questions: they are indexed too, so that a message whose
closest match is one of them is sent on to the agent (live data) instead of a similar public answer.
"""
import asyncio
import hashlib
import json
import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

FAQ_FILE = Path(__file__).parent.parent / "data" / "faq.json"
MAX_QUERY_CHARS = 300
MAX_LEARNED_ENTRIES = 500


def enabled() -> bool:
    return os.getenv("FAQ_ENABLED", "1") != "0"


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", text.lower().replace("'", ""))).strip()


def load_entries(path: Path = FAQ_FILE) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


class FaqService:
    def __init__(
        self,
        entries: list[dict],
        *,
        vector_path: str | None = None,
        embedding_function: Any = None,
        max_distance: float = 0.45,
        cache_ttl: float = 3600,
        vector_enabled: bool = True,
    ):
        self.entries = entries
        self.vector_path = vector_path
        self.embedding_function = embedding_function
        self.max_distance = max_distance
        self.cache_ttl = cache_ttl
        self.vector_enabled = vector_enabled
        # key -> (answer entry, expiry); seeded entries never expire
        self._cache: dict[str, tuple[dict, float | None]] = {
            normalize(e["question"]): (self._public(e), None) for e in entries if e.get("answer")
        }
        self._collection = None
        self._lock = threading.Lock()

    @staticmethod
    def _public(entry: dict) -> dict:
        return {"id": entry["id"], "answer": entry["answer"], "source": entry.get("source", "faq")}

    def _cache_get(self, key: str) -> dict | None:
        item = self._cache.get(key)
        if item is None:
            return None
        value, expires = item
        if expires is not None and expires < time.time():
            del self._cache[key]
            return None
        return value

    def _cache_put(self, key: str, value: dict) -> None:
        learned = [k for k, (_, exp) in self._cache.items() if exp is not None]
        if len(learned) >= MAX_LEARNED_ENTRIES:
            del self._cache[learned[0]]
        self._cache[key] = (value, time.time() + self.cache_ttl)

    def _kb_hash(self) -> str:
        return hashlib.sha256(json.dumps(self.entries, sort_keys=True).encode()).hexdigest()[:12]

    def _get_collection(self):
        with self._lock:
            if self._collection is not None:
                return self._collection
            import chromadb
            from chromadb.config import Settings

            settings = Settings(anonymized_telemetry=False)
            if self.vector_path:
                client = chromadb.PersistentClient(path=self.vector_path, settings=settings)
            else:
                client = chromadb.EphemeralClient(settings=settings)
            # The collection name carries a hash of the FAQ content, so edits re-index automatically.
            name = f"faq_{self._kb_hash()}"
            for stale in client.list_collections():
                stale_name = getattr(stale, "name", stale)
                if stale_name.startswith("faq_") and stale_name != name:
                    client.delete_collection(stale_name)
            kwargs: dict = {"configuration": {"hnsw": {"space": "cosine"}}}
            if self.embedding_function is not None:
                kwargs["embedding_function"] = self.embedding_function
            collection = client.get_or_create_collection(name, **kwargs)
            if collection.count() == 0:
                collection.add(
                    ids=[e["id"] for e in self.entries],
                    documents=[e["question"] for e in self.entries],
                    metadatas=[
                        {"answer": e.get("answer") or "", "source": e.get("source", "faq")} for e in self.entries
                    ],
                )
            self._collection = collection
            return collection

    def _vector_search(self, message: str) -> dict | None:
        res = self._get_collection().query(
            query_texts=[message], n_results=3, include=["metadatas", "distances"]
        )
        if not res["ids"][0]:
            return None
        distance, meta, entry_id = res["distances"][0][0], res["metadatas"][0][0], res["ids"][0][0]
        # A closest match that is a member-specific question means live data is needed.
        if distance > self.max_distance or not meta["answer"]:
            return None
        return {"id": entry_id, "answer": meta["answer"], "source": meta["source"]}

    def warm_up(self) -> None:
        try:
            self._get_collection()
        except Exception:
            logger.exception("Vector DB warm-up failed")

    async def lookup(self, message: str) -> dict | None:
        """Return {id, answer, source, layer} from the cache or the vector DB, else None."""
        message = message.strip()
        key = normalize(message)
        if not key or len(message) > MAX_QUERY_CHARS:
            return None
        hit = self._cache_get(key)
        if hit:
            return {**hit, "layer": "cache"}
        if not self.vector_enabled:
            return None
        try:
            hit = await asyncio.to_thread(self._vector_search, message)
        except Exception:
            logger.exception("Vector DB lookup failed; falling through to the agent")
            return None
        if hit is None:
            return None
        self._cache_put(key, hit)
        return {**hit, "layer": "vector"}


_service: FaqService | None = None


def get_service() -> FaqService:
    global _service
    if _service is None:
        _service = FaqService(
            load_entries(),
            vector_path=os.getenv("VECTOR_DB_PATH", str(Path(__file__).parent.parent / "data" / "chroma")),
            max_distance=float(os.getenv("VECTOR_MAX_DISTANCE", "0.45")),
            cache_ttl=float(os.getenv("FAQ_CACHE_TTL_SECONDS", "3600")),
            vector_enabled=os.getenv("VECTOR_DB_ENABLED", "1") != "0",
        )
    return _service


def set_service(service: FaqService | None) -> None:
    global _service
    _service = service


async def lookup(message: str) -> dict | None:
    return await get_service().lookup(message)
