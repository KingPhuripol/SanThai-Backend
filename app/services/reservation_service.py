"""
Time-limited reservation system (lazy expiry — no cron/scheduler available).

When an order is created, stock is decremented immediately and the order is
stamped with `reserved_until` (now + RESERVATION_MINUTES). Nothing runs in
the background to expire it. Instead, `release_expired_reservations` is
called at the top of every stock-sensitive read/write (product list/detail,
order creation, "my orders") to sweep and cancel any lapsed reservations for
the relevant scope first, restoring their stock — so stock numbers are always
correct by the time anyone actually looks at or acts on them.
"""
from datetime import datetime, timezone
from typing import Optional

RESERVATION_MINUTES = 30


def _restore_stock(sb, order: dict) -> None:
    qty = order.get("quantity", 1)
    variant_id = order.get("variant_id")

    if variant_id:
        v_res = sb.table("product_variants").select("stock_override").eq("id", variant_id).single().execute()
        v = v_res.data
        if v and v.get("stock_override") is not None:
            sb.table("product_variants").update({
                "stock_override": int(v["stock_override"]) + qty,
            }).eq("id", variant_id).execute()
            return

    p_res = sb.table("products").select("stock").eq("id", order["product_id"]).single().execute()
    p = p_res.data
    if p:
        sb.table("products").update({"stock": p["stock"] + qty}).eq("id", order["product_id"]).execute()


def release_expired_reservations(sb, product_id: Optional[int] = None) -> int:
    now_iso = datetime.now(timezone.utc).isoformat()
    query = (
        sb.table("orders")
        .select("id, product_id, variant_id, quantity")
        .eq("status", "pending")
        .lt("reserved_until", now_iso)
    )
    if product_id is not None:
        query = query.eq("product_id", product_id)

    expired = query.execute().data or []
    for o in expired:
        _restore_stock(sb, o)
        sb.table("orders").update({
            "status": "cancelled",
            "courier": "หมดเวลาชำระเงิน (ยกเลิกอัตโนมัติ)",
        }).eq("id", o["id"]).execute()

    return len(expired)
