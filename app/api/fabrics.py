"""
Fabrics API — rewritten to use Supabase REST client instead of SQLAlchemy.
"""
import uuid
import hashlib
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile

from app.api.deps import get_current_artisan_id
from app.supabase_client import get_supabase

router = APIRouter()


# ─── Background AI task ──────────────────────────────────────────────────────

async def _process_fabric_ai(
    fabric_id: int,
    story_th: str,
    community_name: str,
    province: str,
    weave_technique: str,
    dye_method: str,
    image_url: Optional[str],
) -> None:
    sb = get_supabase()
    try:
        from app.services.story_ai import extract_fabric_story

        res = sb.table("fabric_patterns").select("*").eq("id", fabric_id).single().execute()
        fabric = res.data
        if not fabric:
            return

        tags = await extract_fabric_story(story_th, community_name, province, weave_technique, dye_method)

        update_data: dict = {
            "story_tags": tags,
            "ai_processed": True,
        }
        if tags.get("en_cultural_meaning"):
            update_data["cultural_meaning_en"] = tags["en_cultural_meaning"]
        if tags.get("en_summary") and not fabric.get("name_en"):
            update_data["name_en"] = tags["en_summary"][:150]

        sb.table("fabric_patterns").update(update_data).eq("id", fabric_id).execute()
    except Exception as e:
        print(f"⚠️ AI processing error for fabric {fabric_id}: {e}")


def _add_provenance(sb, fabric_id: int, event_type: str, actor_name: str,
                    location: str, desc_th: str, desc_en: str, prev_hash: Optional[str] = None) -> str:
    raw = f"{fabric_id}{event_type}{actor_name}{location}{desc_th}"
    current_hash = hashlib.sha256(((prev_hash or "") + raw).encode()).hexdigest()
    sb.table("provenance_logs").insert({
        "fabric_id": fabric_id,
        "event_type": event_type,
        "actor_name": actor_name,
        "location": location,
        "description_th": desc_th,
        "description_en": desc_en,
        "prev_hash": prev_hash,
        "current_hash": current_hash,
    }).execute()
    return current_hash


# ─── Routes ──────────────────────────────────────────────────────────────────

@router.get("/")
async def list_fabrics(
    skip: int = 0,
    limit: int = 50,
    region: Optional[str] = None,
    weave_technique: Optional[str] = None,
):
    sb = get_supabase()
    query = (
        sb.table("fabric_patterns")
        .select("*, communities(name, province, region)")
        .range(skip, skip + limit - 1)
    )
    if weave_technique:
        query = query.ilike("weave_technique", f"%{weave_technique}%")

    res = query.execute()
    rows = res.data or []

    if region:
        rows = [r for r in rows if (r.get("communities") or {}).get("region", "") == region]

    return [
        {
            "id": f["id"],
            "name_th": f["name_th"],
            "name_en": f.get("name_en"),
            "image_url": f.get("image_url"),
            "weave_technique": f.get("weave_technique"),
            "dye_method": f.get("dye_method"),
            "fiber_type": f.get("fiber_type"),
            "cultural_meaning_th": f.get("cultural_meaning_th"),
            "usage_rights": f.get("usage_rights"),
            "ai_processed": f.get("ai_processed"),
            "story_tags": f.get("story_tags"),
            "community": f.get("communities"),
        }
        for f in rows
    ]


@router.post("/upload")
async def upload_fabric(
    background_tasks: BackgroundTasks,
    artisan_id: int = Depends(get_current_artisan_id),
    name_th: str = Form(...),
    weave_technique: str = Form(""),
    dye_method: str = Form(""),
    fiber_type: str = Form(""),
    cultural_meaning_th: str = Form(""),
    story_th: str = Form(""),
    usage_rights: str = Form("commercial"),
    image: Optional[UploadFile] = File(None),
    image_url: Optional[str] = Form(None),
):
    sb = get_supabase()

    artisan_res = sb.table("artisans").select("*, communities(*)").eq("id", artisan_id).single().execute()
    if not artisan_res.data:
        raise HTTPException(status_code=404, detail="Artisan not found")
    artisan = artisan_res.data
    community = artisan.get("communities") or {}
    province = community.get("province", "")
    community_name = community.get("name", "")

    # Handle image upload
    final_image_url = image_url
    if image and image.filename:
        file_bytes = await image.read()
        content_type = image.content_type or "image/jpeg"
        # Upload to Supabase Storage
        import httpx
        from app.config import settings
        ext = image.filename.rsplit(".", 1)[-1] if "." in image.filename else "jpg"
        fname = f"fabric_{uuid.uuid4().hex[:8]}.{ext}"
        
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

    # Create fabric record
    fabric_res = sb.table("fabric_patterns").insert({
        "name_th": name_th,
        "artisan_id": artisan_id,
        "community_id": artisan.get("community_id"),
        "weave_technique": weave_technique,
        "dye_method": dye_method,
        "fiber_type": fiber_type,
        "cultural_meaning_th": cultural_meaning_th,
        "usage_rights": usage_rights,
        "image_url": final_image_url,
        "ai_processed": False,
    }).execute()
    fabric_id = fabric_res.data[0]["id"]

    # Provenance chain
    h = _add_provenance(sb, fabric_id, "raw_material", artisan["name"], province,
                        f"เริ่มกระบวนการผลิตโดย {artisan['name']} จากชุมชน {community_name}",
                        f"Production started by {artisan['name']} from {community_name}")
    _add_provenance(sb, fabric_id, "finished", artisan["name"], province,
                    f"ผ้าสำเร็จรูป ลาย{name_th}", f"Fabric completed: {name_th}", h)

    background_tasks.add_task(
        _process_fabric_ai, fabric_id, story_th or cultural_meaning_th,
        community_name, province, weave_technique, dye_method, final_image_url,
    )

    return {"id": fabric_id, "name_th": name_th, "message": "Fabric uploaded. AI is processing.", "ai_processing": True}


@router.put("/{fabric_id}")
async def update_fabric(
    fabric_id: int,
    artisan_id: int = Depends(get_current_artisan_id),
    name_th: Optional[str] = Form(None),
    name_en: Optional[str] = Form(None),
    weave_technique: Optional[str] = Form(None),
    dye_method: Optional[str] = Form(None),
    fiber_type: Optional[str] = Form(None),
    cultural_meaning_th: Optional[str] = Form(None),
    usage_rights: Optional[str] = Form(None),
    image: Optional[UploadFile] = File(None),
):
    sb = get_supabase()

    # Verify ownership
    fabric_res = sb.table("fabric_patterns").select("id, artisan_id").eq("id", fabric_id).single().execute()
    if not fabric_res.data:
        raise HTTPException(status_code=404, detail="Fabric not found")
    if fabric_res.data.get("artisan_id") != artisan_id:
        raise HTTPException(status_code=403, detail="Forbidden")

    update_data = {}
    if name_th is not None:
        update_data["name_th"] = name_th
    if name_en is not None:
        update_data["name_en"] = name_en
    if weave_technique is not None:
        update_data["weave_technique"] = weave_technique
    if dye_method is not None:
        update_data["dye_method"] = dye_method
    if fiber_type is not None:
        update_data["fiber_type"] = fiber_type
    if cultural_meaning_th is not None:
        update_data["cultural_meaning_th"] = cultural_meaning_th
    if usage_rights is not None:
        update_data["usage_rights"] = usage_rights

    # Handle image upload
    if image and image.filename:
        file_bytes = await image.read()
        content_type = image.content_type or "image/jpeg"
        import httpx
        from app.config import settings
        ext = image.filename.rsplit(".", 1)[-1] if "." in image.filename else "jpg"
        fname = f"fabric_{uuid.uuid4().hex[:8]}.{ext}"
        url = f"{settings.supabase_url}/storage/v1/object/santhai/{fname}"
        headers = {
            "Authorization": f"Bearer {settings.supabase_secret_key}",
            "apikey": settings.supabase_secret_key,
            "Content-Type": content_type,
        }
        async with httpx.AsyncClient() as client:
            res = await client.post(url, headers=headers, content=file_bytes)
            if res.status_code == 200:
                update_data["image_url"] = f"{settings.supabase_url}/storage/v1/object/public/santhai/{fname}"

    if not update_data:
        return {"message": "No changes"}

    sb.table("fabric_patterns").update(update_data).eq("id", fabric_id).execute()
    return {"id": fabric_id, "message": "Fabric updated successfully"}


@router.get("/{fabric_id}")
async def get_fabric(fabric_id: int):
    sb = get_supabase()
    res = sb.table("fabric_patterns").select("*, artisans(*), communities(*)").eq("id", fabric_id).single().execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Fabric not found")
    f = res.data
    return {
        "id": f["id"],
        "name_th": f["name_th"],
        "name_en": f.get("name_en"),
        "artisan": f.get("artisans"),
        "community": f.get("communities"),
        "weave_technique": f.get("weave_technique"),
        "dye_method": f.get("dye_method"),
        "fiber_type": f.get("fiber_type"),
        "cultural_meaning_th": f.get("cultural_meaning_th"),
        "cultural_meaning_en": f.get("cultural_meaning_en"),
        "usage_rights": f.get("usage_rights"),
        "story_tags": f.get("story_tags"),
        "image_url": f.get("image_url"),
        "ai_processed": f.get("ai_processed"),
        "created_at": f.get("created_at"),
    }


@router.get("/{fabric_id}/provenance")
async def get_provenance(fabric_id: int):
    sb = get_supabase()
    res = sb.table("provenance_logs").select("*").eq("fabric_id", fabric_id).order("id").execute()
    logs = res.data or []

    # Verify chain
    chain_valid = True
    for i, log in enumerate(logs):
        raw = f"{fabric_id}{log['event_type']}{log['actor_name']}{log.get('location', '')}{log.get('description_th', '')}"
        expected = hashlib.sha256(((log.get("prev_hash") or "") + raw).encode()).hexdigest()
        if expected != log["current_hash"]:
            chain_valid = False
            break

    return {
        "fabric_id": fabric_id,
        "chain_valid": chain_valid,
        "total_events": len(logs),
        "events": [
            {
                "id": log["id"],
                "event_type": log["event_type"],
                "actor_name": log["actor_name"],
                "location": log.get("location"),
                "description_th": log.get("description_th"),
                "description_en": log.get("description_en"),
                "timestamp": log.get("timestamp"),
                "hash": log["current_hash"][:16] + "…",
                "prev_hash": log["prev_hash"][:16] + "…" if log.get("prev_hash") else "GENESIS",
            }
            for log in logs
        ],
    }


@router.post("/recognize")
async def recognize_pattern(image: UploadFile = File(...)):
    """
    VLM-based fabric recognition (OpenAI gpt-4o-mini, falls back to Groq) —
    replaces the old CLIP-embedding + pgvector approach, which required a
    locally-loaded model that could hang the process in some environments.
    """
    from app.services.fabric_vision_service import identify_fabric_from_image, search_fabrics_by_keywords

    image_bytes = await image.read()
    mime_type = image.content_type or "image/jpeg"
    vision_result = await identify_fabric_from_image(image_bytes, mime_type)

    keywords = list(vision_result.get("search_keywords") or [])
    for extra in (vision_result.get("fabric_type_th"), vision_result.get("pattern_name_th")):
        if extra:
            keywords.append(extra)

    sb = get_supabase()
    rows = search_fabrics_by_keywords(sb, keywords, limit=5)

    matches = [
        {
            "fabric_id": r["id"],
            "name_th": r.get("name_th"),
            "name_en": r.get("name_en"),
            "image_url": r.get("image_url"),
            "weave_technique": r.get("weave_technique"),
        }
        for r in rows
    ]

    return {
        "vision_analysis": vision_result,
        "matches": matches,
        "needs_human_review": len(matches) == 0,
    }
