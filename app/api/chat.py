"""
Chat API — rewritten to use Supabase REST client for chat history.
"""
import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.deps import rate_limit

from app.supabase_client import get_supabase
from app.services.chatbot_service import chat_with_supabase

router = APIRouter()


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=1000)
    session_id: str = ""


def _valid_session_id(value: str) -> str:
    # Only server-issued UUIDs, so a client can't pick (and read) a guessable session.
    try:
        return str(uuid.UUID(value))
    except ValueError:
        return str(uuid.uuid4())


@router.post("/", dependencies=[Depends(rate_limit(20, 60))])
async def chat_endpoint(request: ChatRequest):
    sb = get_supabase()
    session_id = _valid_session_id(request.session_id)
    reply = await chat_with_supabase(sb, session_id, request.message)
    return {"reply": reply, "session_id": session_id}
