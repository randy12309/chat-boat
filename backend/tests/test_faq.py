import asyncio
import json

import pytest

from app.agents import faq
from app.agents import graph as agent
from app.agents.faq import FaqService
from mcp_server import cache


def run(coro):
    return asyncio.run(coro)


def test_faq_ttl_days_takes_precedence_over_seconds(monkeypatch):
    monkeypatch.setenv("FAQ_CACHE_TTL_DAYS", "10")
    monkeypatch.setenv("FAQ_CACHE_TTL_SECONDS", "120")
    assert faq.cache_ttl_seconds() == 864000
    monkeypatch.delenv("FAQ_CACHE_TTL_DAYS")
    assert faq.cache_ttl_seconds() == 120


@pytest.fixture
def valkey(monkeypatch):
    """A dict-backed stand-in for the Valkey FAQ functions, so no server is needed."""
    answers, semantic = {}, {}
    monkeypatch.setattr(cache, "faq_snapshot", lambda key: (None, answers.get(key)))
    monkeypatch.setattr(cache, "set_faq_answer", lambda key, a: answers.__setitem__(key, a) or True)
    monkeypatch.setattr(cache, "put_faq_semantic", lambda key, emb, a: semantic.__setitem__(key, (emb, a)) or True)

    def find(embedding, min_similarity, on_score=None, *, question=""):
        scored = [(cache._cosine(embedding, emb), a) for emb, a in semantic.values() if not cache.is_retrieved_policy_context(a)]
        if on_score:
            for key, (emb, answer) in semantic.items():
                if not cache.is_retrieved_policy_context(answer):
                    on_score({"q": key, "answer": answer}, cache._cosine(embedding, emb))
        best = max(scored, key=lambda x: x[0], default=None)
        return {**best[1], "similarity": best[0]} if best and best[0] >= min_similarity else None

    monkeypatch.setattr(cache, "find_faq_semantic", find)
    return answers, semantic


@pytest.fixture
def service(valkey):
    s = FaqService()
    faq.set_service(s)
    yield s
    faq.set_service(None)


def seed_curated_faq(valkey, question):
    answers, semantic = valkey
    key = faq.normalize(question)
    entry = {"id": "Q1", "answer": "30 days.", "source": "faq:Q1"}
    answers[key] = entry
    semantic[key] = (faq.embeddings.embed([key])[0], entry)


def test_exact_question_hits_valkey(service, valkey):
    seed_curated_faq(valkey, "What is the grace period?")
    service._cache.clear()
    hit = run(service.lookup("what is the grace period"))
    assert hit["layer"] == "valkey" and hit["answer"] == "30 days."


def test_rephrased_question_hits_by_embedding(service, valkey):
    seed_curated_faq(valkey, "what is the grace period for late payment")
    service._cache.clear()
    hit = run(service.lookup("grace period for late payment what is it"))
    assert hit["layer"] == "valkey" and hit["source"] == "faq:Q1" and hit["similarity"] >= 0.75


def test_hit_is_copied_into_the_memory_cache(service, valkey):
    seed_curated_faq(valkey, "what is the grace period")
    service._cache.clear()
    assert run(service.lookup("what is the grace period"))["layer"] == "valkey"
    assert len(service._cache) == 1


def test_legacy_qdrant_context_is_not_a_faq_hit(service, valkey):
    key = "what is the grace period"
    entry = {"id": "policy:uploaded", "answer": "Raw policy text", "source": "policy:uploaded"}
    valkey[0][key] = entry
    valkey[1][key] = (faq.embeddings.embed([key])[0], entry)
    service._cache_put(key, entry)
    assert run(service.lookup("What is the grace period?")) is None


def test_unrelated_question_misses(service, valkey):
    seed_curated_faq(valkey, "what is the grace period")
    service._cache.clear()
    assert run(service.lookup("tell me a joke")) is None


def test_policy_logs_every_similarity_and_threshold_even_for_exact_hit(service, valkey, monkeypatch):
    monkeypatch.setenv("FAQ_LOG_EACH_SIMILARITY", "1")
    monkeypatch.setenv("FAQ_SEMANTIC_MIN_SIMILARITY", "0.9")
    monkeypatch.setattr(faq.embeddings, "embed", lambda texts: [[1.0, 0.0]])
    answers, semantic = valkey
    answers["policy question"] = {"id": "Q1", "answer": "Exact", "source": "faq:Q1"}
    semantic["policy question"] = ([1.0, 0.0], answers["policy question"])
    semantic["other question"] = ([0.8, 0.6], {"id": "Q2", "answer": "Other", "source": "faq:Q2"})
    events = []
    monkeypatch.setattr(faq.trace, "event", lambda actor, message, **kwargs: events.append((message, kwargs)))
    assert run(service.lookup("Policy question"))["answer"] == "Exact"
    assert any("FAQ Q1: similarity=1.000000" in message for message, _ in events)
    assert any("FAQ Q2: similarity=0.800000" in message for message, _ in events)
    summary = next(kwargs for message, kwargs in events if message.startswith("FAQ comparison:"))
    assert summary["threshold"] == 0.9 and summary["similarity"] == pytest.approx(1.0)
    assert summary["status"] == "compared"


def test_policy_miss_reports_best_score_below_threshold(service, valkey, monkeypatch):
    monkeypatch.setenv("FAQ_SEMANTIC_MIN_SIMILARITY", "0.9")
    monkeypatch.setattr(faq.embeddings, "embed", lambda texts: [[1.0, 0.0]])
    valkey[1]["other question"] = ([0.8, 0.6], {"id": "Q2", "answer": "Other", "source": "faq:Q2"})
    events = []
    monkeypatch.setattr(faq.trace, "event", lambda actor, message, **kwargs: events.append((message, kwargs)))
    assert run(service.lookup("Uncached question")) is None
    summary = next(kwargs for message, kwargs in events if message.startswith("FAQ comparison:"))
    assert summary["similarity"] == pytest.approx(0.8)
    assert summary["threshold"] == 0.9 and summary["status"] == "miss"


def test_valkey_semantic_lookup_reports_each_valid_candidate(monkeypatch):
    class Client:
        def scan_iter(self, **kwargs):
            return iter(["one", "two", "incompatible", "old-raw"])
        def mget(self, keys):
            return [json.dumps({"q": "one", "emb": [1.0, 0.0], "answer": {"id": "Q1", "answer": "One", "source": "faq:1"}}),
                    json.dumps({"q": "two", "emb": [0.8, 0.6], "answer": {"id": "Q2", "answer": "Two", "source": "faq:2"}}),
                    json.dumps({"q": "bad", "emb": [1.0], "answer": {"id": "Q3"}}),
                    json.dumps({"q": "old", "emb": [1.0, 0.0], "answer": {"id": "legacy", "source": "policy:uploaded"}})]
    monkeypatch.setattr(cache, "_faq_client", lambda: Client())
    scores = []
    hit = cache.find_faq_semantic([1.0, 0.0], 0.9, lambda entry, score: scores.append((entry["q"], score)))
    assert hit["id"] == "Q1"
    assert scores == [("one", 1.0), ("two", 0.8)]


def test_overlong_question_is_not_cached(service, valkey):
    assert run(service.lookup("x " * 400)) is None
    assert not valkey[0] and not valkey[1]


def test_embedding_failure_falls_through(service, monkeypatch):
    from mcp_server import embeddings

    def boom(texts):
        raise RuntimeError("embedder down")

    monkeypatch.setattr(embeddings, "embed", boom)
    assert run(service.lookup("what is the grace period")) is None


def test_graph_account_question_never_touches_faq(service, monkeypatch):
    monkeypatch.setenv("FAQ_ENABLED", "1")
    monkeypatch.setattr(agent.llm, "enabled", lambda: True)

    async def account(state):
        return "account", ""

    async def no_lookup(*_a, **_k):
        raise AssertionError("account questions must not hit the FAQ cache")

    monkeypatch.setattr(agent, "_llm_route", account)
    monkeypatch.setattr(agent.faq, "lookup", no_lookup)
    r = run(agent.run_agent(None, "What is my balance?"))
    assert "sign in" in r["reply"].lower()

def test_semantic_match_skips_corrupt_rows_and_conflicting_policy_year(monkeypatch):
    def candidate(year, vector):
        return json.dumps({"q": f"benefits for {year}", "emb": vector,
                           "answer": {"answer": str(year), "source": f"faq:{year}"}})

    class Client:
        def scan_iter(self, **kwargs):
            return iter(["broken", "wrong-year", "valid"])

        def mget(self, keys):
            return ["invalid json", candidate(2026, [1, 0]), candidate(2027, [0.95, 0.2])]

    monkeypatch.setattr(cache, "_faq_client", Client)
    result = cache.find_faq_semantic([1, 0], 0.9, question="benefits for 2027")
    assert result["answer"] == "2027"
    assert cache.find_faq_semantic([1, 0], 0.9, question="benefits for 2028") is None


@pytest.mark.parametrize("invalid", ["nan", "-1", "2", "broken"])
def test_invalid_threshold_uses_default(monkeypatch, invalid):
    monkeypatch.setenv("ACCOUNT_SEMANTIC_THRESHOLD", invalid)
    assert cache.semantic_threshold() == 0.85

@pytest.mark.parametrize(("second_score", "second_answer", "expected"), [
    (0.95, "Different answer", None),
    (0.94, "Different answer", None),
    (0.94, "Same answer", "Same answer"),
    (0.88, "Different answer", "Same answer"),
])
def test_near_ties_between_distinct_answers_fall_through(monkeypatch, second_score, second_answer, expected):
    import math

    def row(score, answer):
        return json.dumps({"q": "coverage rule", "emb": [score, math.sqrt(1 - score ** 2)],
                           "answer": {"source": "faq:coverage", "answer": answer}})

    class Client:
        def scan_iter(self, **kwargs):
            return iter(["first", "second"])

        def mget(self, keys):
            return [row(0.95, "Same answer"), row(second_score, second_answer)]

    monkeypatch.setenv("FAQ_SEMANTIC_MIN_MARGIN", "0.03")
    monkeypatch.setattr(cache, "_faq_client", Client)
    result = cache.find_faq_semantic([1, 0], 0.90, question="coverage rule")
    assert (result["answer"] if result else None) == expected
