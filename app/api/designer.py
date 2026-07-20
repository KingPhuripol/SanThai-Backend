"""
Designer API — mirrors artisan.py's shape, scoped by designer_id (which is
simply the designer's own users_profile.id UUID — there is no separate
`designers` table, unlike artisans).
"""
import uuid
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, Form, HTTPException
from pydantic import BaseModel

from app.api.deps import get_current_designer_id
from app.services.order_status_service import update_order_status
from app.config import settings
from app.supabase_client import get_supabase

router = APIRouter()


@router.get("/dashboard/{designer_id}")
async def get_dashboard(designer_id: str, current_designer_id: str = Depends(get_current_designer_id)):
    if designer_id != current_designer_id:
        raise HTTPException(status_code=403, detail="Forbidden")

    sb = get_supabase()

    products_res = (
        sb.table("products")
        .select("id, title_th, price_thb, price_usd, stock, category, images, is_active")
        .eq("designer_id", designer_id)
        .order("id", desc=True)
        .execute()
    )
    products = products_res.data or []
    product_ids = [p["id"] for p in products]
    active_products = sum(1 for p in products if p.get("is_active"))

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

    total_revenue = sum(o.get("total_thb", 0) for o in orders if o.get("status") != "cancelled")

    return {
        "stats": {
            "total_revenue_thb": total_revenue,
            "total_revenue_usd": round(total_revenue / 35, 2),
            "total_orders": len(orders),
            "active_products": active_products,
            "total_products": len(products),
        },
        "products": products,
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


@router.post("/generate-fashion-image")
async def generate_fashion_image(
    fabric_id: int = Form(...),
    garment_style: str = Form(...),
    designer_id: str = Depends(get_current_designer_id),
):
    from app.services.fashion_generation_service import generate_fashion_image as gen_image

    sb = get_supabase()
    fabric_res = sb.table("fabric_patterns").select("id, name_th, image_url").eq("id", fabric_id).single().execute()
    if not fabric_res.data:
        raise HTTPException(status_code=404, detail="Fabric not found")
    fabric = fabric_res.data
    if not fabric.get("image_url"):
        raise HTTPException(status_code=400, detail="This fabric has no reference photo to generate from")

    async with httpx.AsyncClient(timeout=30.0) as http_client:
        fabric_photo_res = await http_client.get(fabric["image_url"])
        fabric_photo_res.raise_for_status()
        fabric_image_bytes = fabric_photo_res.content

    try:
        generated_bytes = await gen_image(fabric_image_bytes, garment_style)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"AI image generation failed: {e}")

    fname = f"aigen_{uuid.uuid4().hex[:8]}.png"
    upload_url = f"{settings.supabase_url}/storage/v1/object/santhai/{fname}"
    headers = {
        "Authorization": f"Bearer {settings.supabase_secret_key}",
        "apikey": settings.supabase_secret_key,
        "Content-Type": "image/png",
    }
    async with httpx.AsyncClient() as http_client:
        upload_res = await http_client.post(upload_url, headers=headers, content=generated_bytes)
        if upload_res.status_code != 200:
            raise HTTPException(status_code=502, detail="Failed to save generated image")

    image_url = f"{settings.supabase_url}/storage/v1/object/public/santhai/{fname}"
    return {"image_url": image_url}


class UpdateOrderStatusInput(BaseModel):
    status: str
    tracking_number: Optional[str] = None
    courier: Optional[str] = None
    reason: Optional[str] = None


@router.put("/orders/{order_id}/status")
async def update_order_status(
    order_id: int,
    input_data: UpdateOrderStatusInput,
    designer_id: str = Depends(get_current_designer_id),
):
    sb = get_supabase()

    owner_res = (
        sb.table("orders")
        .select("id, products(designer_id)")
        .eq("id", order_id)
        .single()
        .execute()
    )
    if not owner_res.data:
        raise HTTPException(status_code=404, detail="Order not found")
    if (owner_res.data.get("products") or {}).get("designer_id") != designer_id:
        raise HTTPException(status_code=403, detail="Forbidden")

    update_order_status(
        sb, order_id, input_data.status, "designer",
        actor_name=designer_id,
        tracking_number=input_data.tracking_number,
        courier=input_data.courier,
        reason=input_data.reason,
    )

    return {"status": "success", "message": "Order updated"}
