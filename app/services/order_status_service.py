"""Order status transitions and immutable timeline events."""
from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException

ORDER_STATUSES = {
    "pending", "payment_pending_review", "paid", "processing", "preparing",
    "packed", "shipped", "delivered", "completed", "cancel_requested",
    "cancelled", "refund_requested", "refunded", "dispute_open",
    "dispute_resolved", "failed",
}

# Keep the legacy `processing` state valid while gradually adopting the more
# explicit `preparing` / `packed` states.
ALLOWED_TRANSITIONS = {
    "pending": {"payment_pending_review", "paid", "processing", "cancelled", "failed"},
    "payment_pending_review": {"paid", "processing", "cancelled", "failed"},
    "paid": {"processing", "preparing", "cancel_requested", "refund_requested", "dispute_open"},
    "processing": {"packed", "shipped", "cancel_requested", "refund_requested", "dispute_open"},
    "preparing": {"packed", "shipped", "cancel_requested", "refund_requested", "dispute_open"},
    "packed": {"shipped", "cancel_requested", "refund_requested", "dispute_open"},
    "shipped": {"delivered", "dispute_open"},
    "delivered": {"completed", "refund_requested", "dispute_open"},
    "completed": {"refund_requested", "dispute_open"},
    "cancel_requested": {"cancelled", "paid", "processing"},
    "refund_requested": {"refunded", "dispute_open", "completed"},
    "dispute_open": {"dispute_resolved", "refunded", "completed"},
    "dispute_resolved": {"refunded", "completed"},
    "cancelled": set(),
    "refunded": set(),
    "failed": {"pending", "cancelled"},
}


def update_order_status(
    sb,
    order_id: int,
    status: str,
    actor_type: str,
    actor_name: Optional[str] = None,
    tracking_number: Optional[str] = None,
    courier: Optional[str] = None,
    reason: Optional[str] = None,
) -> dict:
    """Validate a transition, update the order, and append its timeline event."""
    if status not in ORDER_STATUSES:
        raise HTTPException(status_code=422, detail="Invalid order status")

    current_res = sb.table("orders").select("id, status").eq("id", order_id).single().execute()
    if not current_res.data:
        raise HTTPException(status_code=404, detail="Order not found")
    current = current_res.data.get("status") or "pending"
    if status != current and status not in ALLOWED_TRANSITIONS.get(current, set()):
        raise HTTPException(status_code=409, detail=f"Order cannot move from {current} to {status}")

    now = datetime.now(timezone.utc).isoformat()
    update_data = {"status": status, "updated_at": now}
    if tracking_number:
        update_data["tracking_number"] = tracking_number
    if courier:
        update_data["courier"] = courier
    if status == "shipped":
        update_data["shipped_at"] = now
    if status == "delivered":
        update_data["delivered_at"] = now

    result = sb.table("orders").update(update_data).eq("id", order_id).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Order not found")

    if status != current or reason or tracking_number or courier:
        sb.table("order_status_events").insert({
            "order_id": order_id,
            "from_status": current,
            "to_status": status,
            "actor_type": actor_type,
            "actor_name": actor_name,
            "reason": reason,
            "created_at": now,
        }).execute()
    return result.data[0]
