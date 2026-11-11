import json

from fastapi.testclient import TestClient

from app.agents import graph
from app.main import app

client = TestClient(app)


def auth(user="john"):
    r = client.post("/api/v1/auth/login", json={"username": user, "password": "password"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _direct(monkeypatch, reply):
    async def route(state):
        return "direct", reply

    monkeypatch.setattr(graph.llm, "enabled", lambda: True)
    monkeypatch.setattr(graph, "_llm_route", route)


def test_chat_requires_auth():
    assert client.post("/api/v1/chat", json={"message": "hi"}).status_code == 401


def test_chat_returns_the_supervisors_direct_answer(monkeypatch):
    _direct(monkeypatch, "Your premium is 245.50.")
    r = client.post("/api/v1/chat", json={"message": "What is my premium?"}, headers=auth())
    assert r.status_code == 200
    assert "245.50" in r.json()["reply"]


def test_chat_rejects_empty_message():
    r = client.post("/api/v1/chat", json={"message": ""}, headers=auth())
    assert r.status_code == 422


def sse_events(response):
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]


def test_stream_requires_auth():
    assert client.post("/api/v1/chat/stream", json={"message": "hi"}).status_code == 401


def test_stream_direct_reply_ends_with_done(monkeypatch):
    _direct(monkeypatch, "Your premium is 245.50.")
    r = client.post("/api/v1/chat/stream", json={"message": "What is my premium?"}, headers=auth())
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    events = sse_events(r)
    assert events[-1]["done"] is True
    assert "245.50" in events[-1]["reply"]
    assert "".join(e.get("delta", "") for e in events) == events[-1]["reply"]


def test_stream_sends_model_tokens_as_they_arrive(monkeypatch):
    from langchain_core.messages import AIMessageChunk

    class FakeModel:
        def bind_tools(self, tools):
            return self

        async def astream(self, messages):
            for piece in ["<thi", "nk>hmm</think>Hel", "lo ", "there"]:
                yield AIMessageChunk(content=piece)

    async def policy(state):
        return "policy", ""

    monkeypatch.setattr(graph.llm, "enabled", lambda: True)
    monkeypatch.setattr(graph.llm, "get_model", lambda: FakeModel())
    monkeypatch.setattr(graph, "_llm_route", policy)
    r = client.post("/api/v1/chat/stream", json={"message": "What does P10 say about late payment?"}, headers=auth())
    events = sse_events(r)
    deltas = [e["delta"] for e in events if "delta" in e]
    assert len(deltas) > 1
    assert "".join(deltas) == "Hello there"
    assert events[-1] == {**events[-1], "done": True, "reply": "Hello there"}


def test_think_filter_handles_split_tags():
    f = graph._ThinkFilter()
    out = "".join(f.feed(p) for p in ["a<", "think>x", "y</thi", "nk>b"])
    assert out == "ab"
