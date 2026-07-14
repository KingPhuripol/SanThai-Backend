"""
Persona Recommendation AI — Style Quiz → Fabric Matching.
Maps quiz answers to a semantic vector and finds matching fabric patterns.
"""
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

from app.services.search_service import encode_query

_OCCASION_MAP = {
    "งานแต่งงาน": "wedding ceremony formal bridal traditional elegant",
    "งานพิธีการ": "ceremony official formal government professional",
    "ชีวิตประจำวัน": "everyday casual modern wearable comfortable",
    "งานกลางคืน": "evening party modern fashion unique",
    "งานทางการ": "formal official professional meeting sophisticated",
    "ท่องเที่ยว": "travel souvenir authentic heritage cultural gift",
}

_PERSONALITY_MAP = {
    "หรูหราคลาสสิก": "elegant classic luxury silk premium refined",
    "สบายๆ ทันสมัย": "casual modern cotton comfortable trendy",
    "ประเพณีดั้งเดิม": "traditional heritage authentic cultural conservative",
    "อาร์ตครีเอทีฟ": "artistic creative unique artisan handcraft expressive",
    "สุขุมน่าเชื่อถือ": "professional formal refined sophisticated trustworthy",
}

_REASONS = [
    "ลาย{name}เหมาะกับ{occasion}และสอดคล้องกับบุคลิก{personality}ของคุณอย่างมาก",
    "ผ้านี้ถูกคัดสรรมาเพื่อตอบโจทย์ Lifestyle {personality} ในงาน{occasion}โดยเฉพาะ",
    "{name} มีเรื่องราวทางวัฒนธรรมที่แข็งแกร่งและความงามที่เหมาะกับ{occasion}",
]


async def get_recommendations(
    db: AsyncSession,
    occasion: str,
    personality: str,
    preferred_color: str,
    budget_thb: int,
    gender: str,
    limit: int = 3,
) -> list:
    occ_text = _OCCASION_MAP.get(occasion, occasion)
    per_text = _PERSONALITY_MAP.get(personality, personality)
    query_text = f"{occ_text} {per_text} {preferred_color} Thai fabric textile handcraft"
    query_embedding = encode_query(query_text)

    result = await db.execute(
        text("""
            SELECT
                fp.id, fp.name_th, fp.name_en,
                fp.cultural_meaning_th, fp.cultural_meaning_en,
                fp.weave_technique, fp.dye_method, fp.image_url, fp.story_tags,
                p.price_thb, p.price_usd, p.id AS product_id,
                a.name AS artisan_name, c.province,
                1 - (fp.text_embedding <=> :emb::vector) AS score
            FROM fabric_patterns fp
            LEFT JOIN products p ON p.fabric_id = fp.id AND p.is_active = true
            LEFT JOIN artisans a ON fp.artisan_id = a.id
            LEFT JOIN communities c ON fp.community_id = c.id
            WHERE fp.text_embedding IS NOT NULL
              AND (p.price_thb IS NULL OR p.price_thb <= :budget)
            ORDER BY fp.text_embedding <=> :emb::vector
            LIMIT :lim
        """),
        {"emb": str(query_embedding), "budget": budget_thb, "lim": limit},
    )
    rows = result.fetchall()

    output = []
    for i, row in enumerate(rows):
        template = _REASONS[i % len(_REASONS)]
        reason = template.format(
            name=row.name_th, occasion=occasion, personality=personality
        )
        output.append({
            "fabric_id": row.id,
            "product_id": row.product_id,
            "name_th": row.name_th,
            "name_en": row.name_en,
            "cultural_meaning_th": row.cultural_meaning_th,
            "cultural_meaning_en": row.cultural_meaning_en,
            "image_url": row.image_url,
            "artisan_name": row.artisan_name,
            "province": row.province,
            "price_thb": row.price_thb,
            "price_usd": row.price_usd,
            "match_score": round(float(row.score) * 100, 1),
            "reason": reason,
        })

    return output


async def get_recommendations_supabase(
    sb,
    occasion: str,
    personality: str,
    preferred_color: str,
    budget_thb: int,
    gender: str,
    limit: int = 3,
) -> list:
    """Query recommendations through Supabase REST client with heuristic scoring."""
    res = (
        sb.table("products")
        .select("*, fabric_patterns(*, communities(*)), artisans(*)")
        .eq("is_active", True)
        .lte("price_thb", budget_thb)
        .execute()
    )
    rows = res.data or []

    # Score each product based on matches with occasion, personality, preferred_color.
    results = []

    # Simple keyword maps for scoring
    occ_keywords = _OCCASION_MAP.get(occasion, occasion).lower().split()
    per_keywords = _PERSONALITY_MAP.get(personality, personality).lower().split()
    color_kw = preferred_color.lower()

    for row in rows:
        fabric = row.get("fabric_patterns") or {}
        artisan = row.get("artisans") or {}
        community = fabric.get("communities") or {}

        # Calculate matching score
        score = 50.0  # Base score

        # 1. Occasion match
        story_tags = fabric.get("story_tags") or {}
        occasions_list = story_tags.get("occasions") or []
        if any(occasion.lower() in occ.lower() for occ in occasions_list):
            score += 20.0

        # 2. Color match in name, weave_technique, dye_method, or cultural meaning
        text_to_search = f"{(fabric.get('name_th') or '')} {(fabric.get('weave_technique') or '')} {(fabric.get('dye_method') or '')} {(fabric.get('cultural_meaning_th') or '')}".lower()
        if color_kw in text_to_search:
            score += 15.0

        # 3. Personality match
        cultural_tags = story_tags.get("cultural_tags") or []
        for pk in per_keywords:
            if pk in text_to_search or any(pk in tag.lower() for tag in cultural_tags):
                score += 5.0

        # Cap score to 99.0
        score = min(score, 99.0)

        results.append((score, row))

    # Sort by score descending
    results.sort(key=lambda x: x[0], reverse=True)

    output = []
    for i, (score, row) in enumerate(results[:limit]):
        fabric = row.get("fabric_patterns") or {}
        artisan = row.get("artisans") or {}
        community = fabric.get("communities") or {}

        template = _REASONS[i % len(_REASONS)]
        reason = template.format(
            name=fabric.get("name_th", ""), occasion=occasion, personality=personality
        )

        output.append({
            "fabric_id": fabric.get("id"),
            "product_id": row.get("id"),
            "name_th": fabric.get("name_th"),
            "name_en": fabric.get("name_en"),
            "cultural_meaning_th": fabric.get("cultural_meaning_th"),
            "cultural_meaning_en": fabric.get("cultural_meaning_en"),
            "image_url": fabric.get("image_url"),
            "artisan_name": artisan.get("name"),
            "province": community.get("province"),
            "price_thb": row.get("price_thb"),
            "price_usd": row.get("price_usd"),
            "match_score": round(score, 1),
            "reason": reason,
        })

    return output

