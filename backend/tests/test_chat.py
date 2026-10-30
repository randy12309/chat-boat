from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def auth(user="bharath"):
    r = client.post("/api/v1/auth/login", json={"username": user, "password": "password"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_chat_requires_auth():
    assert client.post("/api/v1/chat", json={"message": "hi"}).status_code == 401


def test_chat_premium_reply_uses_account_data():
    r = client.post("/api/v1/chat", json={"message": "What is my premium?"}, headers=auth())
    assert r.status_code == 200
    assert "245.50" in r.json()["reply"]


def test_chat_rejects_empty_message():
    r = client.post("/api/v1/chat", json={"message": ""}, headers=auth())
    assert r.status_code == 422
