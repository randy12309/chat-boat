from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def token(user):
    r = client.post("/api/v1/auth/login", json={"username": user, "password": "password"})
    return r.json()["access_token"]


def test_home_requires_auth():
    assert client.get("/api/v1/home").status_code == 401


def test_home_returns_user_data():
    r = client.get("/api/v1/home", headers={"Authorization": f"Bearer {token('randy')}"})
    assert r.status_code == 200
    body = r.json()
    assert body["welcome"]["display_name"] == "Randy"
    assert body["premium_alert"]["status"] == "Pending"
    assert body["inbox"]["unread_count"] == 1
    assert len(body["suggested_questions"]) > 0
