"""
Fabric Vision AI — ถ่ายภาพผ้าแล้วบอกว่าเป็นผ้าอะไร
Primary: OpenAI GPT-5.4 mini (multimodal, released 2026-03-17). Falls back to
Groq (llama-4-scout vision) if the OpenAI call fails (rate limit, network,
etc.) — both are plain HTTP API calls, never a locally-loaded model, so
neither can hang the process the way the local CLIP model did.
"""
import base64
import json

from openai import AsyncOpenAI

from app.config import settings

def _get_openai_client() -> AsyncOpenAI:
    return AsyncOpenAI(api_key=settings.openai_api_key or "dummy_key")

def _get_groq_client() -> AsyncOpenAI:
    return AsyncOpenAI(
        api_key=settings.groq_api_key or "dummy_key",
        base_url="https://api.groq.com/openai/v1",
    )

_IDENTIFY_PROMPT = """\
คุณคือผู้เชี่ยวชาญผ้าไทยและสิ่งทอพื้นบ้าน ดูภาพผ้าที่ส่งมาและวิเคราะห์อย่างละเอียด

ให้ตอบกลับเป็น JSON ที่มีฟิลด์ดังนี้:
- "fabric_type_th": ชื่อประเภทผ้าภาษาไทย (เช่น "ผ้ามัดหมี่", "ผ้าจก", "ผ้าขิด", "ผ้ายกดอก", "ผ้าไหม")
- "fabric_type_en": ชื่อประเภทผ้าภาษาอังกฤษ
- "pattern_name_th": ชื่อลวดลายภาษาไทย (ถ้าระบุได้)
- "weave_technique": เทคนิคการทอ
- "fiber_type": ประเภทเส้นใย (ไหม / ฝ้าย / ผสม)
- "colors": รายการสีหลักในผ้า (list)
- "region_guess": ภาคที่น่าจะมาจาก (เหนือ/อีสาน/กลาง/ใต้)
- "province_guess": จังหวัดที่น่าจะมาจาก (ถ้าระบุได้)
- "cultural_meaning_th": ความหมายทางวัฒนธรรมของลวดลาย ภาษาไทย (2-3 ประโยค)
- "description_th": คำอธิบายภาพรวมของผ้าที่เห็น ภาษาไทย (3-4 ประโยค)
- "confidence": ความมั่นใจในการระบุ 0.0-1.0
- "search_keywords": คีย์เวิร์ดสำหรับค้นหาเพิ่มเติม (list ภาษาไทย)

หากไม่แน่ใจในฟิลด์ใด ให้ใส่ null
Return ONLY valid JSON, no markdown."""


async def _call_vision(client: AsyncOpenAI, model: str, data_url: str, use_completion_tokens: bool = False) -> dict:
    token_kwarg = {"max_completion_tokens": 800} if use_completion_tokens else {"max_tokens": 800}
    response = await client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": data_url}},
                    {"type": "text", "text": _IDENTIFY_PROMPT},
                ],
            }
        ],
        temperature=0.2,
        response_format={"type": "json_object"},
        **token_kwarg,
    )
    return json.loads(response.choices[0].message.content)


async def identify_fabric_from_image(image_bytes: bytes, mime_type: str = "image/jpeg") -> dict:
    """รับ image bytes → VLM วิเคราะห์ → คืน dict ข้อมูลผ้า (รวม search_keywords ที่ใช้ค้นฐานข้อมูลต่อ)"""
    b64 = base64.standard_b64encode(image_bytes).decode("utf-8")
    data_url = f"data:{mime_type};base64,{b64}"

    if settings.openai_api_key:
        try:
            return await _call_vision(_get_openai_client(), "gpt-5.4-mini-2026-03-17", data_url, use_completion_tokens=True)
        except Exception:
            pass  # fall through to Groq

    try:
        return await _call_vision(_get_groq_client(), "meta-llama/llama-4-scout-17b-16e-instruct", data_url)
    except Exception as e:
        return {
            "fabric_type_th": "ไม่สามารถระบุได้",
            "fabric_type_en": "Unknown",
            "description_th": f"ไม่สามารถวิเคราะห์ภาพได้: {str(e)}",
            "confidence": 0.0,
            "search_keywords": [],
        }


def search_fabrics_by_keywords(sb, keywords: list, limit: int = 5) -> list:
    """
    Text-based "visual similarity" search — replaces CLIP embedding search.
    Takes the VLM's descriptive keywords and matches them broadly against
    fabric_patterns' text fields, then ranks by how many keywords each row
    actually hits (client-side, since PostgREST can't score OR matches).
    """
    keywords = [k for k in (keywords or []) if k and k.strip()][:6]
    if not keywords:
        return []

    fields = ["name_th", "name_en", "weave_technique", "dye_method", "cultural_meaning_th"]
    conditions = [f"{field}.ilike.%{kw}%" for kw in keywords for field in fields]

    res = (
        sb.table("fabric_patterns")
        .select("id, name_th, name_en, image_url, weave_technique, dye_method, cultural_meaning_th, artisans(name), communities(province)")
        .or_(",".join(conditions))
        .limit(limit * 3)  # over-fetch, then rank and trim client-side
        .execute()
    )
    rows = res.data or []

    scored = []
    for r in rows:
        haystack = " ".join(str(r.get(f) or "") for f in fields).lower()
        hits = sum(1 for kw in keywords if kw.lower() in haystack)
        scored.append((hits, r))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [r for _, r in scored[:limit]]
