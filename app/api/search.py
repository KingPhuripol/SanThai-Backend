"""
Search API — rewritten to use Supabase REST client.
Vector search falls back to text-based filtering when embeddings are not yet generated.
"""
from typing import Optional

from fastapi import APIRouter, File, Query, UploadFile

from app.supabase_client import get_supabase

router = APIRouter()


def _is_verified_store(artisan: dict | None) -> bool:
    if not artisan:
        return False
    status = (artisan.get("store_status") or "").lower()
    verified = bool(artisan.get("verified_at"))
    return verified or status in ("approved", "active", "verified")


@router.get("/")
async def semantic_search(
    q: str = Query(..., min_length=1, description="Search query in Thai or English"),
    limit: int = 24,
    province: Optional[str] = None,
):
    sb = get_supabase()
    q_clean = q.strip()
    if not q_clean:
        return {"query": q, "total": 0, "results": [], "search_type": "keyword"}

    # Search products table first across all text fields (colors, patterns, categories, techniques)
    p_query = (
        sb.table("products")
        .select("*, fabric_patterns(*, communities(*)), artisans(id, name, verified_at, store_status)")
        .eq("is_active", True)
        .or_(
            f"title_th.ilike.%{q_clean}%,title_en.ilike.%{q_clean}%,description_th.ilike.%{q_clean}%,"
            f"primary_color.ilike.%{q_clean}%,pattern_name.ilike.%{q_clean}%,fiber_composition.ilike.%{q_clean}%,"
            f"category.ilike.%{q_clean}%,production_method.ilike.%{q_clean}%,dye_method.ilike.%{q_clean}%"
        )
        .limit(limit)
    )
    p_res = p_query.execute()
    p_rows = [p for p in (p_res.data or []) if _is_verified_store(p.get("artisans") or {})]

    # Search fabric_patterns table
    f_query = (
        sb.table("fabric_patterns")
        .select("id, name_th, name_en, image_url, weave_technique, dye_method, cultural_meaning_th, cultural_meaning_en, story_tags, usage_rights, artisans(name, verified_at, store_status), communities(province, name), products(*)")
        .or_(
            f"name_th.ilike.%{q_clean}%,name_en.ilike.%{q_clean}%,weave_technique.ilike.%{q_clean}%,"
            f"cultural_meaning_th.ilike.%{q_clean}%,dye_method.ilike.%{q_clean}%,fiber_type.ilike.%{q_clean}%"
        )
        .limit(limit)
    )
    f_res = f_query.execute()
    f_rows = f_res.data or []

    seen_products = set()
    results = []

    for p in p_rows:
        pid = p["id"]
        seen_products.add(pid)
        fabric = p.get("fabric_patterns") or {}
        artisan = p.get("artisans") or {}
        community = (fabric.get("communities") or {}) if isinstance(fabric, dict) else {}
        results.append({
            "fabric_id": fabric.get("id") or pid,
            "product_id": pid,
            "name_th": p.get("title_th"),
            "name_en": p.get("title_en"),
            "image_url": (p.get("images") or [None])[0] or fabric.get("image_url"),
            "weave_technique": p.get("production_method") or fabric.get("weave_technique") or "ทอมือ",
            "dye_method": p.get("dye_method") or fabric.get("dye_method"),
            "cultural_meaning_th": fabric.get("cultural_meaning_th") or p.get("description_th"),
            "cultural_meaning_en": fabric.get("cultural_meaning_en") or p.get("description_en"),
            "story_tags": fabric.get("story_tags"),
            "usage_rights": fabric.get("usage_rights") or "commercial",
            "artisan_name": artisan.get("name"),
            "province": community.get("province") or "ประเทศไทย",
            "community_name": community.get("name"),
            "price_thb": p.get("price_thb"),
            "price_usd": p.get("price_usd"),
            "relevance_score": 95.0,
        })

    for f in f_rows:
        artisan = f.get("artisans") or {}
        if not _is_verified_store(artisan):
            continue
        community = f.get("communities") or {}
        products = f.get("products") or []
        active_prods = [p for p in products if p.get("is_active")]
        prod = active_prods[0] if active_prods else {}
        pid = prod.get("id")
        if pid and pid in seen_products:
            continue
        if pid:
            seen_products.add(pid)
        results.append({
            "fabric_id": f["id"],
            "product_id": pid,
            "name_th": f["name_th"],
            "name_en": f.get("name_en"),
            "image_url": f.get("image_url") or (prod.get("images") or [None])[0],
            "weave_technique": f.get("weave_technique"),
            "dye_method": f.get("dye_method"),
            "cultural_meaning_th": f.get("cultural_meaning_th"),
            "cultural_meaning_en": f.get("cultural_meaning_en"),
            "story_tags": f.get("story_tags"),
            "usage_rights": f.get("usage_rights") or "commercial",
            "artisan_name": artisan.get("name"),
            "province": community.get("province"),
            "community_name": community.get("name"),
            "price_thb": prod.get("price_thb"),
            "price_usd": prod.get("price_usd"),
            "relevance_score": 85.0,
        })

    if province:
        results = [r for r in results if province.lower() in (r.get("province") or "").lower()]

    return {"query": q, "total": len(results), "results": results[:limit], "search_type": "keyword"}



@router.post("/analyze-image")
async def analyze_fabric_image(
    image: UploadFile = File(..., description="ภาพผ้าที่ต้องการวิเคราะห์"),
    limit: int = 3,
):
    sb = get_supabase()
    image_bytes = await image.read()
    mime_type = image.content_type or "image/jpeg"

    # VLM analysis (OpenAI gpt-4o-mini, falls back to Groq internally)
    from app.services.fabric_vision_service import identify_fabric_from_image, search_fabrics_by_keywords
    try:
        vision_result = await identify_fabric_from_image(image_bytes, mime_type)
    except Exception as e:
        vision_result = {"error": str(e), "search_keywords": []}

    # Find visually-similar fabrics via the VLM's descriptive keywords —
    # replaces the old CLIP embedding search (see fabric_vision_service.py
    # docstring: CLIP requires a locally-loaded model that hung this dev
    # environment; keyword search is a plain DB query, never hangs).
    keywords = list(vision_result.get("search_keywords") or [])
    for extra in (vision_result.get("fabric_type_th"), vision_result.get("pattern_name_th")):
        if extra:
            keywords.append(extra)

    similar_fabrics = []
    for r in search_fabrics_by_keywords(sb, keywords, limit=limit):
        products_r = sb.table("products").select("price_thb").eq("fabric_id", r["id"]).limit(1).execute()
        price = (products_r.data or [{}])[0].get("price_thb")
        similar_fabrics.append({
            "fabric_id": r["id"],
            "name_th": r["name_th"],
            "name_en": r.get("name_en"),
            "image_url": r.get("image_url"),
            "weave_technique": r.get("weave_technique"),
            "dye_method": r.get("dye_method"),
            "cultural_meaning_th": r.get("cultural_meaning_th"),
            "cultural_meaning_en": r.get("cultural_meaning_en"),
            "artisan_name": (r.get("artisans") or {}).get("name"),
            "province": (r.get("communities") or {}).get("province"),
            "price_thb": price,
        })

    return {"vision_analysis": vision_result, "similar_fabrics": similar_fabrics}
