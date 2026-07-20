"""
Products API — rewritten to use Supabase REST client instead of SQLAlchemy.
"""
import os
import uuid
import base64
from io import BytesIO
from datetime import datetime, timedelta, timezone
from typing import Optional, List
from pydantic import BaseModel
from promptpay import qrcode as promptpay_qrcode
from fastapi import APIRouter, Depends, HTTPException, File, Form, UploadFile

from app.api.deps import get_current_user_id, get_optional_user
from app.services.reservation_service import RESERVATION_MINUTES, release_expired_reservations
from app.services.order_status_service import update_order_status
from app.supabase_client import get_supabase

router = APIRouter()

PRODUCT_TYPES = {"ready_to_ship", "made_to_order", "pre_order"}
SALE_UNITS = {"meter", "piece", "roll", "set"}
STORE_TERMS_VERSION = "2026-07-18-store-v1"


def _listing_status(is_active: bool, product_type: str, stock: int) -> str:
    if not is_active:
        return "draft"
    if product_type == "ready_to_ship" and stock <= 0:
        return "out_of_stock"
    return "active"


def _is_verified_store(artisan: dict | None) -> bool:
    return bool(
        artisan
        and artisan.get("verified_at")
        and artisan.get("store_status", "approved") == "approved"
    )


def _assert_store_can_publish(sb, artisan_id: int | None) -> None:
    if not artisan_id:
        raise HTTPException(status_code=403, detail="A verified store is required to publish a product")
    artisan_res = (
        sb.table("artisans")
        .select("id, verified_at, store_status, store_terms_version, store_terms_accepted_at")
        .eq("id", artisan_id)
        .single()
        .execute()
    )
    if not _is_verified_store(artisan_res.data):
        raise HTTPException(
            status_code=403,
            detail="ร้านค้าต้องผ่านการตรวจสอบและอยู่ในสถานะเปิดใช้งานก่อนเผยแพร่สินค้า",
        )
    if artisan_res.data.get("store_terms_version") != STORE_TERMS_VERSION or not artisan_res.data.get("store_terms_accepted_at"):
        raise HTTPException(
            status_code=403,
            detail="ร้านค้าต้องยอมรับข้อกำหนดการใช้บริการฉบับร้านค้าก่อนเผยแพร่สินค้า",
        )


def _get_promptpay_id() -> str:
    promptpay_id = os.getenv("PROMPTPAY_ID")
    if not promptpay_id:
        raise HTTPException(status_code=503, detail="Payment is not configured")
    return promptpay_id


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
    passport = p.get("santhai_passports") or {}
    if isinstance(passport, list):
        passport = passport[0] if passport else {}
    return {
        "id": p["id"],
        "fabric_id": p.get("fabric_id"),
        "product_code": p.get("product_code"),
        "title_th": p["title_th"],
        "title_en": p.get("title_en"),
        "price_thb": p["price_thb"],
        "price_usd": p["price_usd"],
        "stock": p["stock"],
        "category": p.get("category"),
        "is_active": p.get("is_active", True),
        "product_type": p.get("product_type") or "ready_to_ship",
        "preparation_time": p.get("preparation_time"),
        "shipping_provider": p.get("shipping_provider"),
        "shipping_cost_thb": float(p.get("shipping_cost_thb") or 0),
        "free_shipping": bool(p.get("free_shipping")),
        "listing_status": p.get("listing_status") or _listing_status(
            bool(p.get("is_active", True)), p.get("product_type") or "ready_to_ship", int(p.get("stock") or 0)
        ),
        "sale_unit": p.get("sale_unit") or "piece",
        "width_cm": p.get("width_cm"),
        "length_cm": p.get("length_cm"),
        "weight_g": p.get("weight_g"),
        "fiber_composition": p.get("fiber_composition"),
        "primary_color": p.get("primary_color"),
        "dye_method": p.get("dye_method"),
        "pattern_name": p.get("pattern_name"),
        "texture": p.get("texture"),
        "production_method": p.get("production_method"),
        "production_origin": p.get("production_origin"),
        "care_instructions": p.get("care_instructions"),
        "available_at": p.get("available_at"),
        "published_at": p.get("published_at"),
        "passport": {
            "code": passport.get("passport_code"),
            "status": passport.get("status"),
            "issued_at": passport.get("issued_at"),
        } if passport else None,
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
        "artisan": {
            "id": artisan.get("id"),
            "name": artisan.get("name"),
            "verified": _is_verified_store(artisan),
        },
        "community": {"name": community.get("name"), "province": community.get("province")},
    }


@router.get("/")
async def list_products(
    province: Optional[str] = None,
    weave_technique: Optional[str] = None,
    max_price_thb: Optional[float] = None,
    artisan_id: Optional[int] = None,
    include_inactive: bool = False,
    skip: int = 0,
    limit: int = 20,
    current_user: Optional[dict] = Depends(get_optional_user),
):
    sb = get_supabase()
    release_expired_reservations(sb)
    if include_inactive:
        if not artisan_id or not current_user or current_user.get("role") != "artisan" or int(current_user.get("artisan_id") or 0) != artisan_id:
            raise HTTPException(status_code=403, detail="Artisan account required to view drafts")
    query = (
        sb.table("products")
        .select("*, product_variants(*), santhai_passports(*), fabric_patterns(*, communities(*)), artisans(id, name, verified_at, store_status)")
    )
    if not include_inactive:
        query = query.eq("is_active", True)
        # Store verification is evaluated after PostgREST returns nested
        # artisan data. Fetch the public catalog window first, filter stores,
        # then paginate; paginating before filtering can hide a valid store
        # when earlier rows belong to stores still under review.
        query = query.range(0, 499)
    else:
        query = query.range(skip, skip + limit - 1)
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

    # A public marketplace only exposes listings from verified, active stores.
    if not include_inactive:
        rows = [p for p in rows if _is_verified_store(p.get("artisans") or {})]
        rows = rows[skip:skip + limit]
    return [_build_product_row(p) for p in rows]


@router.get("/orders/my")
async def get_my_orders_early(user_id: str = Depends(get_current_user_id)):
    """Must be registered before /{product_id}, otherwise Starlette tries to
    parse the word 'orders' as an integer product id."""
    sb = get_supabase()
    release_expired_reservations(sb)
    res = (
        sb.table("orders")
        .select("*, products(title_th, images, fabric_patterns(image_url)), product_variants(size, color), order_status_events(*)")
        .eq("buyer_id", user_id)
        .order("created_at", desc=True)
        .execute()
    )
    return [
        {
            "id": o["id"], "product_id": o.get("product_id"),
            "product_title": ((o.get("products") or {}).get("title_th") or "สินค้าไม่ระบุ"),
            "product_image": (((o.get("products") or {}).get("images") or [None])[0] or ((o.get("products") or {}).get("fabric_patterns") or {}).get("image_url")),
            "variant": o.get("product_variants"), "quantity": o.get("quantity", 1),
            "total_thb": o.get("total_thb", 0), "status": o.get("status", "pending"),
            "reserved_until": o.get("reserved_until"), "slip_url": o.get("slip_url"),
            "tracking_number": o.get("tracking_number"), "courier": o.get("courier"), "created_at": o.get("created_at"),
            "updated_at": o.get("updated_at"), "events": o.get("order_status_events") or [],
        }
        for o in (res.data or [])
    ]


@router.get("/passports/{passport_code}")
async def get_santhai_passport(passport_code: str):
    """Public, privacy-safe Passport view for a product or production lot."""
    sb = get_supabase()
    res = (
        sb.table("santhai_passports")
        .select("*, products(id, product_code, title_th, title_en, description_th, description_en, images, primary_color, dye_method, pattern_name, texture, production_method, production_origin, care_instructions, fabric_patterns(name_th, name_en, weave_technique, fiber_type), artisans(name, bio_en, verified_at, store_status)), santhai_passport_events(*)")
        .eq("passport_code", passport_code)
        .single()
        .execute()
    )
    if not res.data or res.data.get("status") == "revoked":
        raise HTTPException(status_code=404, detail="Passport not found or no longer active")
    passport = res.data
    product = passport.get("products") or {}
    store = product.get("artisans") or {}
    if not _is_verified_store(store):
        raise HTTPException(status_code=404, detail="Passport not found")
    fabric = product.get("fabric_patterns") or {}
    events = sorted(passport.get("santhai_passport_events") or [], key=lambda item: item.get("created_at") or "")
    return {
        "code": passport.get("passport_code"),
        "status": passport.get("status"),
        "issued_at": passport.get("issued_at"),
        "verified_at": passport.get("verified_at"),
        "public_note": passport.get("public_note"),
        "product": {
            "id": product.get("id"),
            "code": product.get("product_code"),
            "title_th": product.get("title_th"),
            "title_en": product.get("title_en"),
            "image": (product.get("images") or [None])[0],
            "color": product.get("primary_color"),
            "dye_method": product.get("dye_method"),
            "pattern_name": product.get("pattern_name"),
            "texture": product.get("texture"),
            "production_method": product.get("production_method"),
            "production_origin": product.get("production_origin"),
            "care_instructions": product.get("care_instructions"),
        },
        "fabric": {
            "name_th": fabric.get("name_th"),
            "weave_technique": fabric.get("weave_technique"),
            "fiber_type": fabric.get("fiber_type"),
        },
        "store": {"name": store.get("name"), "verified": _is_verified_store(store)},
        "events": [
            {
                "id": event.get("id"), "type": event.get("event_type"),
                "actor_name": event.get("actor_name"), "location": event.get("location_label"),
                "description_th": event.get("description_th"), "created_at": event.get("created_at"),
            }
            for event in events
        ],
    }


@router.get("/passports/{passport_code}/qr")
async def get_santhai_passport_qr(passport_code: str):
    """A portable QR data URI for print labels and the public Passport page."""
    import qrcode
    from app.config import settings

    await get_santhai_passport(passport_code)

    public_url = f"{settings.app_url.rstrip('/')}/passport/{passport_code}"
    image = qrcode.make(public_url)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return {
        "passport_code": passport_code,
        "url": public_url,
        "image_data_url": "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii"),
    }


@router.get("/{product_id}")
async def get_product(product_id: int, current_user: Optional[dict] = Depends(get_optional_user)):
    sb = get_supabase()
    release_expired_reservations(sb, product_id=product_id)
    res = (
        sb.table("products")
        .select("*, product_variants(*), santhai_passports(*), fabric_patterns(*, communities(*)), artisans(*)")
        .eq("id", product_id)
        .single()
        .execute()
    )
    if not res.data:
        raise HTTPException(status_code=404, detail="Product not found")

    p = res.data
    fabric = p.get("fabric_patterns") or {}
    artisan = p.get("artisans") or {}
    is_owner = bool(current_user and (
        p.get("designer_id") == current_user.get("sub") or artisan.get("user_id") == current_user.get("sub")
    ))
    if (not p.get("is_active", True) or not _is_verified_store(artisan)) and not is_owner:
        raise HTTPException(status_code=404, detail="Product not found")
    community = fabric.get("communities") or {}
    passport = p.get("santhai_passports") or {}
    if isinstance(passport, list):
        passport = passport[0] if passport else {}

    return {
        "id": p["id"],
        "product_code": p.get("product_code"),
        "title_th": p["title_th"],
        "title_en": p.get("title_en"),
        "description_th": p.get("description_th"),
        "description_en": p.get("description_en"),
        "price_thb": p["price_thb"],
        "price_usd": p["price_usd"],
        "stock": p["stock"],
        "category": p.get("category"),
        "product_type": p.get("product_type") or "ready_to_ship",
        "preparation_time": p.get("preparation_time"),
        "shipping_provider": p.get("shipping_provider"),
        "shipping_cost_thb": float(p.get("shipping_cost_thb") or 0),
        "free_shipping": bool(p.get("free_shipping")),
        "listing_status": p.get("listing_status") or _listing_status(
            bool(p.get("is_active", True)), p.get("product_type") or "ready_to_ship", int(p.get("stock") or 0)
        ),
        "sale_unit": p.get("sale_unit") or "piece",
        "width_cm": p.get("width_cm"),
        "length_cm": p.get("length_cm"),
        "weight_g": p.get("weight_g"),
        "fiber_composition": p.get("fiber_composition"),
        "primary_color": p.get("primary_color"),
        "dye_method": p.get("dye_method"),
        "pattern_name": p.get("pattern_name"),
        "texture": p.get("texture"),
        "production_method": p.get("production_method"),
        "production_origin": p.get("production_origin"),
        "care_instructions": p.get("care_instructions"),
        "available_at": p.get("available_at"),
        "published_at": p.get("published_at"),
        "passport": {
            "code": passport.get("passport_code"),
            "status": passport.get("status"),
            "issued_at": passport.get("issued_at"),
        } if passport else None,
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
            "verified": _is_verified_store(artisan),
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
    if quantity < 1:
        raise HTTPException(status_code=422, detail="Quantity must be at least 1")
    sb = get_supabase()
    release_expired_reservations(sb, product_id=product_id)

    # Get product
    res = sb.table("products").select("*").eq("id", product_id).single().execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Product not found")
    product = res.data
    if not product.get("is_active", True):
        raise HTTPException(status_code=404, detail="Product is not available")

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

    subtotal_thb = unit_price_thb * quantity
    shipping_thb = 0 if product.get("free_shipping") else float(product.get("shipping_cost_thb") or 0)
    total_thb = subtotal_thb + shipping_thb
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
        "subtotal_thb": subtotal_thb,
        "shipping_thb": shipping_thb,
        "total_thb": total_thb,
        "total_usd": total_usd,
        "status": "pending",
        "courier": "รอผู้ผลิตยืนยัน",
        "reserved_until": reserved_until,
    }).execute()
    order = order_res.data[0]
    sb.table("order_status_events").insert({
        "order_id": order["id"], "to_status": "pending", "actor_type": "customer",
        "actor_name": buyer_name or buyer_email, "reason": "สร้างคำสั่งซื้อ",
    }).execute()

    # Decrement stock at the most specific level available.
    if variant and variant.get("stock_override") is not None:
        sb.table("product_variants").update({
            "stock_override": int(variant["stock_override"]) - quantity,
        }).eq("id", variant_id).execute()
    else:
        sb.table("products").update({"stock": product["stock"] - quantity}).eq("id", product_id).execute()

    # Generate PromptPay QR payload
    payload = promptpay_qrcode.generate_payload(_get_promptpay_id(), total_thb)

    return {
        "id": order["id"],
        "status": "pending",
        "total_thb": total_thb,
        "subtotal_thb": subtotal_thb,
        "shipping_thb": shipping_thb,
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
        .select("*, products(title_th, images, fabric_patterns(image_url)), product_variants(size, color), order_status_events(*)")
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
            "updated_at": o.get("updated_at"),
            "events": o.get("order_status_events") or [],
        }
        for o in orders
    ]


@router.post("/upload")
async def upload_product(
    fabric_id: Optional[int] = Form(None),
    title_th: str = Form(...),
    title_en: Optional[str] = Form(None),
    description_th: Optional[str] = Form(None),
    description_en: Optional[str] = Form(None),
    price_thb: float = Form(...),
    category: str = Form("shirt"),
    stock: int = Form(1),
    product_type: str = Form("ready_to_ship"),
    preparation_time: Optional[str] = Form(None),
    sale_unit: str = Form("piece"),
    width_cm: Optional[float] = Form(None),
    length_cm: Optional[float] = Form(None),
    weight_g: Optional[float] = Form(None),
    fiber_composition: Optional[str] = Form(None),
    primary_color: Optional[str] = Form(None),
    dye_method: Optional[str] = Form(None),
    pattern_name: Optional[str] = Form(None),
    texture: Optional[str] = Form(None),
    production_method: Optional[str] = Form(None),
    production_origin: Optional[str] = Form(None),
    care_instructions: Optional[str] = Form(None),
    shipping_provider: Optional[str] = Form(None),
    shipping_cost_thb: float = Form(0),
    free_shipping: bool = Form(False),
    is_active: bool = Form(True),
    image: Optional[UploadFile] = File(None),
    images: List[UploadFile] = File(default=[]),
    image_url: Optional[str] = Form(None),
    current_user: Optional[dict] = Depends(get_optional_user),
):
    sb = get_supabase()

    if not current_user:
        raise HTTPException(status_code=401, detail="Please sign in before adding a product")
    if product_type not in PRODUCT_TYPES:
        raise HTTPException(status_code=422, detail="Invalid product type")
    if sale_unit not in SALE_UNITS:
        raise HTTPException(status_code=422, detail="Invalid sale unit")
    if price_thb <= 0 or stock < 0:
        raise HTTPException(status_code=422, detail="Price must be positive and stock cannot be negative")
    if any(value is not None and value < 0 for value in (width_cm, length_cm, weight_g)):
        raise HTTPException(status_code=422, detail="Product measurements cannot be negative")

    # Verify fabric exists if provided
    artisan_id = None
    if fabric_id:
        fabric_res = sb.table("fabric_patterns").select("*").eq("id", fabric_id).single().execute()
        if not fabric_res.data:
            raise HTTPException(status_code=404, detail="Fabric not found")
        fabric = fabric_res.data
        artisan_id = fabric.get("artisan_id")
    else:
        # If no fabric is provided, infer artisan_id from current_user
        if current_user.get("role") == "artisan":
            artisan_id = current_user.get("artisan_id")

    # Only the artisan who owns a fabric can list it. Designers may create a
    # listing as well, but their identity is always derived from the token.
    valid_designer_uuid = None
    if current_user.get("role") == "artisan":
        if fabric_id and int(current_user.get("artisan_id") or 0) != int(artisan_id or 0):
            raise HTTPException(status_code=403, detail="You can only list products made from your own fabric")

    elif current_user.get("role") == "designer":
        valid_designer_uuid = current_user.get("sub")
    else:
        raise HTTPException(status_code=403, detail="Artisan or designer account required")

    if is_active:
        _assert_store_can_publish(sb, artisan_id)

    uploaded_images = ([image] if image and image.filename else []) + [
        uploaded for uploaded in images if uploaded and uploaded.filename
    ]
    if len(uploaded_images) > 4:
        raise HTTPException(status_code=400, detail="Upload a maximum of 4 product images")

    final_image_urls = [image_url] if image_url else []
    if uploaded_images:
        import httpx
        from app.config import settings

        async with httpx.AsyncClient() as client:
            for uploaded_image in uploaded_images:
                file_bytes = await uploaded_image.read()
                if len(file_bytes) > 5 * 1024 * 1024:
                    raise HTTPException(status_code=422, detail="Each product image must be 5 MB or smaller")
                content_type = uploaded_image.content_type or "image/jpeg"
                if not content_type.startswith("image/"):
                    raise HTTPException(status_code=422, detail="Product uploads must be images")
                ext = uploaded_image.filename.rsplit(".", 1)[-1] if "." in uploaded_image.filename else "jpg"
                fname = f"product_{uuid.uuid4().hex[:8]}.{ext}"
                url = f"{settings.supabase_url}/storage/v1/object/santhai/{fname}"
                headers = {
                    "Authorization": f"Bearer {settings.supabase_secret_key}",
                    "apikey": settings.supabase_secret_key,
                    "Content-Type": content_type,
                }
                res = await client.post(url, headers=headers, content=file_bytes)
                if res.status_code != 200:
                    raise HTTPException(status_code=502, detail="Failed to upload product image")
                final_image_urls.append(f"{settings.supabase_url}/storage/v1/object/public/santhai/{fname}")
    price_usd = round(price_thb / 35, 2)

    listing_status = _listing_status(is_active, product_type, stock)
    product_res = sb.table("products").insert({
        "fabric_id": fabric_id,
        "designer_id": valid_designer_uuid,
        "artisan_id": artisan_id,
        "title_th": title_th,
        "title_en": title_en,
        "description_th": description_th,
        "description_en": description_en,
        "price_thb": price_thb,
        "price_usd": price_usd,
        "stock": stock,
        "category": category,
        "product_type": product_type,
        "preparation_time": preparation_time,
        "listing_status": listing_status,
        "sale_unit": sale_unit,
        "width_cm": width_cm,
        "length_cm": length_cm,
        "weight_g": weight_g,
        "fiber_composition": fiber_composition,
        "primary_color": primary_color,
        "dye_method": dye_method,
        "pattern_name": pattern_name,
        "texture": texture,
        "production_method": production_method,
        "production_origin": production_origin,
        "care_instructions": care_instructions,
        "shipping_provider": shipping_provider,
        "shipping_cost_thb": 0 if free_shipping else max(shipping_cost_thb, 0),
        "free_shipping": free_shipping,
        "images": final_image_urls,
        "is_active": is_active,
        "published_at": datetime.now(timezone.utc).isoformat() if is_active else None,
    }).execute()

    if not product_res.data:
        raise HTTPException(status_code=500, detail="Failed to create product")

    product_id = product_res.data[0]["id"]
    product_code = f"ST-PRD-{product_id:06d}"
    sb.table("products").update({"product_code": product_code}).eq("id", product_id).execute()
    passport_res = sb.table("santhai_passports").insert({
        "product_id": product_id,
        "passport_code": f"STP-{product_id:08d}",
        "status": "seller_declared",
        "public_note": "ข้อมูล Passport รอการตรวจสอบโดย SanThai",
    }).execute()
    if passport_res.data:
        sb.table("santhai_passport_events").insert({
            "passport_id": passport_res.data[0]["id"],
            "event_type": "listed",
            "actor_name": current_user.get("full_name") or "ร้านค้า SanThai",
            "description_th": "สร้าง Passport สำหรับรายการสินค้า",
            "payload": {"product_code": product_code, "listing_status": listing_status},
        }).execute()

    return {
        "id": product_id,
        "product_code": product_code,
        "title_th": title_th,
        "message": "Product uploaded successfully",
    }


@router.put("/{product_id}")
async def update_product(
    product_id: int,
    title_th: Optional[str] = Form(None),
    title_en: Optional[str] = Form(None),
    description_th: Optional[str] = Form(None),
    description_en: Optional[str] = Form(None),
    price_thb: Optional[float] = Form(None),
    stock: Optional[int] = Form(None),
    category: Optional[str] = Form(None),
    is_active: Optional[bool] = Form(None),
    product_type: Optional[str] = Form(None),
    preparation_time: Optional[str] = Form(None),
    sale_unit: Optional[str] = Form(None),
    width_cm: Optional[float] = Form(None),
    length_cm: Optional[float] = Form(None),
    weight_g: Optional[float] = Form(None),
    fiber_composition: Optional[str] = Form(None),
    primary_color: Optional[str] = Form(None),
    dye_method: Optional[str] = Form(None),
    pattern_name: Optional[str] = Form(None),
    texture: Optional[str] = Form(None),
    production_method: Optional[str] = Form(None),
    production_origin: Optional[str] = Form(None),
    care_instructions: Optional[str] = Form(None),
    shipping_provider: Optional[str] = Form(None),
    shipping_cost_thb: Optional[float] = Form(None),
    free_shipping: Optional[bool] = Form(None),
    image: Optional[UploadFile] = File(None),
    images: List[UploadFile] = File(default=[]),
    current_user: Optional[dict] = Depends(get_optional_user),
):
    sb = get_supabase()

    # Verify product exists and check ownership
    prod_res = sb.table("products").select("id, artisan_id, designer_id, stock, product_type, is_active").eq("id", product_id).single().execute()
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

    if sale_unit is not None and sale_unit not in SALE_UNITS:
        raise HTTPException(status_code=422, detail="Invalid sale unit")
    if product_type is not None and product_type not in PRODUCT_TYPES:
        raise HTTPException(status_code=422, detail="Invalid product type")
    if stock is not None and stock < 0:
        raise HTTPException(status_code=422, detail="Stock cannot be negative")
    if any(value is not None and value < 0 for value in (width_cm, length_cm, weight_g)):
        raise HTTPException(status_code=422, detail="Product measurements cannot be negative")
    if is_active is True:
        _assert_store_can_publish(sb, product.get("artisan_id"))

    update_data = {}
    if title_th is not None:
        update_data["title_th"] = title_th
    if title_en is not None:
        update_data["title_en"] = title_en
    if description_th is not None:
        update_data["description_th"] = description_th
    if description_en is not None:
        update_data["description_en"] = description_en
    if price_thb is not None:
        update_data["price_thb"] = price_thb
        update_data["price_usd"] = round(price_thb / 35, 2)
    if stock is not None:
        update_data["stock"] = stock
    if category is not None:
        update_data["category"] = category
    if is_active is not None:
        update_data["is_active"] = is_active
    if product_type is not None:
        update_data["product_type"] = product_type
    if preparation_time is not None:
        update_data["preparation_time"] = preparation_time
    if sale_unit is not None:
        update_data["sale_unit"] = sale_unit
    if width_cm is not None:
        update_data["width_cm"] = width_cm
    if length_cm is not None:
        update_data["length_cm"] = length_cm
    if weight_g is not None:
        update_data["weight_g"] = weight_g
    if fiber_composition is not None:
        update_data["fiber_composition"] = fiber_composition
    if primary_color is not None:
        update_data["primary_color"] = primary_color
    if dye_method is not None:
        update_data["dye_method"] = dye_method
    if pattern_name is not None:
        update_data["pattern_name"] = pattern_name
    if texture is not None:
        update_data["texture"] = texture
    if production_method is not None:
        update_data["production_method"] = production_method
    if production_origin is not None:
        update_data["production_origin"] = production_origin
    if care_instructions is not None:
        update_data["care_instructions"] = care_instructions
    if shipping_provider is not None:
        update_data["shipping_provider"] = shipping_provider
    if shipping_cost_thb is not None:
        update_data["shipping_cost_thb"] = max(shipping_cost_thb, 0)
    if free_shipping is not None:
        update_data["free_shipping"] = free_shipping
        if free_shipping:
            update_data["shipping_cost_thb"] = 0

    effective_active = is_active if is_active is not None else bool(product.get("is_active", True))
    effective_type = product_type if product_type is not None else product.get("product_type") or "ready_to_ship"
    effective_stock = stock if stock is not None else int(product.get("stock") or 0)
    update_data["listing_status"] = _listing_status(effective_active, effective_type, effective_stock)
    if is_active is True:
        update_data["published_at"] = datetime.now(timezone.utc).isoformat()

    # A new selection replaces the product gallery; leaving it empty preserves
    # the existing images.
    uploaded_images = ([image] if image and image.filename else []) + [
        uploaded for uploaded in images if uploaded and uploaded.filename
    ]
    if len(uploaded_images) > 4:
        raise HTTPException(status_code=400, detail="Upload a maximum of 4 product images")
    if uploaded_images:
        import httpx
        from app.config import settings
        new_urls = []
        async with httpx.AsyncClient() as client:
            for uploaded_image in uploaded_images:
                file_bytes = await uploaded_image.read()
                if len(file_bytes) > 5 * 1024 * 1024:
                    raise HTTPException(status_code=422, detail="Each product image must be 5 MB or smaller")
                content_type = uploaded_image.content_type or "image/jpeg"
                if not content_type.startswith("image/"):
                    raise HTTPException(status_code=422, detail="Product uploads must be images")
                ext = uploaded_image.filename.rsplit(".", 1)[-1] if "." in uploaded_image.filename else "jpg"
                fname = f"product_{uuid.uuid4().hex[:8]}.{ext}"
                url = f"{settings.supabase_url}/storage/v1/object/santhai/{fname}"
                headers = {"Authorization": f"Bearer {settings.supabase_secret_key}", "apikey": settings.supabase_secret_key, "Content-Type": content_type}
                res = await client.post(url, headers=headers, content=file_bytes)
                if res.status_code != 200:
                    raise HTTPException(status_code=502, detail="Failed to upload product image")
                new_urls.append(f"{settings.supabase_url}/storage/v1/object/public/santhai/{fname}")
        update_data["images"] = new_urls

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
    user_id: str = Depends(get_current_user_id),
):
    sb = get_supabase()
    order_res = sb.table("orders").select("id, buyer_id, buyer_name, buyer_email").eq("id", order_id).single().execute()
    if not order_res.data:
        raise HTTPException(status_code=404, detail="Order not found")
    if order_res.data.get("buyer_id") != user_id:
        raise HTTPException(status_code=403, detail="Only the buyer can upload a payment slip")
    if not (slip.content_type or "").startswith("image/"):
        raise HTTPException(status_code=422, detail="Payment slip must be an image")
    
    # Save the slip
    file_bytes = await slip.read()
    if len(file_bytes) > 5 * 1024 * 1024:
        raise HTTPException(status_code=422, detail="Payment slip must be 5 MB or smaller")
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

    # Store the receipt first, then append an auditable status transition.
    res = sb.table("orders").update({
        "slip_url": final_url,
        "paid_at": datetime.now(timezone.utc).isoformat(),
    }).eq("id", order_id).execute()
    
    if not res.data:
        raise HTTPException(status_code=404, detail="Order not found")
        
    update_order_status(
        sb, order_id, "processing", "customer",
        actor_name=order_res.data.get("buyer_name") or order_res.data.get("buyer_email"),
        reason="ผู้ซื้อแนบหลักฐานการชำระเงิน",
    )
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
    if not input_data.items:
        raise HTTPException(status_code=422, detail="Cart must contain at least one item")

    # Validate every line before creating any order, so an invalid later item
    # cannot leave a partially-created cart checkout behind.
    prepared_items = []
    for item in input_data.items:
        if item.quantity < 1:
            raise HTTPException(status_code=422, detail="Quantity must be at least 1")
        release_expired_reservations(sb, product_id=item.product_id)
        prod_res = sb.table("products").select("*").eq("id", item.product_id).single().execute()
        if not prod_res.data or not prod_res.data.get("is_active", True):
            raise HTTPException(status_code=404, detail=f"Product {item.product_id} is not available")
        product = prod_res.data
        variant = None
        price = float(product["price_thb"])
        available_stock = int(product.get("stock") or 0)
        if item.variant_id:
            var_res = (sb.table("product_variants").select("*").eq("id", item.variant_id).eq("product_id", item.product_id).single().execute())
            if not var_res.data:
                raise HTTPException(status_code=404, detail="Product variant not found")
            variant = var_res.data
            price += float(variant.get("additional_price_thb") or 0)
            if variant.get("stock_override") is not None:
                available_stock = int(variant["stock_override"])
        if available_stock < item.quantity:
            raise HTTPException(status_code=400, detail=f"Insufficient stock for product {item.product_id}")
        prepared_items.append((item, product, variant, price))

    subtotal_thb = 0
    shipping_thb = 0
    orders_created = []
    reserved_until = (datetime.now(timezone.utc) + timedelta(minutes=RESERVATION_MINUTES)).isoformat()

    for item, product, variant, price in prepared_items:
        line_subtotal = price * item.quantity
        line_shipping = 0 if product.get("free_shipping") else float(product.get("shipping_cost_thb") or 0)
        line_total = line_subtotal + line_shipping
        subtotal_thb += line_subtotal
        shipping_thb += line_shipping
        
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
            "subtotal_thb": line_subtotal,
            "shipping_thb": line_shipping,
            "total_thb": line_total,
            "total_usd": round(line_total / 35, 2),
            "status": "pending",
            "courier": "รอผู้ผลิตยืนยัน",
            "batch_id": batch_id,
            "reserved_until": reserved_until,
        }).execute()
        
        if order_res.data:
            order_id = order_res.data[0]["id"]
            orders_created.append(order_id)
            sb.table("order_status_events").insert({
                "order_id": order_id, "to_status": "pending", "actor_type": "customer",
                "actor_name": input_data.buyer_name or input_data.buyer_email,
                "reason": "สร้างคำสั่งซื้อจากตะกร้า",
            }).execute()
            # Update stock
            if variant and variant.get("stock_override") is not None:
                sb.table("product_variants").update({
                    "stock_override": int(variant["stock_override"]) - item.quantity,
                }).eq("id", item.variant_id).execute()
            else:
                sb.table("products").update({"stock": product["stock"] - item.quantity}).eq("id", item.product_id).execute()

    if not orders_created:
        raise HTTPException(status_code=400, detail="Failed to create any orders")

    total_thb = subtotal_thb + shipping_thb
    payload = promptpay_qrcode.generate_payload(_get_promptpay_id(), total_thb)

    return {
        "batch_id": batch_id,
        "order_ids": orders_created,
        "total_thb": total_thb,
        "subtotal_thb": subtotal_thb,
        "shipping_thb": shipping_thb,
        "promptpay_payload": payload,
        "reserved_until": reserved_until,
        "message": "Cart checked out successfully"
    }
