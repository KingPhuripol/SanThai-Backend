"""
Products API — rewritten to use Supabase REST client instead of SQLAlchemy.
"""
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional, List
from pydantic import BaseModel
from promptpay import qrcode as promptpay_qrcode
from fastapi import APIRouter, Depends, HTTPException, File, Form, UploadFile

from app.api.deps import get_current_user_id, get_optional_user
from app.services.reservation_service import RESERVATION_MINUTES, release_expired_reservations
from app.supabase_client import get_supabase

router = APIRouter()


def _build_variant_row(v: dict) -> dict:
    return {
        "id": v.get("id"),
        "product_id": v.get("product_id"),
        "size": v.get("size"),
        "color": v.get("color"),
        "additional_price_thb": float(v.get("additional_price_thb") or 0),
        "stock_override": v.get("stock_override"),
    }


def _build_product_row(p: dict) -> dict:
    fabric = p.get("fabric_patterns") or {}
    artisan = p.get("artisans") or {}
    community = (fabric.get("communities") or {}) if isinstance(fabric, dict) else {}
    return {
        "id": p["id"],
        "title_th": p["title_th"],
        "title_en": p.get("title_en"),
        "price_thb": p["price_thb"],
        "price_usd": p["price_usd"],
        "stock": p["stock"],
        "category": p.get("category"),
        "images": p.get("images") or ([fabric.get("image_url")] if fabric.get("image_url") else []),
        "variants": [_build_variant_row(v) for v in (p.get("product_variants") or [])],
        "fabric": {
            "id": fabric.get("id"),
            "name_th": fabric.get("name_th"),
            "name_en": fabric.get("name_en"),
            "image_url": fabric.get("image_url"),
            "weave_technique": fabric.get("weave_technique"),
            "usage_rights": fabric.get("usage_rights"),
        },
        "artisan": {"id": artisan.get("id"), "name": artisan.get("name")},
        "community": {"name": community.get("name"), "province": community.get("province")},
    }


@router.get("/")
async def list_products(
    province: Optional[str] = None,
    weave_technique: Optional[str] = None,
    max_price_thb: Optional[float] = None,
    artisan_id: Optional[int] = None,
    skip: int = 0,
    limit: int = 20,
):
    sb = get_supabase()
    release_expired_reservations(sb)
    query = (
        sb.table("products")
        .select("*, product_variants(*), fabric_patterns(*, communities(*)), artisans(id, name)")
        .eq("is_active", True)
        .range(skip, skip + limit - 1)
    )
    if max_price_thb:
        query = query.lte("price_thb", max_price_thb)
    if artisan_id:
        query = query.eq("artisan_id", artisan_id)

    res = query.execute()
    rows = res.data or []

    # Filter by province / weave_technique (PostgREST nested filter)
    if province:
        rows = [r for r in rows
                if (r.get("fabric_patterns") or {}).get("communities", {}) and
                province.lower() in ((r["fabric_patterns"]["communities"] or {}).get("province", "")).lower()]
    if weave_technique:
        rows = [r for r in rows
                if weave_technique.lower() in ((r.get("fabric_patterns") or {}).get("weave_technique", "") or "").lower()]

    return [_build_product_row(p) for p in rows]


@router.get("/{product_id}")
async def get_product(product_id: int):
    sb = get_supabase()
    release_expired_reservations(sb, product_id=product_id)
    res = (
        sb.table("products")
        .select("*, product_variants(*), fabric_patterns(*, communities(*)), artisans(*)")
        .eq("id", product_id)
        .single()
        .execute()
    )
    if not res.data:
        raise HTTPException(status_code=404, detail="Product not found")

    p = res.data
    fabric = p.get("fabric_patterns") or {}
    artisan = p.get("artisans") or {}
    community = fabric.get("communities") or {}

    return {
        "id": p["id"],
        "title_th": p["title_th"],
        "title_en": p.get("title_en"),
        "description_th": p.get("description_th"),
        "description_en": p.get("description_en"),
        "price_thb": p["price_thb"],
        "price_usd": p["price_usd"],
        "stock": p["stock"],
        "category": p.get("category"),
        "images": p.get("images") or ([fabric.get("image_url")] if fabric.get("image_url") else []),
        "variants": [_build_variant_row(v) for v in (p.get("product_variants") or [])],
        "fabric_id": fabric.get("id"),
        "fabric": {
            "id": fabric.get("id"),
            "name_th": fabric.get("name_th"),
            "name_en": fabric.get("name_en"),
            "image_url": fabric.get("image_url"),
            "weave_technique": fabric.get("weave_technique"),
            "dye_method": fabric.get("dye_method"),
            "cultural_meaning_th": fabric.get("cultural_meaning_th"),
            "cultural_meaning_en": fabric.get("cultural_meaning_en"),
            "usage_rights": fabric.get("usage_rights"),
            "story_tags": fabric.get("story_tags"),
        },
        "artisan": {
            "id": artisan.get("id"),
            "name": artisan.get("name"),
            "bio_th": artisan.get("bio_th"),
            "bio_en": artisan.get("bio_en"),
            "avatar_url": artisan.get("avatar_url"),
        },
        "community": {
            "name": community.get("name"),
            "province": community.get("province"),
            "region": community.get("region"),
            "latitude": community.get("latitude"),
            "longitude": community.get("longitude"),
        },
    }


@router.post("/orders")
async def create_order(
    product_id: int,
    buyer_email: str,
    quantity: int = 1,
    buyer_name: Optional[str] = None,
    buyer_address: Optional[str] = None,
    variant_id: Optional[int] = None,
    user_id: str = Depends(get_current_user_id),
):
    sb = get_supabase()
    release_expired_reservations(sb, product_id=product_id)

    # Get product
    res = sb.table("products").select("*").eq("id", product_id).single().execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Product not found")
    product = res.data

    variant = None
    unit_price_thb = float(product["price_thb"])
    unit_price_usd = float(product["price_usd"])
    available_stock = int(product["stock"])

    if variant_id:
        variant_res = (
            sb.table("product_variants")
            .select("*")
            .eq("id", variant_id)
            .eq("product_id", product_id)
            .single()
            .execute()
        )
        if not variant_res.data:
            raise HTTPException(status_code=404, detail="Product variant not found")
        variant = variant_res.data
        unit_price_thb += float(variant.get("additional_price_thb") or 0)
        unit_price_usd = round(unit_price_thb / 35, 2)
        if variant.get("stock_override") is not None:
            available_stock = int(variant["stock_override"])

    if available_stock < quantity:
        raise HTTPException(status_code=400, detail="Insufficient stock")

    total_thb = unit_price_thb * quantity
    total_usd = unit_price_usd * quantity
    reserved_until = (datetime.now(timezone.utc) + timedelta(minutes=RESERVATION_MINUTES)).isoformat()

    # Create order
    order_res = sb.table("orders").insert({
        "product_id": product_id,
        "variant_id": variant_id,
        "buyer_id": user_id,
        "buyer_email": buyer_email,
        "buyer_name": buyer_name,
        "buyer_address": buyer_address,
        "quantity": quantity,
        "total_thb": total_thb,
        "total_usd": total_usd,
        "status": "pending",
        "courier": "รอผู้ผลิตยืนยัน",
        "reserved_until": reserved_until,
    }).execute()
    order = order_res.data[0]

    # Decrement stock at the most specific level available.
    if variant and variant.get("stock_override") is not None:
        sb.table("product_variants").update({
            "stock_override": int(variant["stock_override"]) - quantity,
        }).eq("id", variant_id).execute()
    else:
        sb.table("products").update({"stock": product["stock"] - quantity}).eq("id", product_id).execute()

    # Generate PromptPay QR payload
    promptpay_id = os.getenv("PROMPTPAY_ID", "0812345678")
    payload = promptpay_qrcode.generate_payload(promptpay_id, total_thb)

    return {
        "id": order["id"],
        "status": "pending",
        "total_thb": total_thb,
        "total_usd": total_usd,
        "variant": _build_variant_row(variant) if variant else None,
        "shipping": {
            "status": "pending",
            "courier": "รอผู้ผลิตยืนยัน",
            "tracking_number": None,
        },
        "promptpay_payload": payload,
        "reserved_until": reserved_until,
        "message": "Order placed. Proceed to payment.",
    }


@router.get("/orders/my")
async def get_my_orders(user_id: str = Depends(get_current_user_id)):
    sb = get_supabase()
    release_expired_reservations(sb)

    res = (
        sb.table("orders")
        .select("*, products(title_th, images, fabric_patterns(image_url)), product_variants(size, color)")
        .eq("buyer_id", user_id)
        .order("created_at", desc=True)
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
            "quantity": o.get("quantity", 1),
            "total_thb": o.get("total_thb", 0),
            "status": o.get("status", "pending"),
            "reserved_until": o.get("reserved_until"),
            "slip_url": o.get("slip_url"),
            "tracking_number": o.get("tracking_number"),
            "courier": o.get("courier"),
            "created_at": o.get("created_at"),
        }
        for o in orders
    ]


@router.post("/upload")
async def upload_product(
    fabric_id: int = Form(...),
    title_th: str = Form(...),
    title_en: Optional[str] = Form(None),
    description_th: Optional[str] = Form(None),
    price_thb: float = Form(...),
    category: str = Form("shirt"),
    stock: int = Form(1),
    image: Optional[UploadFile] = File(None),
    image_url: Optional[str] = Form(None),
    current_user: Optional[dict] = Depends(get_optional_user),
):
    sb = get_supabase()

    # Verify fabric exists
    fabric_res = sb.table("fabric_patterns").select("*").eq("id", fabric_id).single().execute()
    if not fabric_res.data:
        raise HTTPException(status_code=404, detail="Fabric not found")
    fabric = fabric_res.data
    artisan_id = fabric.get("artisan_id")

    # designer_id is server-derived only, never client-supplied — a request
    # with no token (or a non-designer token, e.g. an artisan auto-listing
    # their own fabric) simply results in no designer being credited.
    valid_designer_uuid = None
    if current_user and current_user.get("role") == "designer":
        valid_designer_uuid = current_user.get("sub")

    final_image_url = image_url
    if image and image.filename:
        file_bytes = await image.read()
        content_type = image.content_type or "image/jpeg"
        
        # Upload to Supabase Storage
        import uuid
        import httpx
        from app.config import settings
        ext = image.filename.rsplit(".", 1)[-1] if "." in image.filename else "jpg"
        fname = f"product_{uuid.uuid4().hex[:8]}.{ext}"
        
        url = f"{settings.supabase_url}/storage/v1/object/santhai/{fname}"
        headers = {
            "Authorization": f"Bearer {settings.supabase_secret_key}",
            "apikey": settings.supabase_secret_key,
            "Content-Type": content_type,
        }
        async with httpx.AsyncClient() as client:
            res = await client.post(url, headers=headers, content=file_bytes)
            if res.status_code == 200:
                final_image_url = f"{settings.supabase_url}/storage/v1/object/public/santhai/{fname}"
            else:
                pass # fallback to original image_url if failed

    images = [final_image_url] if final_image_url else []
    price_usd = round(price_thb / 35, 2)

    product_res = sb.table("products").insert({
        "fabric_id": fabric_id,
        "designer_id": valid_designer_uuid,
        "artisan_id": artisan_id,
        "title_th": title_th,
        "title_en": title_en,
        "description_th": description_th,
        "price_thb": price_thb,
        "price_usd": price_usd,
        "stock": stock,
        "category": category,
        "images": images,
        "is_active": True
    }).execute()

    if not product_res.data:
        raise HTTPException(status_code=500, detail="Failed to create product")

    return {"id": product_res.data[0]["id"], "title_th": title_th, "message": "Product uploaded successfully"}


@router.put("/{product_id}")
async def update_product(
    product_id: int,
    title_th: Optional[str] = Form(None),
    title_en: Optional[str] = Form(None),
    description_th: Optional[str] = Form(None),
    price_thb: Optional[float] = Form(None),
    stock: Optional[int] = Form(None),
    category: Optional[str] = Form(None),
    is_active: Optional[bool] = Form(None),
    image: Optional[UploadFile] = File(None),
    current_user: Optional[dict] = Depends(get_optional_user),
):
    sb = get_supabase()

    # Verify product exists and check ownership
    prod_res = sb.table("products").select("id, artisan_id, designer_id").eq("id", product_id).single().execute()
    if not prod_res.data:
        raise HTTPException(status_code=404, detail="Product not found")

    product = prod_res.data
    user_id = current_user.get("sub") if current_user else None
    is_owner = False
    if product.get("designer_id") and product["designer_id"] == user_id:
        is_owner = True
    if product.get("artisan_id") and current_user:
        # Check if user is this artisan
        artisan_res = sb.table("artisans").select("id").eq("user_id", user_id).single().execute()
        if artisan_res.data and artisan_res.data["id"] == product["artisan_id"]:
            is_owner = True

    if not is_owner:
        raise HTTPException(status_code=403, detail="Forbidden")

    update_data = {}
    if title_th is not None:
        update_data["title_th"] = title_th
    if title_en is not None:
        update_data["title_en"] = title_en
    if description_th is not None:
        update_data["description_th"] = description_th
    if price_thb is not None:
        update_data["price_thb"] = price_thb
        update_data["price_usd"] = round(price_thb / 35, 2)
    if stock is not None:
        update_data["stock"] = stock
    if category is not None:
        update_data["category"] = category
    if is_active is not None:
        update_data["is_active"] = is_active

    # Handle image upload
    if image and image.filename:
        file_bytes = await image.read()
        content_type = image.content_type or "image/jpeg"
        import httpx
        from app.config import settings
        ext = image.filename.rsplit(".", 1)[-1] if "." in image.filename else "jpg"
        fname = f"product_{uuid.uuid4().hex[:8]}.{ext}"
        url = f"{settings.supabase_url}/storage/v1/object/santhai/{fname}"
        headers = {
            "Authorization": f"Bearer {settings.supabase_secret_key}",
            "apikey": settings.supabase_secret_key,
            "Content-Type": content_type,
        }
        async with httpx.AsyncClient() as client:
            res = await client.post(url, headers=headers, content=file_bytes)
            if res.status_code == 200:
                new_url = f"{settings.supabase_url}/storage/v1/object/public/santhai/{fname}"
                update_data["images"] = [new_url]

    if not update_data:
        return {"message": "No changes"}

    sb.table("products").update(update_data).eq("id", product_id).execute()
    return {"id": product_id, "message": "Product updated successfully"}


@router.delete("/{product_id}")
async def delete_product(
    product_id: int,
    current_user: Optional[dict] = Depends(get_optional_user),
):
    sb = get_supabase()

    prod_res = sb.table("products").select("id, artisan_id, designer_id").eq("id", product_id).single().execute()
    if not prod_res.data:
        raise HTTPException(status_code=404, detail="Product not found")

    product = prod_res.data
    user_id = current_user.get("sub") if current_user else None
    is_owner = False
    if product.get("designer_id") and product["designer_id"] == user_id:
        is_owner = True
    if product.get("artisan_id") and current_user:
        artisan_res = sb.table("artisans").select("id").eq("user_id", user_id).single().execute()
        if artisan_res.data and artisan_res.data["id"] == product["artisan_id"]:
            is_owner = True

    if not is_owner:
        raise HTTPException(status_code=403, detail="Forbidden")

    # Soft delete — just mark as inactive
    sb.table("products").update({"is_active": False}).eq("id", product_id).execute()
    return {"id": product_id, "message": "Product deleted (deactivated) successfully"}


@router.post("/orders/{order_id}/slip")
async def upload_payment_slip(
    order_id: int,
    slip: UploadFile = File(...),
):
    sb = get_supabase()
    
    # Save the slip
    file_bytes = await slip.read()
    content_type = slip.content_type or "image/jpeg"
    # Upload to Supabase Storage
    import uuid
    import httpx
    from app.config import settings
    ext = slip.filename.rsplit(".", 1)[-1] if "." in slip.filename else "jpg"
    fname = f"slip_{order_id}_{uuid.uuid4().hex[:8]}.{ext}"
    
    url = f"{settings.supabase_url}/storage/v1/object/santhai/{fname}"
    headers = {
        "Authorization": f"Bearer {settings.supabase_secret_key}",
        "apikey": settings.supabase_secret_key,
        "Content-Type": content_type,
    }
    async with httpx.AsyncClient() as client:
        res = await client.post(url, headers=headers, content=file_bytes)
        if res.status_code == 200:
            final_url = f"{settings.supabase_url}/storage/v1/object/public/santhai/{fname}"
        else:
            raise HTTPException(status_code=500, detail="Failed to upload to Supabase Storage")

    # Update order
    res = sb.table("orders").update({
        "slip_url": final_url,
        "status": "processing"
    }).eq("id", order_id).execute()
    
    if not res.data:
        raise HTTPException(status_code=404, detail="Order not found")
        
    return {"status": "success", "slip_url": final_url}


class CartItemInput(BaseModel):
    product_id: int
    quantity: int
    variant_id: Optional[int] = None

class CheckoutCartInput(BaseModel):
    buyer_name: str
    buyer_email: str
    buyer_phone: Optional[str] = None
    buyer_address: str
    items: List[CartItemInput]

@router.post("/checkout_cart")
async def checkout_cart(input_data: CheckoutCartInput, user_id: str = Depends(get_current_user_id)):
    sb = get_supabase()
    import uuid

    batch_id = str(uuid.uuid4())
    total_thb = 0
    orders_created = []
    reserved_until = (datetime.now(timezone.utc) + timedelta(minutes=RESERVATION_MINUTES)).isoformat()

    for item in input_data.items:
        release_expired_reservations(sb, product_id=item.product_id)
        # Fetch product
        prod_res = sb.table("products").select("*").eq("id", item.product_id).single().execute()
        if not prod_res.data:
            continue
        product = prod_res.data
        
        # Calculate price
        price = product["price_thb"]
        
        variant = None
        if item.variant_id:
            var_res = sb.table("product_variants").select("*").eq("id", item.variant_id).single().execute()
            if var_res.data:
                variant = var_res.data
                price += variant.get("additional_price_thb", 0)
                
        line_total = price * item.quantity
        total_thb += line_total
        
        # Insert order
        order_res = sb.table("orders").insert({
            "product_id": item.product_id,
            "variant_id": item.variant_id,
            "quantity": item.quantity,
            "buyer_id": user_id,
            "buyer_name": input_data.buyer_name,
            "buyer_email": input_data.buyer_email,
            "buyer_phone": input_data.buyer_phone,
            "buyer_address": input_data.buyer_address,
            "total_thb": line_total,
            "total_usd": round(line_total / 35, 2),
            "status": "pending",
            "courier": "รอผู้ผลิตยืนยัน",
            "batch_id": batch_id,
            "reserved_until": reserved_until,
        }).execute()
        
        if order_res.data:
            orders_created.append(order_res.data[0]["id"])
            # Update stock
            if variant and variant.get("stock_override") is not None:
                sb.table("product_variants").update({
                    "stock_override": int(variant["stock_override"]) - item.quantity,
                }).eq("id", item.variant_id).execute()
            else:
                sb.table("products").update({"stock": product["stock"] - item.quantity}).eq("id", item.product_id).execute()

    if not orders_created:
        raise HTTPException(status_code=400, detail="Failed to create any orders")

    promptpay_id = os.getenv("PROMPTPAY_ID", "0812345678")
    payload = promptpay_qrcode.generate_payload(promptpay_id, total_thb)

    return {
        "batch_id": batch_id,
        "order_ids": orders_created,
        "total_thb": total_thb,
        "promptpay_payload": payload,
        "reserved_until": reserved_until,
        "message": "Cart checked out successfully"
    }
