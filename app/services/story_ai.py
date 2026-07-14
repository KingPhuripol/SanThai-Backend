"""
Story Structuring AI — Groq (llama-3.3-70b) pipeline.
Extracts cultural tags, occasions, and bilingual summary from raw Thai input.
"""
import json
from typing import Optional

from openai import AsyncOpenAI

from app.config import settings

client = AsyncOpenAI(
    api_key=settings.groq_api_key,
    base_url="https://api.groq.com/openai/v1",
)

_EXTRACTION_PROMPT = """\
You are an expert in Thai textile culture and heritage. Analyze the following description of a Thai fabric pattern and extract structured data.

Return a JSON object with exactly these fields:
- "cultural_tags": list of Thai strings (cultural themes/keywords)
- "occasions": list of Thai strings (when to wear, e.g. ["งานแต่งงาน", "ชีวิตประจำวัน"])
- "weave_type": string (e.g. "มัดหมี", "ขิด", "จก", "ยก")
- "color_palette": list of strings (main colors)
- "symbolism": list of Thai strings (what patterns symbolize)
- "region": string — one of: "north", "northeast", "central", "south"
- "en_summary": string (English description, ~80 words, engaging for international buyers)
- "en_cultural_meaning": string (English cultural significance explanation, ~60 words)
- "thai_keywords": list of Thai strings (search index keywords)

Input:
Story: {story}
Community: {community}, Province: {province}
Weave technique: {weave_technique}
Dye method: {dye_method}

Return ONLY valid JSON, no markdown fences."""


async def extract_fabric_story(
    story: str,
    community: str,
    province: str,
    weave_technique: str = "",
    dye_method: str = "",
) -> dict:
    prompt = _EXTRACTION_PROMPT.format(
        story=story,
        community=community,
        province=province,
        weave_technique=weave_technique,
        dye_method=dye_method,
    )
    try:
        response = await client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            response_format={"type": "json_object"},
        )
        return json.loads(response.choices[0].message.content)
    except Exception:
        return {}


async def translate_to_english(text_th: str) -> str:
    if not text_th:
        return ""
    try:
        response = await client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{
                "role": "user",
                "content": (
                    "Translate this Thai textile description to natural English. "
                    "Keep Thai cultural terms with a brief parenthetical explanation. "
                    f"Text: {text_th}"
                ),
            }],
            temperature=0.2,
        )
        return response.choices[0].message.content.strip()
    except Exception:
        return ""
