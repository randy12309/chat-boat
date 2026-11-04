import asyncio

import pytest

from app.agents.mcp_client import call_tool
from mcp_server import embeddings, pgvec, server


def run(coro):
    return asyncio.run(coro)


def set_setting(key, value):
    with pgvec.connection() as conn:
        conn.execute("UPDATE plan_settings SET value = %s WHERE key = %s", (value, key))


@pytest.fixture
def restore_settings():
    original = pgvec.get_settings()
    yield
    for key, value in original.items():
        set_setting(key, value)


def test_policy_topics_come_from_the_database():
    names = pgvec.policy_topic_names()
    assert {"grace period", "renewal", "dependents", "cancellation", "eligibility", "pension benefits"} <= set(names)


def test_policy_lookup_by_name():
    r = run(call_tool("get_policy_info", {"topic": "Grace Period"}))
    assert r["status"] == "ok" and r["source"] == "policy:grace period" and "30-day" in r["data"]["text"]


def test_policy_text_is_rendered_from_plan_settings():
    r = run(call_tool("get_policy_info", {"topic": "pension"}))
    assert r["status"] == "ok" and r["data"]["topic"] == "pension benefits"
    assert "28000" in r["data"]["text"] and "{" not in r["data"]["text"]


def test_policy_semantic_neighbour_without_shared_name():
    # No topic name appears in the query; the match comes from the embedding alone.
    r = run(call_tool("get_policy_info", {"topic": "submitting supporting documents"}))
    assert r["status"] == "ok" and r["data"]["topic"] == "claims"


def test_unknown_policy_topic_is_not_found():
    assert run(call_tool("get_policy_info", {"topic": "quantum zebra"}))["error_type"] == "not_found"


def test_policy_store_outage_is_reported(monkeypatch):
    monkeypatch.setattr(pgvec, "find_policy", lambda *a: (_ for _ in ()).throw(RuntimeError("down")))
    assert run(call_tool("get_policy_info", {"topic": "renewal"}))["error_type"] == "unavailable"


def test_eligibility_numbers_come_from_plan_settings(monkeypatch, restore_settings):
    monkeypatch.setattr(server.db, "enabled", lambda: True)
    monkeypatch.setattr(server.db, "total_paid_minor", lambda user: 100_000)  # 1,000.00 paid
    set_setting("eligibility_threshold", "5000")
    set_setting("quarterly_premium", "500")
    principal = server.mint_principal("bharath")
    d = run(call_tool("get_eligibility", {"principal": principal}))["data"]
    assert d["threshold"] == 5000 and d["remaining"] == 4000 and d["quarters_remaining"] == 8
    assert d["quarterly_premium"] == 500 and d["eligible"] is False


def test_premium_breakdown_uses_database_ratio(restore_settings):
    set_setting("base_coverage_ratio", "0.5")
    d = run(call_tool("explain_premium", {"principal": server.mint_principal("randy")}))["data"]
    assert d["breakdown"] == {"base_coverage": 156.0, "taxes_and_fees": 156.0}


def test_missing_plan_settings_are_an_error_not_a_default(monkeypatch):
    monkeypatch.setattr(pgvec, "get_settings", lambda: {})
    r = run(call_tool("get_eligibility", {"principal": server.mint_principal("bharath")}))
    assert r["status"] == "error" and r["error_type"] == "unavailable"


def test_seed_is_idempotent_and_keeps_database_edits(restore_settings):
    set_setting("quarterly_premium", "999")
    assert pgvec.seed() == {"settings": 0, "policies": 0, "faq": 0}
    assert pgvec.get_settings()["quarterly_premium"] == "999"


def test_embeddings_have_the_column_dimension():
    assert all(len(v) == embeddings.DIMENSIONS for v in embeddings.embed(["a", "b c"]))


def test_stale_postmaster_pid_is_removed(tmp_path):
    pid_file = tmp_path / "postmaster.pid"
    pid_file.write_text("999999999\n/data\n")
    pgvec._clear_stale_pid(str(tmp_path))
    assert not pid_file.exists()
