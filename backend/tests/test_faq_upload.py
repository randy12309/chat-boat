import asyncio
import io
import os

import openpyxl
import pytest
import valkey
from fastapi.testclient import TestClient

from app import auth
from app.agents import faq
from app.main import app
from mcp_server import cache, faq_excel


def workbook(rows):
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.append(["Question No.", "Topic", "Question", "Answer", "Answer type", "Policy references"])
    for row in rows:
        sheet.append(row)
    stream = io.BytesIO()
    book.save(stream)
    return stream.getvalue()


class Pipeline:
    def __init__(self, client):
        self.client, self.operations = client, []

    def delete(self, *keys):
        self.operations.append(("delete", keys))
        return self

    def set(self, key, value):
        self.operations.append(("set", (key, value)))
        return self

    def execute(self):
        for operation, args in self.operations:
            if operation == "delete":
                for key in args:
                    self.client.data.pop(key, None)
            else:
                self.client.data[args[0]] = args[1]


class FakeValkey:
    def __init__(self):
        self.data = {"plan:faq:old": "old", "plan:account:user:balance": "private"}

    def scan_iter(self, match, count):
        assert match == "plan:faq:*"
        return iter([key for key in self.data if key.startswith("plan:faq:")])

    def pipeline(self, transaction=True):
        assert transaction
        return Pipeline(self)


def test_upload_replaces_only_faq_keys_and_saves_source(monkeypatch, tmp_path):
    client = FakeValkey()
    monkeypatch.setattr(cache, "_faq_client", lambda: client)
    monkeypatch.setattr(faq_excel, "UPLOADED_FILE", tmp_path / "faq.xlsx")
    monkeypatch.setattr(faq_excel.embeddings, "embed", lambda texts: [[1.0, 0.0] for _ in texts])
    data = workbook([[1, "Plan", "What is the rule?", "A policy answer.", "Public", "P01"]])
    assert faq_excel.replace_uploaded(data) == 1
    assert "plan:faq:old" not in client.data
    assert client.data["plan:account:user:balance"] == "private"
    assert cache._faq_key("what is the rule") in client.data
    assert "plan:faq:revision" in client.data
    assert faq_excel.UPLOADED_FILE.read_bytes() == data
    assert faq_excel.excel_path() == faq_excel.UPLOADED_FILE


def test_invalid_upload_preserves_existing_cache(monkeypatch, tmp_path):
    client = FakeValkey()
    monkeypatch.setattr(cache, "_faq_client", lambda: client)
    monkeypatch.setattr(faq_excel, "UPLOADED_FILE", tmp_path / "faq.xlsx")
    data = workbook([[1, "Plan", "Same?", "One", "Public", "P01"], [2, "Plan", "Same!", "Two", "Public", "P01"]])
    with pytest.raises(faq_excel.FaqUploadError):
        faq_excel.replace_uploaded(data)
    assert client.data["plan:faq:old"] == "old"
    assert not faq_excel.UPLOADED_FILE.exists()


def test_embedding_failure_preserves_existing_cache(monkeypatch, tmp_path):
    client = FakeValkey()
    monkeypatch.setattr(cache, "_faq_client", lambda: client)
    monkeypatch.setattr(faq_excel, "UPLOADED_FILE", tmp_path / "faq.xlsx")
    def unavailable(_):
        raise RuntimeError("embedding unavailable")
    monkeypatch.setattr(faq_excel.embeddings, "embed", unavailable)
    with pytest.raises(RuntimeError):
        faq_excel.replace_uploaded(workbook([[1, "Plan", "What is the rule?", "Answer", "Public", "P01"]]))
    assert client.data["plan:faq:old"] == "old"
    assert not faq_excel.UPLOADED_FILE.exists()


@pytest.mark.skipif(not os.getenv("TEST_VALKEY_URL"), reason="Requires a disposable Valkey instance")
def test_upload_with_valkey_preserves_account_keys(monkeypatch, tmp_path):
    client = valkey.Valkey.from_url(os.environ["TEST_VALKEY_URL"], decode_responses=True)
    monkeypatch.setattr(cache, "_faq_client", lambda: client)
    monkeypatch.setattr(faq_excel, "UPLOADED_FILE", tmp_path / "faq.xlsx")
    monkeypatch.setattr(faq_excel.embeddings, "embed", lambda texts: [[1.0, 0.0] for _ in texts])
    client.set("plan:faq:old", "old")
    client.set("plan:account:upload-test", "private")
    data = workbook([[1, "Plan", "What is the rule?", "A policy answer.", "Public", "P01"]])
    assert faq_excel.replace_uploaded(data) == 1
    assert client.get("plan:faq:old") is None
    assert client.get("plan:account:upload-test") == "private"
    monkeypatch.setattr(cache, "_client", lambda: client)
    revision, exact = cache.faq_snapshot("what is the rule")
    assert revision == client.get("plan:faq:revision")
    assert exact["answer"] == "A policy answer."


def test_upload_endpoint_requires_admin_and_xlsx(monkeypatch):
    client = TestClient(app)
    monkeypatch.setattr(auth, "is_admin", lambda username: username == "randy")
    def headers(user):
        return {"Authorization": f"Bearer {auth.create_token(user)}"}
    assert client.post("/api/v1/admin/valkey-cache/upload", files={"file": ("faq.xlsx", b"x")}).status_code == 401
    assert client.post("/api/v1/admin/valkey-cache/upload", headers=headers("john"), files={"file": ("faq.xlsx", b"x")}).status_code == 403
    assert client.post("/api/v1/admin/valkey-cache/upload", headers=headers("randy"), files={"file": ("faq.pdf", b"x")}).status_code == 415
    assert client.post("/api/v1/admin/valkey-cache/upload", headers=headers("randy"), files={"file": ("faq.xlsx", b"x")}).status_code == 422
    monkeypatch.setattr(faq_excel, "replace_uploaded", lambda data: 100)
    result = client.post("/api/v1/admin/valkey-cache/upload", headers=headers("randy"), files={"file": ("faq.xlsx", b"data")})
    assert result.status_code == 200 and result.json()["count"] == 100


def test_clear_policy_cache_endpoint_requires_admin(monkeypatch):
    client = TestClient(app)
    monkeypatch.setattr(auth, "is_admin", lambda username: username == "randy")
    calls = []
    monkeypatch.setattr(cache, "clear_faq_cache", lambda: calls.append(True) or 2)
    def headers(user):
        return {"Authorization": f"Bearer {auth.create_token(user)}"}
    assert client.delete("/api/v1/admin/valkey-cache").status_code == 401
    assert client.delete("/api/v1/admin/valkey-cache", headers=headers("john")).status_code == 403
    result = client.delete("/api/v1/admin/valkey-cache", headers=headers("randy"))
    assert result.status_code == 200 and result.json()["deleted_keys"] == 2
    assert calls == [True]


@pytest.mark.skipif(not os.getenv("TEST_VALKEY_URL"), reason="Requires a disposable Valkey instance")
def test_clear_faq_leaves_account_keys_and_updates_revision(monkeypatch):
    client = valkey.Valkey.from_url(os.environ["TEST_VALKEY_URL"], decode_responses=True)
    monkeypatch.setattr(cache, "_faq_client", lambda: client)
    client.set("plan:faq:revision", "old")
    client.set("plan:faq:old", "old")
    client.set("plan:account:clear-test", "private")
    assert cache.clear_faq_cache() >= 2
    assert client.get("plan:faq:old") is None
    assert client.get("plan:faq:revision") != "old"
    assert client.get("plan:account:clear-test") == "private"


def test_faq_revision_change_clears_worker_memory(monkeypatch):
    revision = ["old"]
    monkeypatch.setattr(cache, "faq_snapshot", lambda key: (revision[0], None))
    service = faq.FaqService()
    async def no_semantic(_):
        return None
    monkeypatch.setattr(service, "_semantic_lookup", no_semantic)
    asyncio.run(service.lookup("old question"))
    service._cache_put("old question", {"id": "old", "answer": "old", "source": "old"})
    revision[0] = "new"
    assert asyncio.run(service.lookup("old question")) is None
