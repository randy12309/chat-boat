import asyncio

import pytest

from app.agents.mcp_client import call_tool
from mcp_server import embeddings, knowledge_pdf, server, vectordb


def run(coro):
    return asyncio.run(coro)


def set_setting(key, value):
    vectordb.set_setting(key, value)


@pytest.fixture
def restore_settings():
    original = vectordb.get_settings()
    yield
    for key, value in original.items():
        set_setting(key, value)


def test_policy_lookup_by_id():
    r = run(call_tool("get_policy_info", {"topic": "P10"}))
    assert r["status"] == "ok" and r["source"] == "policy:P10" and "$375" in r["data"]["text"]


def test_unknown_policy_topic_is_not_found():
    assert run(call_tool("get_policy_info", {"topic": "quantum zebra"}))["error_type"] == "not_found"


def test_policy_store_outage_is_reported(monkeypatch):
    monkeypatch.setattr(vectordb, "find_policy", lambda *a: (_ for _ in ()).throw(RuntimeError("down")))
    assert run(call_tool("get_policy_info", {"topic": "P10"}))["error_type"] == "unavailable"


def test_eligibility_numbers_come_from_plan_settings(monkeypatch, restore_settings):
    monkeypatch.setattr(server.db, "enabled", lambda: True)
    monkeypatch.setattr(server.db, "total_paid", lambda user: 1_000.00)  # EPAYMENT.AMT is in major units
    set_setting("eligibility_threshold", "5000")
    set_setting("quarterly_premium", "500")
    principal = server.mint_principal("john")
    d = run(call_tool("get_eligibility", {"principal": principal}))["data"]
    assert d["threshold"] == 5000 and d["remaining"] == 4000 and d["quarters_remaining"] == 8
    assert d["quarterly_premium"] == 500 and d["eligible"] is False


def test_premium_breakdown_uses_database_ratio(restore_settings):
    set_setting("base_coverage_ratio", "0.5")
    d = run(call_tool("explain_premium", {"principal": server.mint_principal("randy")}))["data"]
    assert d["breakdown"] == {"base_coverage": 156.0, "taxes_and_fees": 156.0}


def test_missing_plan_settings_are_an_error_not_a_default(monkeypatch):
    monkeypatch.setattr(vectordb, "get_settings", lambda: {})
    r = run(call_tool("get_eligibility", {"principal": server.mint_principal("john")}))
    assert r["status"] == "error" and r["error_type"] == "unavailable"


def test_seed_is_idempotent_and_keeps_database_edits(restore_settings):
    set_setting("quarterly_premium", "999")
    assert vectordb.seed() == {"settings": 0, "knowledge": 0}
    assert vectordb.get_settings()["quarterly_premium"] == "999"


def test_pdf_parses_into_the_documented_records():
    parsed = knowledge_pdf.parse(vectordb.DEFAULT_PDF)
    assert (len(parsed["policies"]), len(parsed["faqs"]), len(parsed["sources"])) == (27, 300, 38)
    assert knowledge_pdf.validate(parsed) == []
    modes = [q["mode"] for q in parsed["faqs"]]
    assert [modes.count(m) for m in ("REFERENCE", "LIVE", "REVIEW", "APP_GAP")] == [115, 96, 43, 46]
    p10 = parsed["policies"][9]
    assert p10["id"] == "P10" and "$375" in p10["text"] and p10["source_ids"] == ["S05", "S16", "S35"]


def test_pdf_records_are_embedded_in_qdrant():
    records = list(vectordb._scroll(vectordb.KNOWLEDGE))
    assert len(records) == 27 and all(r["kind"] == "policy" for r in records)  # FAQ questions are not stored in Qdrant
    p01 = next(r for r in records if r["record_id"] == "P01")
    assert p01["source_urls"][0].startswith("https://www.sagaftraplans.org")
    assert vectordb.client().count(vectordb.KNOWLEDGE).count == 27


def test_unrelated_text_does_not_match_a_policy():
    assert vectordb.find_policy("quantum zebra", 0.55) is None


def test_reingesting_the_pdf_is_idempotent_and_a_missing_pdf_changes_nothing(tmp_path):
    assert vectordb.seed_knowledge() == 0
    assert vectordb.client().count(vectordb.KNOWLEDGE).count == 27
    missing = tmp_path / "absent.pdf"
    assert vectordb.seed_knowledge(missing) == 0 and vectordb.client().count(vectordb.KNOWLEDGE).count == 27


def test_embeddings_have_the_column_dimension():
    assert all(len(v) == embeddings.DIMENSIONS for v in embeddings.embed(["a", "b c"]))
