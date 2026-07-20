"""Small first-party traffic collector. It deliberately stores no IP address."""
from typing import Any, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.deps import get_optional_user
from app.supabase_client import get_supabase

router = APIRouter()


class TrafficEventInput(BaseModel):
    event_name: str = Field(min_length=1, max_length=60)
    path: Optional[str] = Field(default=None, max_length=500)
    product_id: Optional[int] = None
    anonymous_id: Optional[str] = Field(default=None, max_length=100)
    metadata: Optional[dict[str, Any]] = None


@router.post("/events")
async def record_event(input_data: TrafficEventInput, user: Optional[dict] = Depends(get_optional_user)):
    row = input_data.model_dump()
    row["user_id"] = user.get("sub") if user else None
    get_supabase().table("traffic_events").insert(row).execute()
    return {"ok": True}
