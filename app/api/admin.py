"""
Admin API — platform-wide user management, order oversight, and stats.
Every endpoint requires `Depends(get_current_admin)`; there is no self-signup
path for the admin role (see auth.py's VALID_ROLES, which excludes "admin").
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.deps import get_current_admin
from app.services.reservation_service import release_expired_reservations
from app.services.order_status_service import update_order_status
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
    artisan_res = sb.table("artisans").select("id, user_id, name, verified_at, store_status, store_reviewed_at, community_id").execute()
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
            "store_status": artisans_by_user.get(u["id"], {}).get("store_status"),
        }
        for u in users
    ]


@router.put("/artisans/{artisan_id}/verify")
async def verify_artisan(artisan_id: int, admin: dict = Depends(get_current_admin)):
    sb = get_supabase()
    res = (
        sb.table("artisans")
        .update({
            "verified_at": datetime.now(timezone.utc).isoformat(),
            "store_status": "approved",
            "store_reviewed_at": datetime.now(timezone.utc).isoformat(),
        })
        .eq("id", artisan_id)
        .execute()
    )
    if not res.data:
        raise HTTPException(status_code=404, detail="Artisan not found")
    return {"status": "success", "message": "Store verified and activated"}


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
    reason: Optional[str] = None


class PartnerContactInput(BaseModel):
    partner_name: str
    organization: Optional[str] = None
    partner_type: Optional[str] = None
    contact_name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    status: str = "to_contact"
    contacted_at: Optional[str] = None
    contact_result: Optional[str] = None
    owner_name: Optional[str] = None
    next_action: Optional[str] = None
    next_action_at: Optional[str] = None
    notes: Optional[str] = None


VALID_PARTNER_STATUSES = {"contacted", "in_progress", "to_contact", "won", "not_interested"}


def _validate_partner_input(data: PartnerContactInput) -> dict:
    if data.status not in VALID_PARTNER_STATUSES:
        raise HTTPException(status_code=400, detail="Invalid partner status")
    return data.model_dump()


@router.get("/partners")
async def list_partners(status: Optional[str] = None, admin: dict = Depends(get_current_admin)):
    sb = get_supabase()
    query = sb.table("partner_contacts").select("*").order("next_action_at").order("created_at", desc=True)
    if status:
        if status not in VALID_PARTNER_STATUSES:
            raise HTTPException(status_code=400, detail="Invalid partner status")
        query = query.eq("status", status)
    return query.execute().data or []


@router.post("/partners")
async def create_partner(input_data: PartnerContactInput, admin: dict = Depends(get_current_admin)):
    sb = get_supabase()
    row = _validate_partner_input(input_data)
    row["created_by"] = admin["sub"]
    res = sb.table("partner_contacts").insert(row).execute()
    return res.data[0]


@router.put("/partners/{partner_id}")
async def update_partner(partner_id: int, input_data: PartnerContactInput, admin: dict = Depends(get_current_admin)):
    sb = get_supabase()
    row = _validate_partner_input(input_data)
    row["updated_at"] = datetime.now(timezone.utc).isoformat()
    res = sb.table("partner_contacts").update(row).eq("id", partner_id).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Partner not found")
    return res.data[0]


@router.delete("/partners/{partner_id}")
async def delete_partner(partner_id: int, admin: dict = Depends(get_current_admin)):
    sb = get_supabase()
    res = sb.table("partner_contacts").delete().eq("id", partner_id).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Partner not found")
    return {"id": partner_id, "message": "Partner deleted"}


@router.get("/reports/financial")
async def financial_report(days: int = 30, admin: dict = Depends(get_current_admin)):
    if days < 1 or days > 365:
        raise HTTPException(status_code=400, detail="days must be between 1 and 365")
    sb = get_supabase()
    start = (datetime.now(timezone.utc) - timedelta(days=days - 1)).date().isoformat()
    orders = sb.table("orders").select("id, subtotal_thb, shipping_thb, total_thb, status, paid_at, created_at").gte("created_at", start).execute().data or []
    paid_statuses = {"processing", "shipped", "completed"}
    recognized = [o for o in orders if o.get("status") in paid_statuses]
    pending = [o for o in orders if o.get("status") == "pending"]
    cancelled = [o for o in orders if o.get("status") == "cancelled"]
    gross_sales = sum(float(o.get("subtotal_thb") if o.get("subtotal_thb") is not None else o.get("total_thb") or 0) for o in recognized)
    shipping_income = sum(float(o.get("shipping_thb") or 0) for o in recognized)
    trend: dict[str, dict] = {}
    for o in recognized:
        day = (o.get("paid_at") or o.get("created_at") or "")[:10]
        if not day:
            continue
        record = trend.setdefault(day, {"date": day, "sales_thb": 0.0, "shipping_thb": 0.0, "orders": 0})
        record["sales_thb"] += float(o.get("subtotal_thb") if o.get("subtotal_thb") is not None else o.get("total_thb") or 0)
        record["shipping_thb"] += float(o.get("shipping_thb") or 0)
        record["orders"] += 1
    return {
        "period_days": days,
        "gross_sales_thb": gross_sales,
        "shipping_income_thb": shipping_income,
        "recognized_revenue_thb": gross_sales + shipping_income,
        "paid_orders": len(recognized),
        "pending_orders": len(pending),
        "cancelled_orders": len(cancelled),
        "daily": [trend[key] for key in sorted(trend)],
    }


@router.get("/reports/traffic")
async def traffic_report(days: int = 30, admin: dict = Depends(get_current_admin)):
    if days < 1 or days > 365:
        raise HTTPException(status_code=400, detail="days must be between 1 and 365")
    sb = get_supabase()
    start = (datetime.now(timezone.utc) - timedelta(days=days - 1)).isoformat()
    events = sb.table("traffic_events").select("event_name, path, anonymous_id, user_id, created_at").gte("created_at", start).execute().data or []
    event_counts: dict[str, int] = {}
    visitors = set()
    pages: dict[str, int] = {}
    for event in events:
        name = event.get("event_name") or "unknown"
        event_counts[name] = event_counts.get(name, 0) + 1
        visitor = event.get("user_id") or event.get("anonymous_id")
        if visitor:
            visitors.add(visitor)
        if name == "page_view" and event.get("path"):
            path = event["path"]
            pages[path] = pages.get(path, 0) + 1
    top_pages = [{"path": path, "views": views} for path, views in sorted(pages.items(), key=lambda item: item[1], reverse=True)[:10]]
    return {"period_days": days, "unique_visitors": len(visitors), "events": event_counts, "top_pages": top_pages}


@router.put("/orders/{order_id}/status")
async def update_order_status(
    order_id: int,
    input_data: UpdateOrderStatusInput,
    admin: dict = Depends(get_current_admin),
):
    sb = get_supabase()
    update_order_status(
        sb, order_id, input_data.status, "admin",
        actor_name=admin.get("full_name") or admin.get("email"),
        tracking_number=input_data.tracking_number,
        courier=input_data.courier,
        reason=input_data.reason,
    )

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
