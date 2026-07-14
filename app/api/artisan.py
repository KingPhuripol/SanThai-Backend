"""
Artisan API — rewritten to use Supabase REST client instead of SQLAlchemy.
"""
from datetime import datetime, timezone
from collections import defaultdict
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.deps import get_current_artisan_id
from app.supabase_client import get_supabase

router = APIRouter()

MONTH_TH = ["", "ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.",
            "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."]


@router.get("/dashboard/{artisan_id}")
async def get_dashboard(artisan_id: int, current_artisan_id: int = Depends(get_current_artisan_id)):
    if artisan_id != current_artisan_id:
        raise HTTPException(status_code=403, detail="Forbidden")

    sb = get_supabase()

    # Artisan + Community
    artisan_res = sb.table("artisans").select("*, communities(*)").eq("id", artisan_id).single().execute()
    if not artisan_res.data:
        raise HTTPException(status_code=404, detail="Artisan not found")
    artisan = artisan_res.data
    community = artisan.get("communities") or {}

    # All products by this artisan
    products_res = sb.table("products").select("id, price_thb, price_usd, is_active").eq("artisan_id", artisan_id).execute()
    products = products_res.data or []
    product_ids = [p["id"] for p in products]
    active_products = sum(1 for p in products if p.get("is_active"))

    # All orders for artisan's products
    orders = []
    if product_ids:
        orders_res = (
            sb.table("orders")
            .select("*, products(title_th, images, fabric_patterns(name_th, image_url)), product_variants(size, color)")
            .in_("product_id", product_ids)
            .order("created_at", desc=True)
            .limit(20)
            .execute()
        )
        orders = orders_res.data or []

    total_revenue = sum(o.get("total_thb", 0) for o in orders)
    total_orders = len(orders)

    # Revenue trend (last 6 months)
    monthly: dict = defaultdict(lambda: {"revenue_thb": 0.0, "orders": 0})
    for o in orders:
        created_at = o.get("created_at", "")
        if created_at:
            try:
                dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                key = (dt.year, dt.month)
                monthly[key]["revenue_thb"] += float(o.get("total_thb", 0))
                monthly[key]["orders"] += 1
            except Exception:
                pass

    now = datetime.now(timezone.utc)
    trend_data = []
    for months_back in range(5, -1, -1):
        m = now.month - months_back
        y = now.year
        while m <= 0:
            m += 12
            y -= 1
        data = monthly.get((y, m), {"revenue_thb": 0.0, "orders": 0})
        trend_data.append({"month": MONTH_TH[m], "revenue_thb": data["revenue_thb"], "orders": data["orders"]})

    # Fabrics
    fabrics_res = sb.table("fabric_patterns").select("*").eq("artisan_id", artisan_id).order("id", desc=True).limit(20).execute()
    fabrics = fabrics_res.data or []

    return {
        "artisan": {
            "id": artisan["id"],
            "name": artisan["name"],
            "avatar_url": artisan.get("avatar_url"),
        },
        "stats": {
            "artisan_name": artisan["name"],
            "community_name": community.get("name", ""),
            "province": community.get("province", ""),
            "total_revenue_thb": total_revenue,
            "total_revenue_usd": round(total_revenue / 35, 2),
            "total_orders": total_orders,
            "active_products": active_products,
            "registered_fabrics": len(fabrics),
            "community_share_thb": round(total_revenue * 0.30, 2),
        },
        "fabrics": [
            {
                "id": f["id"],
                "name_th": f["name_th"],
                "name_en": f.get("name_en"),
                "image_url": f.get("image_url"),
                "weave_technique": f.get("weave_technique"),
                "fiber_type": f.get("fiber_type"),
                "ai_processed": f.get("ai_processed"),
            }
            for f in fabrics
        ],
        "revenue_trend": trend_data,
        "recent_orders": [
            {
                "id": o["id"],
                "product_id": o.get("product_id"),
                "product_title": ((o.get("products") or {}).get("title_th") or "สินค้าไม่ระบุ"),
                "product_image": (((o.get("products") or {}).get("images") or [None])[0]
                                  or ((o.get("products") or {}).get("fabric_patterns") or {}).get("image_url")),
                "variant": o.get("product_variants"),
                "buyer_name": o.get("buyer_name"),
                "buyer_email": o.get("buyer_email"),
                "buyer_address": o.get("buyer_address"),
                "quantity": o.get("quantity", 1),
                "total_thb": o.get("total_thb", 0),
                "status": o.get("status", "pending"),
                "courier": o.get("courier"),
                "tracking_number": o.get("tracking_number"),
                "slip_url": o.get("slip_url"),
                "created_at": o.get("created_at"),
            }
            for o in orders
        ],
    }

class UpdateOrderStatusInput(BaseModel):
    status: str
    tracking_number: str = None
    courier: str = None

@router.put("/orders/{order_id}/status")
async def update_order_status(
    order_id: int,
    input_data: UpdateOrderStatusInput,
    artisan_id: int = Depends(get_current_artisan_id),
):
    sb = get_supabase()

    owner_res = (
        sb.table("orders")
        .select("id, products(artisan_id)")
        .eq("id", order_id)
        .single()
        .execute()
    )
    if not owner_res.data:
        raise HTTPException(status_code=404, detail="Order not found")
    if (owner_res.data.get("products") or {}).get("artisan_id") != artisan_id:
        raise HTTPException(status_code=403, detail="Forbidden")

    update_data = {"status": input_data.status}
    if input_data.tracking_number:
        update_data["tracking_number"] = input_data.tracking_number
    if input_data.courier:
        update_data["courier"] = input_data.courier
        
    res = sb.table("orders").update(update_data).eq("id", order_id).execute()
    
    if not res.data:
        raise HTTPException(status_code=404, detail="Order not found")
        
    return {"status": "success", "message": "Order updated"}


@router.get("/quiz/recommend")
async def quiz_recommend(
    occasion: str,
    personality: str,
    preferred_color: str,
    budget_thb: int,
    gender: str,
):
    sb = get_supabase()
    try:
        from app.services.recommendation_service import get_recommendations_supabase
        results = await get_recommendations_supabase(
            sb=sb,
            occasion=occasion,
            personality=personality,
            preferred_color=preferred_color,
            budget_thb=budget_thb,
            gender=gender,
        )
    except Exception:
        # Fallback: return fabrics within budget
        res = (
            sb.table("products")
            .select("*, fabric_patterns(*, communities(*))")
            .eq("is_active", True)
            .lte("price_thb", budget_thb)
            .limit(5)
            .execute()
        )
        results = [
            {
                "fabric_id": r.get("fabric_id"),
                "name_th": (r.get("fabric_patterns") or {}).get("name_th", ""),
                "name_en": (r.get("fabric_patterns") or {}).get("name_en"),
                "image_url": (r.get("fabric_patterns") or {}).get("image_url"),
                "price_thb": r.get("price_thb"),
                "product_id": r.get("id"),
                "reason": "ราคาอยู่ในงบประมาณของคุณ",
            }
            for r in (res.data or [])
        ]
    return {"recommendations": results}
