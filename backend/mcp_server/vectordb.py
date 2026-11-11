"""Qdrant access: plan settings, embedded policy topics, and the SAG-AFTRA PDF knowledge base.

Connection (first match wins): QDRANT_URL (+ QDRANT_API_KEY) for the `qdrant/qdrant` Docker server; or QDRANT_PATH for
Qdrant's embedded local mode (a directory, or ":memory:"), which needs no server but allows only one process at a time.
"""
import hashlib
import json
import logging
import os
import re
import threading
import time
import uuid
from pathlib import Path

from qdrant_client import QdrantClient, models

from mcp_server import embeddings, knowledge_pdf

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parents[1] / "app" / "data"
DEFAULT_PDF = DATA_DIR / "SAG_AFTRA_RAG_Policy_Reference.pdf"

SETTINGS, KNOWLEDGE = "plan_settings", "sag_aftra_knowledge"
DOCUMENTS = "uploaded_documents"  # not rebuildable from seed files, so drop_schema leaves it alone
_REBUILDABLE = (KNOWLEDGE,)
_VECTOR_COLLECTIONS = _REBUILDABLE + (DOCUMENTS,)
_PAYLOAD_ONLY = (SETTINGS,)  # Qdrant needs a vector per point; these carry a 1-number placeholder
_NAMESPACE = uuid.UUID("6f1d5c1e-52a0-4c58-8a43-6d1f0d6c7a11")
_BATCH = 128
_PAYLOAD_INDEXES = {
    KNOWLEDGE: {"kind": "keyword", "record_id": "keyword", "mode": "keyword"},
    DOCUMENTS: {"doc_id": "keyword"},
}

_client: "QdrantClient | _LockedClient | None" = None
_client_lock = threading.Lock()
_hit_lock = threading.Lock()


class _LockedClient:
    """Embedded mode has no internal locking, so serialise every call."""

    def __init__(self, inner: QdrantClient):
        self._inner, self._lock = inner, threading.RLock()

    def __getattr__(self, name):
        attr = getattr(self._inner, name)
        if not callable(attr):
            return attr

        def call(*args, **kwargs):
            with self._lock:
                return attr(*args, **kwargs)

        return call


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", text.lower().replace("'", ""))).strip()


def enabled() -> bool:
    if os.getenv("QDRANT_ENABLED", "1") == "0":
        return False
    return bool(os.getenv("QDRANT_URL") or os.getenv("QDRANT_PATH"))


def _is_local() -> bool:
    return not os.getenv("QDRANT_URL")


def client():
    global _client
    with _client_lock:
        if _client is None:
            url = os.getenv("QDRANT_URL")
            if url:
                _client = QdrantClient(
                    url=url, api_key=os.getenv("QDRANT_API_KEY") or None,
                    timeout=int(os.getenv("QDRANT_TIMEOUT_SECONDS", "10")),
                    prefer_grpc=os.getenv("QDRANT_PREFER_GRPC") == "1",
                )
            else:
                local = os.environ["QDRANT_PATH"]
                _client = _LockedClient(QdrantClient(location=local) if local == ":memory:" else QdrantClient(path=local))
        return _client


def reset_connection_cache() -> None:
    global _client
    with _client_lock:
        if _client is not None:
            _client.close()
        _client = None


def _id(*parts: str) -> str:
    return str(uuid.uuid5(_NAMESPACE, "\x1f".join(parts)))


def _hash(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()[:16]


def _eq(key: str, value) -> models.FieldCondition:
    return models.FieldCondition(key=key, match=models.MatchValue(value=value))


def _scroll(collection: str, flt: models.Filter | None = None):
    offset = None
    while True:
        points, offset = client().scroll(collection, scroll_filter=flt, limit=256, offset=offset, with_payload=True)
        yield from (p.payload for p in points)
        if offset is None:
            return


def _upsert(collection: str, points: list[models.PointStruct]) -> None:
    for i in range(0, len(points), _BATCH):
        client().upsert(collection, points=points[i:i + _BATCH], wait=True)


def _nearest(collection: str, text: str, flt: models.Filter | None = None) -> dict | None:
    """Closest point as its payload plus the cosine distance (1 - similarity)."""
    result = client().query_points(
        collection, query=embeddings.embed([text])[0], query_filter=flt, limit=1, with_payload=True
    )
    if not result.points:
        return None
    return {**result.points[0].payload, "distance": 1.0 - result.points[0].score}


# --- schema -----------------------------------------------------------------------------------------------------

def create_schema() -> None:
    c = client()
    for name in _VECTOR_COLLECTIONS + _PAYLOAD_ONLY:
        if c.collection_exists(name):
            continue
        size, distance = (embeddings.DIMENSIONS, models.Distance.COSINE) if name in _VECTOR_COLLECTIONS else (1, models.Distance.DOT)
        try:
            c.create_collection(name, vectors_config=models.VectorParams(size=size, distance=distance))
        except Exception:
            if not c.collection_exists(name):  # another worker may have created it first
                raise
    if not _is_local():  # embedded mode has no payload indexes
        for name, fields in _PAYLOAD_INDEXES.items():
            for field, kind in fields.items():
                c.create_payload_index(name, field, models.PayloadSchemaType(kind))


def drop_schema() -> None:
    for name in _REBUILDABLE + _PAYLOAD_ONLY:
        client().delete_collection(name)


# --- seed data --------------------------------------------------------------------------------------------------

def seed(*, overwrite: bool = False) -> dict:
    """Load plan_settings.json and the SAG-AFTRA PDF.

    Settings that already exist keep their stored value unless overwrite=True. PDF records are upserted by
    id and only re-embedded when their text changed.
    """
    settings = json.loads((DATA_DIR / "plan_settings.json").read_text(encoding="utf-8"))
    counts = {"settings": 0, "knowledge": 0}

    stored = {p["key"] for p in _scroll(SETTINGS)}
    points = [
        models.PointStruct(
            id=_id("setting", key), vector=[1.0],
            payload={"key": key, "value": item["value"], "description": item.get("description", "")},
        )
        for key, item in settings.items() if overwrite or key not in stored
    ]
    _upsert(SETTINGS, points)
    counts["settings"] = len(points)

    counts["knowledge"] = seed_knowledge()
    return counts


def knowledge_pdf_path() -> Path:
    return Path(os.getenv("KNOWLEDGE_PDF") or DEFAULT_PDF)


def seed_knowledge(path: Path | None = None) -> int:
    """Parse the policy reference PDF and embed its policy (Pxx) records; returns how many changed.

    Only policies go to Qdrant. Records no longer in the PDF (including FAQ records stored by older versions) are
    removed, so a new edition of the PDF refreshes the corpus.
    """
    path = path or knowledge_pdf_path()
    if not path.exists():
        logger.warning("Knowledge PDF not found at %s; skipping", path)
        return 0
    parsed = knowledge_pdf.parse(path)
    problems = knowledge_pdf.validate(parsed)
    if problems:
        raise ValueError("PDF did not parse cleanly: " + "; ".join(problems[:5]))
    urls = {sid: s["url"] for sid, s in parsed["sources"].items()}

    records = []  # (record id, text to embed, payload). Only the policies: the FAQ questions live in Valkey.
    for p in parsed["policies"]:
        payload = {
            "kind": "policy", "record_id": p["id"], "title": p["title"], "record_type": p["record_type"],
            "scope": p["scope"], "text": p["text"], "source_ids": p["source_ids"],
            "source_urls": [urls[s] for s in p["source_ids"]],
        }
        records.append((p["id"], f"{p['title']}: {p['text']}", payload))
    for _, _, payload in records:
        payload["content_hash"] = _hash(json.dumps(payload, sort_keys=True))

    existing = {p["record_id"]: p["content_hash"] for p in _scroll(KNOWLEDGE)}
    changed = [r for r in records if existing.get(r[0]) != r[2]["content_hash"]]
    if changed:
        vectors = embeddings.embed([text for _, text, _ in changed])
        _upsert(KNOWLEDGE, [
            models.PointStruct(id=_id("knowledge", rid), vector=vec, payload=payload)
            for (rid, _, payload), vec in zip(changed, vectors)
        ])
    stale = [_id("knowledge", rid) for rid in existing if rid not in {r[0] for r in records}]
    if stale:
        client().delete(KNOWLEDGE, points_selector=models.PointIdsList(points=stale))
    return len(changed) + len(stale)


def ensure_ready() -> None:
    """Create the collections and load the seed data; cheap when everything is already in place."""
    create_schema()
    seed()


# --- uploaded documents -----------------------------------------------------------------------------------------

EMBED_BATCH = 64


def ingest_document(doc_id: str, filename: str, chunks: list[str]) -> dict:
    """Embed a document's chunks and store them. Re-sending the same doc_id replaces its earlier chunks."""
    if not client().collection_exists(DOCUMENTS):
        create_schema()
    own = models.Filter(must=[_eq("doc_id", doc_id)])
    existing = client().count(DOCUMENTS, count_filter=own, exact=True).count
    if existing == len(chunks):
        return {"doc_id": doc_id, "chunks": existing, "status": "unchanged"}
    if existing:
        client().delete(DOCUMENTS, points_selector=models.FilterSelector(filter=own))
    now = time.time()
    for start in range(0, len(chunks), EMBED_BATCH):
        batch = chunks[start:start + EMBED_BATCH]
        _upsert(DOCUMENTS, [
            models.PointStruct(
                id=_id("document", doc_id, str(start + n)), vector=vec,
                payload={"doc_id": doc_id, "filename": filename, "chunk_index": start + n, "text": text, "uploaded_at": now},
            )
            for n, (text, vec) in enumerate(zip(batch, embeddings.embed(batch)))
        ])
    return {"doc_id": doc_id, "chunks": len(chunks), "status": "replaced" if existing else "inserted"}


# --- settings ---------------------------------------------------------------------------------------------------

def get_settings() -> dict[str, str]:
    return {p["key"]: p["value"] for p in _scroll(SETTINGS)}


def set_setting(key: str, value: str) -> None:
    client().set_payload(SETTINGS, {"value": value}, points=[_id("setting", key)])


class _Missing(dict):
    def __missing__(self, key):
        return "{" + key + "}"


def render(text: str, settings: dict[str, str]) -> str:
    """Fill {setting_key} placeholders; unknown placeholders are left as they are."""
    try:
        return text.format_map(_Missing(settings))
    except (ValueError, IndexError):
        return text


# --- policy -----------------------------------------------------------------------------------------------------


def find_policy(topic: str, max_distance: float) -> dict | None:
    """Policy by topic name (exact or contained) from KNOWLEDGE collection, else nearest embedded policy within max_distance."""
    key = topic.strip().lower()[:100]
    if not key:
        return None
    # Search for exact match by record_id in KNOWLEDGE (kind=policy)
    named = [
        p for p in _scroll(KNOWLEDGE, models.Filter(must=[_eq("kind", "policy")]))
        if p["record_id"].lower() == key or p["record_id"].lower() in key or key in p["record_id"].lower()
    ]
    row = max(named, key=lambda p: len(p["record_id"])) if named else _nearest(
        KNOWLEDGE, key, models.Filter(must=[_eq("kind", "policy")]))
    if row is None or row.get("distance", 0.0) > max_distance:
        return None
    return {"topic": row["record_id"], "text": render(row["text"], get_settings())}
