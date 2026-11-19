"""Optional shared Valkey cache for short-lived, authenticated account reads and FAQ embeddings."""
import hashlib
import json
import logging
import os
import secrets
import time
from functools import lru_cache

import valkey

logger = logging.getLogger(__name__)
TTL = 120  # account cache TTL (seconds)
FAQ_TTL = 3600  # FAQ cache TTL (1 hour)
SEMANTIC_PREFIX = "plan:faq:sem:"
SEMANTIC_MAX_SCAN = 2000  # cap on stored question embeddings compared per lookup
_PUT_IF_CURRENT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('SET', KEYS[2], ARGV[2], 'EX', ARGV[3])
end
return nil
"""
_INVALIDATE = """
redis.call('SET', KEYS[1], ARGV[1], 'EX', ARGV[2])
for i = 2, #KEYS do redis.call('DEL', KEYS[i]) end
return 1
"""
# Per-user account reads that share one generation token and are all cleared by a payment.
KINDS = ("balance", "eligibility", "due", "account", "premium")


@lru_cache(maxsize=1)
def _client():
    url = os.getenv("VALKEY_URL", "")
    if not url:
        return None
    return valkey.Valkey.from_url(url, decode_responses=True, socket_connect_timeout=0.2, socket_timeout=0.2,
                                max_connections=50)


@lru_cache(maxsize=1)
def _faq_client():
    """Client for FAQ lookups: scanning the stored question embeddings is larger than an account read."""
    url = os.getenv("VALKEY_URL", "")
    if not url:
        return None
    return valkey.Valkey.from_url(url, decode_responses=True, socket_connect_timeout=0.5, socket_timeout=3.0,
                                max_connections=10)


def compact(vector: list[float]) -> list[float]:
    """Round an embedding to 5 decimals: about half the bytes, with no visible change to cosine similarity."""
    return [round(float(x), 5) for x in vector]


def _keys(username: str) -> tuple[str, ...]:
    user_hash = hashlib.sha256(username.encode("utf-8")).hexdigest()
    prefix = f"plan:account:{{{user_hash}}}"
    return (f"{prefix}:generation", *(f"{prefix}:{kind}" for kind in KINDS))


def _faq_key(normalized_question: str) -> str:
    """Valkey key for a cached FAQ answer (by normalized question)."""
    q_hash = hashlib.sha256(normalized_question.encode("utf-8")).hexdigest()[:16]
    return f"plan:faq:{q_hash}"


def _faq_embedding_key(normalized_question: str) -> str:
    """Valkey key for a cached FAQ embedding vector."""
    q_hash = hashlib.sha256(normalized_question.encode("utf-8")).hexdigest()[:16]
    return f"plan:faq:emb:{q_hash}"


def get_or_load(username: str, kind: str, loader):
    try:
        client = _client()
    except (valkey.ValkeyError, ValueError, TypeError):
        logger.warning("Valkey account cache unavailable", exc_info=True)
        return loader()
    if client is None:
        return loader()
    keys = _keys(username)
    generation, key = keys[0], keys[1 + KINDS.index(kind)]
    try:
        # The common hit needs only one round trip; generations are needed only
        # when a source read might later populate the cache.
        value = client.get(key)
        if value is not None:
            return json.loads(value)
        token = client.get(generation)
        if token is None:
            client.set(generation, secrets.token_hex(16), nx=True, ex=TTL * 2)
            token = client.get(generation)
    except (valkey.ValkeyError, ValueError, TypeError):
        logger.warning("Valkey account cache unavailable", exc_info=True)
        return loader()
    value = loader()
    if value is not None and token is not None and not (isinstance(value, dict) and value.get("status") == "error"):
        try:
            client.eval(_PUT_IF_CURRENT, 2, generation, key, token, json.dumps(value), TTL)
        except (valkey.ValkeyError, ValueError, TypeError):
            logger.warning("Valkey account cache write failed", exc_info=True)
    return value


def invalidate_account(username: str) -> None:
    try:
        client = _client()
        if client is None:
            return
        keys = (*_keys(username), _answer_key(username))
        client.eval(_INVALIDATE, len(keys), *keys, secrets.token_hex(16), max(TTL * 2, _answer_ttl() * 2))
    except (valkey.ValkeyError, ValueError, TypeError):
        logger.warning("Valkey account invalidation failed; cached data expires within %s seconds", TTL, exc_info=True)


# --- FAQ Cache ---

def get_faq_answer(normalized_question: str) -> dict | None:
    """Get cached FAQ answer from Valkey. Returns None if not found or error."""
    try:
        client = _client()
        if client is None:
            return None
        key = _faq_key(normalized_question)
        value = client.get(key)
        if value is not None:
            return json.loads(value)
    except (valkey.ValkeyError, ValueError, TypeError):
        logger.warning("Valkey FAQ cache read failed", exc_info=True)
    return None


def set_faq_answer(normalized_question: str, answer: dict) -> bool:
    """Cache FAQ answer in Valkey with TTL. Returns True if successful."""
    try:
        client = _client()
        if client is None:
            return False
        key = _faq_key(normalized_question)
        client.set(key, json.dumps(answer), ex=FAQ_TTL)
        return True
    except (valkey.ValkeyError, ValueError, TypeError):
        logger.warning("Valkey FAQ cache write failed", exc_info=True)
        return False


def get_faq_embedding(normalized_question: str) -> list[float] | None:
    """Get cached FAQ embedding from Valkey. Returns None if not found or error."""
    try:
        client = _client()
        if client is None:
            return None
        key = _faq_embedding_key(normalized_question)
        value = client.get(key)
        if value is not None:
            return json.loads(value)
    except (valkey.ValkeyError, ValueError, TypeError):
        logger.warning("Valkey FAQ embedding cache read failed", exc_info=True)
    return None


def set_faq_embedding(normalized_question: str, embedding: list[float]) -> bool:
    """Cache FAQ embedding in Valkey with TTL. Returns True if successful."""
    try:
        client = _client()
        if client is None:
            return False
        key = _faq_embedding_key(normalized_question)
        client.set(key, json.dumps(embedding), ex=FAQ_TTL)
        return True
    except (valkey.ValkeyError, ValueError, TypeError):
        logger.warning("Valkey FAQ embedding cache write failed", exc_info=True)
        return False


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


def put_faq_semantic(normalized_question: str, embedding: list[float], answer: dict) -> bool:
    """Store a question's embedding with its public answer in Valkey, so rephrasings can find it later."""
    try:
        client = _faq_client()
        if client is None:
            return False
        key = f"{SEMANTIC_PREFIX}{hashlib.sha256(normalized_question.encode('utf-8')).hexdigest()[:16]}"
        client.set(key, json.dumps({"q": normalized_question, "emb": compact(embedding), "answer": answer}), ex=FAQ_TTL)
        return True
    except (valkey.ValkeyError, ValueError, TypeError):
        logger.warning("Valkey semantic FAQ write failed", exc_info=True)
        return False


def find_faq_semantic(embedding: list[float], min_similarity: float) -> dict | None:
    """Return the cached answer whose stored question embedding is most similar (cosine) to the given one."""
    try:
        client = _faq_client()
        if client is None:
            return None
        keys = [k for _, k in zip(range(SEMANTIC_MAX_SCAN), client.scan_iter(match=f"{SEMANTIC_PREFIX}*", count=200))]
        if not keys:
            return None
        best, best_score = None, min_similarity
        for raw in client.mget(keys):
            if raw is None:
                continue
            entry = json.loads(raw)
            score = _cosine(embedding, entry["emb"])
            if score >= best_score:
                best, best_score = entry, score
        return {**best["answer"], "similarity": round(best_score, 4)} if best else None
    except (valkey.ValkeyError, ValueError, TypeError, KeyError):
        logger.warning("Valkey semantic FAQ read failed", exc_info=True)
        return None


def _entry_row(key: str, raw: str, ttl: int) -> dict:
    entry = json.loads(raw)
    answer = entry.get("answer", {})
    emb = entry.get("emb", [])
    return {
        "key": key, "question": entry.get("q", ""), "answer": answer.get("answer", ""),
        "source": answer.get("source", ""), "answer_type": answer.get("answer_type") or "learned",
        "ttl_seconds": ttl if ttl >= 0 else None, "dimensions": len(emb),
        "vector_preview": [round(x, 4) for x in emb[:6]],
    }


def inspect_entries(limit: int = 2000) -> list[dict]:
    """Admin view of the cached questions with their embeddings. Raises on a Valkey error."""
    client = _faq_client()
    if client is None:
        return []
    keys = [k for _, k in zip(range(limit), client.scan_iter(match=f"{SEMANTIC_PREFIX}*", count=500))]
    rows = []
    for start in range(0, len(keys), 100):
        batch = keys[start:start + 100]
        pipe = client.pipeline()
        for key in batch:
            pipe.get(key)
            pipe.ttl(key)
        values = pipe.execute()
        for key, raw, ttl in zip(batch, values[0::2], values[1::2]):
            if raw is not None:
                rows.append(_entry_row(key, raw, ttl))
    return sorted(rows, key=lambda r: r["question"])


def get_entry(key: str) -> dict | None:
    """One cached question with its full embedding vector, or None."""
    if not key.startswith(SEMANTIC_PREFIX):
        return None
    client = _faq_client()
    raw = client.get(key) if client is not None else None
    if raw is None:
        return None
    entry = json.loads(raw)
    return {"id": key, "collection": "Valkey cache", "dimensions": len(entry.get("emb", [])),
            "payload": {"question": entry.get("q"), **entry.get("answer", {}), "ttl_seconds": client.ttl(key)},
            "vector": entry.get("emb", [])}


def warm_faq_cache(questions: list[dict]) -> int:
    """
    Pre-warm FAQ cache with hot questions from Qdrant.
    questions: list of {"key", "id", "answer", "source"} from faq_top_public/faq_frequent_phrasings
    Returns number of entries cached.
    """
    try:
        client = _client()
        if client is None:
            return 0
        cached = 0
        pipe = client.pipeline()
        for q in questions:
            key = _faq_key(q["key"])
            value = json.dumps({"id": q["id"], "answer": q["answer"], "source": q["source"]})
            pipe.set(key, value, ex=FAQ_TTL)
            cached += 1
        pipe.execute()
        return cached
    except (valkey.ValkeyError, ValueError, TypeError):
        logger.warning("Valkey FAQ warm cache failed", exc_info=True)
        return 0


def get_account_info(username: str, info_type: str) -> dict | None:
    """Get cached account info (payment methods, payment history, etc.) from Valkey."""
    try:
        client = _client()
        if client is None:
            return None
        user_hash = hashlib.sha256(username.encode("utf-8")).hexdigest()
        key = f"plan:account:{user_hash}:{info_type}"
        value = client.get(key)
        if value is not None:
            return json.loads(value)
    except (valkey.ValkeyError, ValueError, TypeError):
        logger.warning("Valkey account info cache read failed", exc_info=True)
    return None


def set_account_info(username: str, info_type: str, data: dict, ttl: int = 300) -> bool:
    """Cache account info in Valkey with TTL (default 5 min)."""
    try:
        client = _client()
        if client is None:
            return False
        user_hash = hashlib.sha256(username.encode("utf-8")).hexdigest()
        key = f"plan:account:{user_hash}:{info_type}"
        client.set(key, json.dumps(data), ex=ttl)
        return True
    except (valkey.ValkeyError, ValueError, TypeError):
        logger.warning("Valkey account info cache write failed", exc_info=True)
        return False


def invalidate_account_info(username: str, info_type: str) -> None:
    """Invalidate specific account info cache. Supports prefix matching with trailing ':' for pattern deletion."""
    try:
        client = _client()
        if client is None:
            return
        user_hash = hashlib.sha256(username.encode("utf-8")).hexdigest()
        prefix = f"plan:account:{user_hash}:{info_type}"
        if info_type.endswith(":"):
            # Pattern deletion: delete all keys matching prefix
            for key in client.scan_iter(match=f"{prefix}*"):
                client.delete(key)
        else:
            client.delete(prefix)
    except (valkey.ValkeyError, ValueError, TypeError):
        logger.warning("Valkey account info invalidation failed", exc_info=True)


# Shared, bounded LRU of users; each user has a small hash of answer entries.
ANSWER_USERS = "plan:account:answers:users"
_ANSWER_PUT = """
if redis.call('GET', KEYS[1]) ~= ARGV[1] then return 0 end
redis.call('ZADD', KEYS[2], ARGV[2], KEYS[3])
redis.call('HSET', KEYS[3], ARGV[3], ARGV[4])
redis.call('EXPIRE', KEYS[3], ARGV[5])
redis.call('SET', KEYS[3] .. ':owner', ARGV[8], 'EX', ARGV[5])
local fields = redis.call('HKEYS', KEYS[3])
for i = 1, #fields - tonumber(ARGV[7]) do redis.call('HDEL', KEYS[3], fields[i]) end
local excess = redis.call('ZCARD', KEYS[2]) - tonumber(ARGV[6])
if excess > 0 then
  local victims = redis.call('ZRANGE', KEYS[2], 0, excess - 1)
  for _, key in ipairs(victims) do redis.call('DEL', key, key .. ':owner'); redis.call('ZREM', KEYS[2], key) end
end
return 1
"""
_ANSWER_READ = """
local data = redis.call('HGETALL', KEYS[2])
if #data > 0 then redis.call('ZADD', KEYS[1], ARGV[1], KEYS[2])
else redis.call('ZREM', KEYS[1], KEYS[2]) end
return data
"""

def semantic_threshold() -> float:
    try:
        return float(os.getenv("ACCOUNT_SEMANTIC_THRESHOLD", "0.85"))
    except ValueError:
        return 0.85

def best_semantic_match(vector: list[float], entries: list[dict]) -> dict | None:
    """The user's own entry most similar to the vector, only if its cosine is strictly above the threshold."""
    scored = [(_cosine(vector, e["embedding"]), e) for e in entries if e.get("embedding")]
    score, entry = max(scored, default=(0.0, None), key=lambda item: item[0])
    return entry if entry is not None and score > semantic_threshold() else None

def _answer_key(username: str) -> str:
    return f"plan:account:answer:{hashlib.sha256(username.encode('utf-8')).hexdigest()}"

def _answer_ttl() -> int:
    return max(1, int(os.getenv("ACCOUNT_ANSWER_TTL_SECONDS", "120")))

def answer_snapshot(username: str) -> str | None:
    """Capture a generation before source reads to reject writes racing a payment."""
    try:
        client = _client()
        if client is None:
            return None
        generation = _keys(username)[0]
        client.set(generation, secrets.token_hex(16), nx=True, ex=max(TTL * 2, _answer_ttl() * 2))
        return client.get(generation)
    except (valkey.ValkeyError, ValueError, TypeError):
        logger.warning("Account answer cache unavailable", exc_info=True)
        return None

def read_account_answers(username: str) -> list[dict]:
    try:
        client = _client()
        if client is None:
            return []
        raw = client.eval(_ANSWER_READ, 2, ANSWER_USERS, _answer_key(username), time.time())
        return [entry for entry in (json.loads(value) for value in raw[1::2]) if entry.get("expires", 0) > time.time()]
    except (valkey.ValkeyError, ValueError, TypeError, KeyError):
        logger.warning("Account answer cache read failed", exc_info=True)
        return []

def inspect_account_cache(username: str) -> dict:
    """Inspect one user's cache without refreshing LRU or returning raw account data."""
    client = _client()
    if client is None:
        raise RuntimeError("Valkey is not configured")
    keys = _keys(username)
    pipe = client.pipeline(transaction=False)
    user_hash = hashlib.sha256(username.encode("utf-8")).hexdigest()
    data_keys = [*keys[1:], f"plan:account:{user_hash}:payment_methods"]
    kinds = (*KINDS, "payment_methods")
    for key in data_keys:
        pipe.exists(key)
        pipe.ttl(key)
    answer_key = _answer_key(username)
    pipe.hgetall(answer_key)
    values = pipe.execute()
    data = [
        {"kind": kind, "ttl_seconds": values[2 * i + 1] if values[2 * i + 1] >= 0 else None}
        for i, kind in enumerate(kinds) if values[2 * i]
    ]
    now = time.time()
    answers = []
    for raw in values[-1].values():
        try:
            entry = json.loads(raw)
            remaining = entry["expires"] - now
            if remaining > 0:
                answers.append({"question": entry["question"], "answer": entry["answer"],
                                "sources": entry.get("sources", []), "ttl_seconds": max(0, int(remaining)),
                                "semantic": bool(entry.get("embedding"))})
        except (ValueError, TypeError, KeyError):
            logger.warning("Skipping invalid account answer cache entry")
    return {"username": username, "data": data, "answers": answers}


def list_account_answer_users(query: str, limit: int, offset: int) -> dict:
    """List active answer-cache users without touching their LRU order."""
    client = _client()
    if client is None:
        raise RuntimeError("Valkey is not configured")
    # Cap administrative work even if the configured active-user limit is larger.
    max_users = 10000
    keys = client.zrevrange(ANSWER_USERS, 0, max_users - 1)
    pipe = client.pipeline(transaction=False)
    for key in keys:
        pipe.get(f"{key}:owner")
        pipe.exists(key)
    values = pipe.execute() if keys else []
    needle = query.strip().casefold()
    users = []
    for key, owner, exists in zip(keys, values[::2], values[1::2]):
        if not exists:
            continue
        label = owner or f"Unknown user ({key.rsplit(':', 1)[-1][:12]})"
        if needle and needle not in label.casefold():
            continue
        users.append((key, label))
    selected = users[offset:offset + limit]
    pipe = client.pipeline(transaction=False)
    for key, _ in selected:
        pipe.hgetall(key)
    hashes = pipe.execute() if selected else []
    now = time.time()
    rows = []
    for (_, username), values in zip(selected, hashes):
        for raw in values.values():
            try:
                entry = json.loads(raw)
                remaining = entry["expires"] - now
                if remaining > 0:
                    rows.append({"username": username, "question": entry["question"], "answer": entry["answer"],
                                 "sources": entry.get("sources", []), "ttl_seconds": max(0, int(remaining)),
                                 "semantic": bool(entry.get("embedding"))})
            except (ValueError, TypeError, KeyError):
                logger.warning("Skipping invalid account answer cache entry")
    return {"rows": rows, "total_users": len(users), "offset": offset, "limit": limit,
            "truncated": len(keys) == max_users}

def put_account_answer(username: str, token: str | None, question: str, answer: str, sources: list[str], embedding: list[float] | None, intent: str | None = None) -> bool:
    try:
        client = _client()
        if client is None or token is None:
            return False
        ttl = _answer_ttl()
        entry = {"question": question, "answer": answer, "sources": sources, "embedding": compact(embedding) if embedding else None, "expires": time.time() + ttl, "intent": intent}
        field = hashlib.sha256(question.encode("utf-8")).hexdigest()
        return bool(client.eval(_ANSWER_PUT, 3, _keys(username)[0], ANSWER_USERS, _answer_key(username), token, time.time(), field, json.dumps(entry), ttl, max(1, int(os.getenv("ACCOUNT_CACHE_MAX_USERS", "4"))), max(1, int(os.getenv("ACCOUNT_CACHE_MAX_ENTRIES_PER_USER", "20"))), username))
    except (valkey.ValkeyError, ValueError, TypeError):
        logger.warning("Account answer cache write failed", exc_info=True)
        return False
