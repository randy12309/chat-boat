from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.agents import sessions
from app.agents.graph import run_agent
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


@router.get("/sessions")
def list_sessions(username: str = Depends(current_user)):
    return {"sessions": sessions.list_sessions(username)}


@router.get("/sessions/{session_id}")
def get_session(session_id: str, username: str = Depends(current_user)):
    if not sessions.valid_session_id(session_id):
        raise HTTPException(status_code=422, detail="Invalid session id")
    return {"session_id": session_id, "history": sessions.load(username, session_id)["history"]}


@router.delete("/sessions/{session_id}", status_code=204)
def delete_session(session_id: str, username: str = Depends(current_user)):
    if not sessions.valid_session_id(session_id) or not sessions.delete(username, session_id):
        raise HTTPException(status_code=404, detail="Session not found")
