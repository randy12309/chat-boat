"""PostgreSQL + pgvector access: plan settings, embedded policy topics, and the embedded FAQ with hit counts.

Connection (first match wins): PG_DSN; PG_HOST/PG_PORT/PG_USER/PG_PASSWORD/PG_DATABASE; or PG_EMBEDDED=1, which runs
a local PostgreSQL (with pgvector) from the `pgserver` package in PG_DATA_DIR - no install needed for development.
"""
import hashlib
import json
import logging
import os
import re
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from mcp_server import embeddings

logger = logging.getLogger(__name__)

SCHEMA_FILE = Path(__file__).parent / "pg_schema.sql"
DATA_DIR = Path(__file__).resolve().parents[1] / "app" / "data"
DEFAULT_DB = "plan_assistant"

_dsn_cache: str | None = None
_dsn_lock = threading.Lock()


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", text.lower().replace("'", ""))).strip()


def enabled() -> bool:
    if os.getenv("PG_ENABLED", "1") == "0":
        return False
    return bool(os.getenv("PG_DSN") or os.getenv("PG_HOST") or os.getenv("PG_EMBEDDED") == "1")


def _database() -> str:
    name = os.getenv("PG_DATABASE", DEFAULT_DB)
    if not re.fullmatch(r"[A-Za-z0-9_]{1,63}", name):
        raise ValueError("PG_DATABASE must be a simple identifier")
    return name


def _embedded_server():
    import pgserver

    data_dir = os.getenv("PG_DATA_DIR") or str(DATA_DIR / "pgdata")
    mode = "stop" if os.getenv("PG_EMBEDDED_STOP") == "1" else None  # default: keep running between processes
    return pgserver.get_server(data_dir, cleanup_mode=mode)


def _create_database_if_missing(admin_dsn: str, name: str) -> None:
    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        if conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone() is None:
            conn.execute(f'CREATE DATABASE "{name}"')


def dsn() -> str:
    global _dsn_cache
    with _dsn_lock:
        if _dsn_cache is not None:
            return _dsn_cache
        explicit = os.getenv("PG_DSN")
        if explicit:
            _dsn_cache = explicit
        elif os.getenv("PG_HOST"):
            _dsn_cache = (
                f"host={os.environ['PG_HOST']} port={os.getenv('PG_PORT', '5432')} "
                f"user={os.getenv('PG_USER', 'postgres')} password={os.getenv('PG_PASSWORD', '')} dbname={_database()}"
            )
        else:
            server = _embedded_server()
            name = _database()
            _create_database_if_missing(server.get_uri(), name)
            _dsn_cache = server.get_uri(name)
        return _dsn_cache


def reset_connection_cache() -> None:
    global _dsn_cache
    _dsn_cache = None


@contextmanager
def connection():
    conn = psycopg.connect(dsn(), row_factory=dict_row)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def create_schema() -> None:
    # The extension and tables are created from a database where the vector type does not exist yet, so run each statement.
    with connection() as conn:
        for statement in SCHEMA_FILE.read_text(encoding="utf-8").split(";"):
            if statement.strip():
                conn.execute(statement)


def drop_schema() -> None:
    with connection() as conn:
        for table in ("faq_learned", "faq_entries", "policy_topics", "plan_settings"):
            conn.execute(f"DROP TABLE IF EXISTS {table} CASCADE")


def _hash(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()[:16]


def seed(*, overwrite: bool = False) -> dict:
    """Load plan_settings.json, policies.json and faq.json into the database.

    Settings that already exist keep their database value unless overwrite=True. Policy and FAQ rows are upserted by
    id/topic and only re-embedded when their text changed; hit counts are preserved.
    """
    settings = json.loads((DATA_DIR / "plan_settings.json").read_text(encoding="utf-8"))
    policies = json.loads((DATA_DIR / "policies.json").read_text(encoding="utf-8"))
    faqs = json.loads((DATA_DIR / "faq.json").read_text(encoding="utf-8"))
    counts = {"settings": 0, "policies": 0, "faq": 0}
    with connection() as conn:
        for key, item in settings.items():
            conflict = (
                "DO UPDATE SET value = EXCLUDED.value, description = EXCLUDED.description, updated_at = now()"
                if overwrite
                else "DO NOTHING"
            )
            cur = conn.execute(
                f"INSERT INTO plan_settings (key, value, description) VALUES (%s, %s, %s) ON CONFLICT (key) {conflict}",
                (key, item["value"], item.get("description", "")),
            )
            counts["settings"] += cur.rowcount

        existing = {r["topic"]: r["content_hash"] for r in conn.execute("SELECT topic, content_hash FROM policy_topics")}
        changed = [p for p in policies if existing.get(p["topic"]) != _hash(p["topic"], p["content"])]
        if changed:
            vectors = embeddings.embed([f"{p['topic']}: {p['content']}" for p in changed])
            for p, vec in zip(changed, vectors):
                conn.execute(
                    "INSERT INTO policy_topics (topic, content, content_hash, embedding) VALUES (%s, %s, %s, %s::vector) "
                    "ON CONFLICT (topic) DO UPDATE SET content = EXCLUDED.content, content_hash = EXCLUDED.content_hash, "
                    "embedding = EXCLUDED.embedding, updated_at = now()",
                    (p["topic"], p["content"], _hash(p["topic"], p["content"]), embeddings.to_literal(vec)),
                )
            counts["policies"] = len(changed)

        existing = {r["id"]: r["content_hash"] for r in conn.execute("SELECT id, content_hash FROM faq_entries")}

        def faq_hash(e: dict) -> str:
            return _hash(e["question"], e.get("answer") or "", e.get("source", "faq"))

        changed = [e for e in faqs if existing.get(e["id"]) != faq_hash(e)]
        if changed:
            vectors = embeddings.embed([e["question"] for e in changed])
            for e, vec in zip(changed, vectors):
                conn.execute(
                    "INSERT INTO faq_entries (id, question, question_key, answer, source, content_hash, embedding) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s::vector) ON CONFLICT (id) DO UPDATE SET "
                    "question = EXCLUDED.question, question_key = EXCLUDED.question_key, answer = EXCLUDED.answer, "
                    "source = EXCLUDED.source, content_hash = EXCLUDED.content_hash, embedding = EXCLUDED.embedding",
                    (
                        e["id"], e["question"], normalize(e["question"]), e.get("answer"), e.get("source", "faq"),
                        faq_hash(e), embeddings.to_literal(vec),
                    ),
                )
            counts["faq"] = len(changed)
    return counts


def ensure_ready() -> None:
    """Create the schema and load the seed data; cheap when everything is already in place."""
    create_schema()
    seed()


# --- settings ---------------------------------------------------------------------------------------------------

def get_settings() -> dict[str, str]:
    with connection() as conn:
        return {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM plan_settings")}


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

_topics_cache: tuple[float, list[str]] = (0.0, [])
TOPICS_TTL_SECONDS = 300


def policy_topic_names() -> list[str]:
    global _topics_cache
    if not enabled():
        return []
    stamp, names = _topics_cache
    if names and time.time() - stamp < TOPICS_TTL_SECONDS:
        return names
    try:
        with connection() as conn:
            names = [r["topic"] for r in conn.execute("SELECT topic FROM policy_topics ORDER BY topic")]
    except Exception:
        logger.exception("Could not load policy topics")
        return names
    _topics_cache = (time.time(), names)
    return names


def clear_topics_cache() -> None:
    global _topics_cache
    _topics_cache = (0.0, [])


def find_policy(topic: str, max_distance: float) -> dict | None:
    """Policy by topic name (exact or contained), else the nearest embedded topic within max_distance."""
    key = topic.strip().lower()[:100]
    if not key:
        return None
    with connection() as conn:
        row = conn.execute(
            "SELECT topic, content FROM policy_topics WHERE lower(topic) = %s OR position(lower(topic) in %s) > 0 "
            "OR position(%s in lower(topic)) > 0 ORDER BY length(topic) DESC LIMIT 1",
            (key, key, key),
        ).fetchone()
        if row is None:
            literal = embeddings.to_literal(embeddings.embed([key])[0])
            row = conn.execute(
                "SELECT topic, content, embedding <=> %s::vector AS distance FROM policy_topics "
                "ORDER BY embedding <=> %s::vector LIMIT 1",
                (literal, literal),
            ).fetchone()
            if row is None or row["distance"] > max_distance:
                return None
        settings = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM plan_settings")}
    return {"topic": row["topic"], "text": render(row["content"], settings)}


# --- FAQ --------------------------------------------------------------------------------------------------------

def faq_exact(key: str) -> dict | None:
    with connection() as conn:
        return conn.execute(
            "SELECT id, answer, source FROM faq_entries WHERE question_key = %s LIMIT 1", (key,)
        ).fetchone()


def faq_nearest(message: str) -> dict | None:
    """Closest FAQ entry (including member-specific ones, whose answer is NULL) with its cosine distance."""
    literal = embeddings.to_literal(embeddings.embed([message])[0])
    with connection() as conn:
        return conn.execute(
            "SELECT id, answer, source, embedding <=> %s::vector AS distance FROM faq_entries "
            "ORDER BY embedding <=> %s::vector LIMIT 1",
            (literal, literal),
        ).fetchone()


def faq_top_public(limit: int) -> list[dict]:
    """Most frequently asked public questions (by hit count) for the hot cache."""
    with connection() as conn:
        return conn.execute(
            "SELECT question_key AS key, id, answer, source FROM faq_entries WHERE answer IS NOT NULL "
            "ORDER BY hit_count DESC, id LIMIT %s",
            (limit,),
        ).fetchall()


def faq_frequent_phrasings(min_hits: int, limit: int) -> list[dict]:
    with connection() as conn:
        return conn.execute(
            "SELECT l.question_key AS key, e.id, e.answer, e.source FROM faq_learned l "
            "JOIN faq_entries e ON e.id = l.entry_id WHERE e.answer IS NOT NULL AND l.hit_count >= %s "
            "ORDER BY l.hit_count DESC LIMIT %s",
            (min_hits, limit),
        ).fetchall()


def record_faq_hit(entry_id: str, key: str, question: str) -> int | None:
    """Count one answered question. Returns the phrasing's hit count when it is not the entry's own wording."""
    with connection() as conn:
        row = conn.execute(
            "UPDATE faq_entries SET hit_count = hit_count + 1, last_hit_at = now() WHERE id = %s "
            "RETURNING question_key",
            (entry_id,),
        ).fetchone()
        if row is None or row["question_key"] == key:
            return None
        learned = conn.execute(
            "INSERT INTO faq_learned (question_key, question, entry_id) VALUES (%s, %s, %s) "
            "ON CONFLICT (question_key) DO UPDATE SET hit_count = faq_learned.hit_count + 1, last_hit_at = now() "
            "RETURNING hit_count",
            (key, question[:300], entry_id),
        ).fetchone()
        return learned["hit_count"]
