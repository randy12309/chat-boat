from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def login(u, p):
    return client.post("/api/v1/auth/login", json={"username": u, "password": p})


def test_login_success_and_me():
    for user in ("bharath", "randy"):
        r = login(user, "password")
        assert r.status_code == 200
        token = r.json()["access_token"]
        me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert me.json() == {"username": user}


def test_login_rejects_bad_credentials():
    assert login("bharath", "wrong").status_code == 401
    assert login("nobody", "password").status_code == 401


def test_me_requires_valid_token():
    assert client.get("/api/v1/auth/me").status_code == 401
    bad = client.get("/api/v1/auth/me", headers={"Authorization": "Bearer junk"})
    assert bad.status_code == 401
