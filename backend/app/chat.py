from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.agents.graph import run_agent
from app.auth import current_user

router = APIRouter(prefix="/api/v1/chat", tags=["chat"])


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    context: dict[str, Any] | None = None


class ChatResponse(BaseModel):
    reply: str
    sources: list[str] = []


@router.post("", response_model=ChatResponse)
async def chat(body: ChatRequest, username: str = Depends(current_user)):
    return ChatResponse(**await run_agent(username, body.message))