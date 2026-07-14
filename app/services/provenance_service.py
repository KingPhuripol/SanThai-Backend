"""
Blockchain-simulation provenance service.
Each event is SHA-256 hashed and chained to the previous hash,
creating a tamper-evident audit trail stored in PostgreSQL.
"""
import hashlib
import json
from datetime import datetime, timezone, UTC
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.models import ProvenanceLog


def compute_hash(event_data: dict, prev_hash: Optional[str]) -> str:
    payload = {**event_data, "prev_hash": prev_hash or "GENESIS"}
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def add_provenance_event(
    db: AsyncSession,
    fabric_id: int,
    event_type: str,
    actor_name: str,
    location: Optional[str] = None,
    description_th: Optional[str] = None,
    description_en: Optional[str] = None,
    data: Optional[dict] = None,
) -> ProvenanceLog:
    # Fetch the last hash in the chain for this fabric
    result = await db.execute(
        select(ProvenanceLog)
        .where(ProvenanceLog.fabric_id == fabric_id)
        .order_by(ProvenanceLog.id.desc())
        .limit(1)
    )
    last_log = result.scalar_one_or_none()
    prev_hash = last_log.current_hash if last_log else None

    now = datetime.now(UTC).replace(tzinfo=None)  # naive UTC for DB compatibility
    event_data = {
        "fabric_id": fabric_id,
        "event_type": event_type,
        "actor_name": actor_name,
        "location": location,
        "timestamp": now.isoformat(),
    }
    if data:
        event_data.update(data)

    current_hash = compute_hash(event_data, prev_hash)

    log = ProvenanceLog(
        fabric_id=fabric_id,
        event_type=event_type,
        actor_name=actor_name,
        location=location,
        description_th=description_th,
        description_en=description_en,
        data=data,
        prev_hash=prev_hash,
        current_hash=current_hash,
        timestamp=now,
    )
    db.add(log)
    await db.commit()
    await db.refresh(log)
    return log


async def verify_chain(db: AsyncSession, fabric_id: int) -> dict:
    result = await db.execute(
        select(ProvenanceLog)
        .where(ProvenanceLog.fabric_id == fabric_id)
        .order_by(ProvenanceLog.id.asc())
    )
    logs = result.scalars().all()

    if not logs:
        return {"valid": True, "events": 0, "latest_hash": None}

    is_valid = True
    prev_hash = None

    for log in logs:
        event_data = {
            "fabric_id": log.fabric_id,
            "event_type": log.event_type,
            "actor_name": log.actor_name,
            "location": log.location,
            "timestamp": log.timestamp.isoformat() if log.timestamp else "",
        }
        if log.data:
            event_data.update(log.data)

        expected = compute_hash(event_data, prev_hash)
        if expected != log.current_hash:
            is_valid = False
            break
        prev_hash = log.current_hash

    return {
        "valid": is_valid,
        "events": len(logs),
        "latest_hash": logs[-1].current_hash,
    }
