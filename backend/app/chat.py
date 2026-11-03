import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.agents import sessions
from app.agents.graph import run_agent, run_agent_stream
from app.auth import current_user

router = APIRouter(prefix="/api/v1/chat", tags=["chat"])


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    session_id: str = Field(default=sessions.DEFAULT_SESSION, pattern=r"^[A-Za-z0-9_-]{1,64}$")
    context: dict[str, Any] | None = None


class ChatResponse(BaseModel):
    reply: str
    sources: list[str] = []
    session_id: str = sessions.DEFAULT_SESSION


@router.post("", response_model=ChatResponse)
async def chat(body: ChatRequest, username: str = Depends(current_user)):
    return ChatResponse(**await run_agent(username, body.message, body.session_id))


@router.post("/stream")
async def chat_stream(body: ChatRequest, username: str = Depends(current_user)):
    """Server-sent events: {"delta": text} while the model writes, {"reset": true} to discard it, then {"done": true, ...}."""

    async def events():
        try:
            async for event in run_agent_stream(username, body.message, body.session_id):
                yield f"data: {json.dumps(event)}\n\n"
        except Exception:
            yield f'data: {json.dumps({"error": "Message failed to send."})}\n\n'

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/sessions")
def list_sessions(username: str = Depends(current_user)):
    return {"sessions": sessions.list_sessions(username)}


@router.get("/sessions/{session_id}")
def get_session(session_id: str, username: str = Depends(current_user)):
    if not sessions.valid_session_id(session_id):
        raise HTTPException(status_code=422, detail="Invalid session id")
    return {"session_id": session_id, "history": sessions.get_history(username, session_id)}


class RenameRequest(BaseModel):
    title: str = Field(min_length=1, max_length=120)


@router.patch("/sessions/{session_id}")
def rename_session(session_id: str, body: RenameRequest, username: str = Depends(current_user)):
    if not sessions.valid_session_id(session_id) or not sessions.rename(username, session_id, body.title):
        raise HTTPException(status_code=404, detail="Session not found")
    return {"session_id": session_id, "title": sessions.chat_store.make_title(body.title)}


@router.delete("/sessions/{session_id}", status_code=204)
def delete_session(session_id: str, username: str = Depends(current_user)):
    if not sessions.valid_session_id(session_id) or not sessions.delete(username, session_id):
        raise HTTPException(status_code=404, detail="Session not found")
