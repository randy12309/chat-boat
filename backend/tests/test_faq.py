import asyncio

import pytest

from app.agents import faq
from app.agents import graph as agent
from app.agents.faq import FaqService
from mcp_server import cache


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def valkey(monkeypatch):
    """A dict-backed stand-in for the Valkey FAQ functions, so no server is needed."""
    answers, semantic = {}, {}
    monkeypatch.setattr(cache, "get_faq_answer", lambda key: answers.get(key))
    monkeypatch.setattr(cache, "set_faq_answer", lambda key, a: answers.__setitem__(key, a) or True)
    monkeypatch.setattr(cache, "put_faq_semantic", lambda key, emb, a: semantic.__setitem__(key, (emb, a)) or True)

    def find(embedding, min_similarity):
        scored = [(cache._cosine(embedding, emb), a) for emb, a in semantic.values()]
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


def test_remembered_question_is_stored_with_its_embedding(service, valkey):
    answers, semantic = valkey
    run(service.remember("What is the grace period?", "30 days.", "policy:P05"))
    assert len(semantic) == 1 and len(answers) == 1
    embedding, entry = next(iter(semantic.values()))
    assert len(embedding) == 384 and entry["answer"] == "30 days."


def test_exact_question_hits_valkey(service):
    run(service.remember("What is the grace period?", "30 days.", "policy:P05"))
    service._cache.clear()
    hit = run(service.lookup("what is the grace period"))
    assert hit["layer"] == "valkey" and hit["answer"] == "30 days."


def test_rephrased_question_hits_by_embedding(service):
    run(service.remember("what is the grace period for late payment", "30 days.", "policy:P05"))
    service._cache.clear()
    hit = run(service.lookup("grace period for late payment what is it"))
    assert hit["layer"] == "valkey" and hit["source"] == "policy:P05" and hit["similarity"] >= 0.75


def test_hit_is_copied_into_the_memory_cache(service):
    run(service.remember("what is the grace period", "30 days.", "policy:P05"))
    service._cache.clear()
    assert run(service.lookup("what is the grace period"))["layer"] == "valkey"
    assert len(service._cache) == 1


def test_unrelated_question_misses(service):
    run(service.remember("what is the grace period", "30 days.", "policy:P05"))
    service._cache.clear()
    assert run(service.lookup("tell me a joke")) is None


def test_overlong_question_is_not_cached(service, valkey):
    run(service.remember("x " * 400, "a", "policy:P05"))
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
