"""
Artisan API — rewritten to use Supabase REST client instead of SQLAlchemy.
"""
from datetime import datetime, timezone
from collections import defaultdict
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.deps import get_current_artisan_id
from app.services.order_status_service import update_order_status
from app.supabase_client import get_supabase

router = APIRouter()

MONTH_TH = ["", "ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.",
            "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."]


def _is_public_store(store: dict) -> bool:
    return bool(store.get("verified_at") and store.get("store_status", "approved") == "approved")


@router.get("/communities")
async def list_public_communities():
    """Community/map data derived from verified stores and their public listings."""
    sb = get_supabase()
    communities = sb.table("communities").select("id, name, province, region, latitude, longitude").execute().data or []
    stores = sb.table("artisans").select("id, name, community_id, bio_th, bio_en, avatar_url, verified_at, store_status").execute().data or []
    public_stores = [store for store in stores if _is_public_store(store)]
    products = sb.table("products").select("id, artisan_id, title_th, title_en, price_thb, images, stock, is_active, fabric_patterns(image_url)").eq("is_active", True).execute().data or []
    products_by_store: dict[int, list[dict]] = defaultdict(list)
    for product in products:
        products_by_store[product.get("artisan_id")].append(product)

    rows = []
    for community in communities:
        community_stores = [store for store in public_stores if store.get("community_id") == community["id"]]
        if not community_stores:
            continue
        store_rows = []
        for store in community_stores:
            store_products = products_by_store.get(store["id"], [])
            first_product = store_products[0] if store_products else {}
            fabric = first_product.get("fabric_patterns") or {}
            image = ((first_product.get("images") or [None])[0] or fabric.get("image_url") or store.get("avatar_url"))
            store_rows.append({
                "id": store["id"], "name": store.get("name"), "bio_th": store.get("bio_th"), "bio_en": store.get("bio_en"),
                "avatar_url": store.get("avatar_url"), "product_count": len(store_products),
                "image_url": image,
            })
        rows.append({**community, "store_count": len(store_rows), "product_count": sum(s["product_count"] for s in store_rows), "stores": store_rows})
    return rows


@router.get("/storefront/{artisan_id}")
async def get_public_storefront(artisan_id: int):
    sb = get_supabase()
    store_res = sb.table("artisans").select("*, communities(*)").eq("id", artisan_id).single().execute()
    store = store_res.data
    if not store or not _is_public_store(store):
        raise HTTPException(status_code=404, detail="Store not found")
    products = (
        sb.table("products")
        .select("id, title_th, title_en, price_thb, stock, sale_unit, images, is_active, fabric_patterns(name_th, name_en, image_url)")
        .eq("artisan_id", artisan_id).eq("is_active", True).order("id", desc=True).execute().data or []
    )
    return {
        "store": {"id": store["id"], "name": store.get("name"), "bio_th": store.get("bio_th"), "bio_en": store.get("bio_en"), "avatar_url": store.get("avatar_url"), "verified": True},
        "community": store.get("communities") or {},
        "products": [{
            "id": p["id"], "title_th": p.get("title_th"), "title_en": p.get("title_en"), "price_thb": p.get("price_thb"), "stock": p.get("stock"), "sale_unit": p.get("sale_unit"),
            "image_url": ((p.get("images") or [None])[0] or (p.get("fabric_patterns") or {}).get("image_url")),
            "fabric_name": (p.get("fabric_patterns") or {}).get("name_th"),
            "fabric_name_en": (p.get("fabric_patterns") or {}).get("name_en"),
        } for p in products],
    }


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
    reason: str = None

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

    artisan_res = sb.table("artisans").select("name").eq("id", artisan_id).single().execute()
    update_order_status(
        sb, order_id, input_data.status, "store",
        actor_name=(artisan_res.data or {}).get("name"),
        tracking_number=input_data.tracking_number,
        courier=input_data.courier,
        reason=input_data.reason,
    )
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
