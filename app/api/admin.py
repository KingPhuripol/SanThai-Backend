"""
Admin API — platform-wide user management, order oversight, and stats.
Every endpoint requires `Depends(get_current_admin)`; there is no self-signup
path for the admin role (see auth.py's VALID_ROLES, which excludes "admin").
"""
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.deps import get_current_admin
from app.services.reservation_service import release_expired_reservations
from app.supabase_client import get_supabase

router = APIRouter()


@router.get("/users")
async def list_users(role: Optional[str] = None, admin: dict = Depends(get_current_admin)):
    sb = get_supabase()
    query = sb.table("users_profile").select("id, email, full_name, role, is_suspended, created_at")
    if role:
        query = query.eq("role", role)
    res = query.order("created_at", desc=True).execute()
    users = res.data or []

    # Attach artisan verification info where relevant
    artisan_res = sb.table("artisans").select("id, user_id, name, verified_at, community_id").execute()
    artisans_by_user = {a["user_id"]: a for a in (artisan_res.data or []) if a.get("user_id")}

    return [
        {
            "id": u["id"],
            "email": u["email"],
            "full_name": u["full_name"],
            "role": u["role"],
            "is_suspended": u.get("is_suspended", False),
            "created_at": u.get("created_at"),
            "artisan_id": artisans_by_user.get(u["id"], {}).get("id"),
            "verified_at": artisans_by_user.get(u["id"], {}).get("verified_at"),
        }
        for u in users
    ]


@router.put("/artisans/{artisan_id}/verify")
async def verify_artisan(artisan_id: int, admin: dict = Depends(get_current_admin)):
    sb = get_supabase()
    res = (
        sb.table("artisans")
        .update({"verified_at": datetime.now(timezone.utc).isoformat()})
        .eq("id", artisan_id)
        .execute()
    )
    if not res.data:
        raise HTTPException(status_code=404, detail="Artisan not found")
    return {"status": "success", "message": "Artisan verified"}


@router.put("/users/{user_id}/suspend")
async def suspend_user(user_id: str, admin: dict = Depends(get_current_admin)):
    sb = get_supabase()
    res = sb.table("users_profile").update({"is_suspended": True}).eq("id", user_id).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="User not found")
    return {"status": "success", "message": "User suspended"}


@router.put("/users/{user_id}/unsuspend")
async def unsuspend_user(user_id: str, admin: dict = Depends(get_current_admin)):
    sb = get_supabase()
    res = sb.table("users_profile").update({"is_suspended": False}).eq("id", user_id).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="User not found")
    return {"status": "success", "message": "User unsuspended"}


@router.get("/orders")
async def list_all_orders(admin: dict = Depends(get_current_admin)):
    sb = get_supabase()
    release_expired_reservations(sb)

    res = (
        sb.table("orders")
        .select("*, products(title_th, images, artisan_id, designer_id, fabric_patterns(name_th, image_url)), product_variants(size, color)")
        .order("created_at", desc=True)
        .limit(200)
        .execute()
    )
    orders = res.data or []

    return [
        {
            "id": o["id"],
            "product_id": o.get("product_id"),
            "product_title": ((o.get("products") or {}).get("title_th") or "สินค้าไม่ระบุ"),
            "product_image": (((o.get("products") or {}).get("images") or [None])[0]
                              or ((o.get("products") or {}).get("fabric_patterns") or {}).get("image_url")),
            "variant": o.get("product_variants"),
            "buyer_name": o.get("buyer_name"),
            "buyer_email": o.get("buyer_email"),
            "quantity": o.get("quantity", 1),
            "total_thb": o.get("total_thb", 0),
            "status": o.get("status", "pending"),
            "courier": o.get("courier"),
            "tracking_number": o.get("tracking_number"),
            "slip_url": o.get("slip_url"),
            "reserved_until": o.get("reserved_until"),
            "created_at": o.get("created_at"),
        }
        for o in orders
    ]


class UpdateOrderStatusInput(BaseModel):
    status: str
    tracking_number: Optional[str] = None
    courier: Optional[str] = None


@router.put("/orders/{order_id}/status")
async def update_order_status(
    order_id: int,
    input_data: UpdateOrderStatusInput,
    admin: dict = Depends(get_current_admin),
):
    sb = get_supabase()
    update_data = {"status": input_data.status}
    if input_data.tracking_number:
        update_data["tracking_number"] = input_data.tracking_number
    if input_data.courier:
        update_data["courier"] = input_data.courier

    res = sb.table("orders").update(update_data).eq("id", order_id).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Order not found")

    return {"status": "success", "message": "Order updated"}


@router.get("/stats")
async def get_platform_stats(admin: dict = Depends(get_current_admin)):
    sb = get_supabase()
    release_expired_reservations(sb)

    users_res = sb.table("users_profile").select("role").execute()
    users = users_res.data or []
    users_by_role = {"artisan": 0, "designer": 0, "customer": 0, "admin": 0}
    for u in users:
        r = u.get("role")
        if r in users_by_role:
            users_by_role[r] += 1

    orders_res = sb.table("orders").select("total_thb, status").execute()
    orders = orders_res.data or []
    active_orders = [o for o in orders if o.get("status") != "cancelled"]
    total_revenue = sum(o.get("total_thb", 0) for o in active_orders)

    products_res = sb.table("products").select("id", count="exact").execute()
    fabrics_res = sb.table("fabric_patterns").select("id", count="exact").execute()

    return {
        "total_revenue_thb": total_revenue,
        "total_revenue_usd": round(total_revenue / 35, 2),
        "total_community_share_thb": round(total_revenue * 0.30, 2),
        "total_orders": len(active_orders),
        "cancelled_orders": len(orders) - len(active_orders),
        "total_products": products_res.count or 0,
        "total_fabrics": fabrics_res.count or 0,
        "users_by_role": users_by_role,
        "total_users": sum(users_by_role.values()),
    }
