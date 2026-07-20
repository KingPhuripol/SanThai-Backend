"""
Search API — rewritten to use Supabase REST client.
Vector search falls back to text-based filtering when embeddings are not yet generated.
"""
from typing import Optional

from fastapi import APIRouter, File, Query, UploadFile

from app.supabase_client import get_supabase

router = APIRouter()


@router.get("/")
async def semantic_search(
    q: str = Query(..., min_length=1, description="Search query in Thai or English"),
    limit: int = 12,
    province: Optional[str] = None,
):
    sb = get_supabase()

    # Try vector search via RPC first
    try:
        from app.services.search_service import encode_query
        embedding = encode_query(q)
        res = sb.rpc("search_fabrics_by_text", {
            "query_embedding": str(embedding),
            "match_count": limit,
            "province_filter": province or "",
        }).execute()
        rows = res.data or []
        if rows:
            return {
                "query": q,
                "total": len(rows),
                "results": rows,
                "search_type": "vector",
            }
    except Exception:
        pass

    # Fallback: keyword-based search
    query = (
        sb.table("fabric_patterns")
        .select("id, name_th, name_en, image_url, weave_technique, dye_method, cultural_meaning_th, cultural_meaning_en, story_tags, usage_rights, artisans(name), communities(province, name), products(id, price_thb, price_usd)")
        .or_(f"name_th.ilike.%{q}%,name_en.ilike.%{q}%,weave_technique.ilike.%{q}%,cultural_meaning_th.ilike.%{q}%")
        .limit(limit)
    )
    res = query.execute()
    rows = res.data or []

    if province:
        rows = [r for r in rows if province.lower() in ((r.get("communities") or {}).get("province", "") or "").lower()]

    results = []
    for r in rows:
        community = r.get("communities") or {}
        artisan = r.get("artisans") or {}
        products = r.get("products") or [{}]
        product = products[0] if products else {}
        results.append({
            "fabric_id": r["id"],
            "product_id": product.get("id"),
            "name_th": r["name_th"],
            "name_en": r.get("name_en"),
            "image_url": r.get("image_url"),
            "weave_technique": r.get("weave_technique"),
            "dye_method": r.get("dye_method"),
            "cultural_meaning_th": r.get("cultural_meaning_th"),
            "cultural_meaning_en": r.get("cultural_meaning_en"),
            "story_tags": r.get("story_tags"),
            "usage_rights": r.get("usage_rights"),
            "artisan_name": artisan.get("name"),
            "province": community.get("province"),
            "community_name": community.get("name"),
            "price_thb": product.get("price_thb"),
            "price_usd": product.get("price_usd"),
            "relevance_score": 70.0,
        })

    return {"query": q, "total": len(results), "results": results, "search_type": "keyword"}


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
