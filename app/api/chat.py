"""
Chat API — rewritten to use Supabase REST client for chat history.
"""
import uuid

from fastapi import APIRouter
from pydantic import BaseModel

from app.supabase_client import get_supabase
from app.services.chatbot_service import chat_with_supabase

router = APIRouter()


class ChatRequest(BaseModel):
    message: str
    session_id: str = ""


@router.post("/")
async def chat_endpoint(request: ChatRequest):
    sb = get_supabase()
    session_id = request.session_id or str(uuid.uuid4())
    reply = await chat_with_supabase(sb, session_id, request.message)
    return {"reply": reply, "session_id": session_id}
