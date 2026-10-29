"""
Agentic RAG Chatbot service — "สานไทยสนทนา".
Uses OpenTyphoon (typhoon-v2.5-30b-a3b-instruct) with Tool Calling 
to query relevant fabric patterns from pgvector.
"""
import json
from openai import AsyncOpenAI

from app.config import settings

client = AsyncOpenAI(
    api_key=settings.typhoon_api_key or "unset",
    base_url="https://api.opentyphoon.ai/v1",
)

_SYSTEM_PROMPT = """\
คุณคือ "สานไทย" (SanThai AI Architect) ผู้เชี่ยวชาญด้านผ้าไทย วัฒนธรรมการทอ และหัตถกรรมพื้นบ้าน
คุณมีเครื่องมือ (Tool) `search_fabric_db` สำหรับค้นหาข้อมูลผ้าไทยในฐานข้อมูล
ให้เรียกใช้ `search_fabric_db` ทุกครั้งที่ผู้ใช้ถามหาลายผ้า จังหวัด หรือต้องการคำแนะนำ 
หากคำถามซับซ้อน เช่น เปรียบเทียบผ้า 2 ชนิด ให้เรียกใช้ Tool หลายครั้งเพื่อหาข้อมูลให้ครบก่อนตอบ
ใช้ภาษาไทยที่อบอุ่น เป็นมิตร และเชี่ยวชาญ เพิ่มข้อมูลเชิงลึกทางวัฒนธรรมให้กับทุกคำตอบ
"""

_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_fabric_db",
            "description": "ค้นหาข้อมูลผ้าไทยในฐานข้อมูล SanThai (เช่น ค้นหาด้วยชื่อจังหวัด ชื่อลาย หรือประเภทการใช้งาน)",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "คำค้นหา เช่น 'ผ้าจากภาคเหนือ', 'ผ้าไหมสีฟ้า', 'แพรวา'"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "จำนวนที่ต้องการ (default 4)"
                    }
                },
                "required": ["query"]
            }
        }
    }
]


async def search_fabric_db_supabase(sb, query: str, limit: int = 4) -> str:
    """ค้นหา fabric ผ่าน Supabase REST API พร้อมสกัดคีย์เวิร์ดอย่างฉลาด"""
    import re
    cleaned = re.sub(r"[^\w\s\u0e00-\u0e7f]", " ", query)
    tokens = [t for t in cleaned.split() if len(t) > 1]
    
    stopwords = {"สอบถาม", "ประวัติ", "ขอ", "อยาก", "ทราบ", "เกี่ยวกับ", "หน่อย", "ครับ", "ค่ะ", "ช่วย", "บอก", "แนะนำ", "ข้อมูล"}
    keywords = [t for t in tokens if t not in stopwords]
    if not keywords:
        keywords = tokens or [query]

    rows = []
    seen = set()
    for kw in keywords[:4]:
        try:
            res = (
                sb.table("fabric_patterns")
                .select("name_th, name_en, cultural_meaning_th, weave_technique, dye_method, communities(province)")
                .or_(f"name_th.ilike.%{kw}%,weave_technique.ilike.%{kw}%,cultural_meaning_th.ilike.%{kw}%")
                .limit(limit)
                .execute()
            )
            for r in (res.data or []):
                name = r["name_th"]
                if name not in seen:
                    seen.add(name)
                    rows.append({
                        "name": name,
                        "name_en": r.get("name_en"),
                        "province": (r.get("communities") or {}).get("province"),
                        "technique": r.get("weave_technique"),
                        "dye": r.get("dye_method"),
                        "cultural_meaning": r.get("cultural_meaning_th"),
                    })
        except Exception:
            pass

    if not rows:
        return "ไม่มีข้อมูลที่เกี่ยวข้องในฐานข้อมูล"
    return json.dumps(rows[:limit], ensure_ascii=False)


async def chat_with_supabase(sb, session_id: str, user_message: str) -> str:
    """Chat function using Supabase REST client for history storage."""
    # Load history
    history_res = (
        sb.table("chat_history")
        .select("role, content")
        .eq("session_id", session_id)
        .order("id", desc=True)
        .limit(10)
        .execute()
    )
    history = list(reversed(history_res.data or []))

    messages = [{"role": "system", "content": _SYSTEM_PROMPT}]
    for h in history:
        messages.append({"role": h["role"], "content": h["content"]})
    messages.append({"role": "user", "content": user_message})

    # Agentic ReAct Loop
    reply = "ขออภัยค่ะ ไม่สามารถประมวลผลได้ในขณะนี้"
    for _ in range(3):  # cap tool-call rounds
        try:
            response = await client.chat.completions.create(
                model="typhoon-v2.5-30b-a3b-instruct",
                messages=messages,
                tools=_TOOLS,
                tool_choice="auto",
                temperature=0.6,
                max_tokens=800,
            )
            response_message = response.choices[0].message

            if response_message.tool_calls:
                messages.append(response_message)
                for tool_call in response_message.tool_calls:
                    try:
                        function_args = json.loads(tool_call.function.arguments)
                    except Exception:
                        function_args = {"query": user_message}
                    tool_result = await search_fabric_db_supabase(
                        sb,
                        query=function_args.get("query", user_message),
                        limit=max(1, min(int(function_args.get("limit") or 4), 8)),
                    )
                    messages.append({
                        "tool_call_id": tool_call.id,
                        "role": "tool",
                        "name": "search_fabric_db",
                        "content": tool_result,
                    })
            else:
                reply = response_message.content
                break
        except Exception as e:
            print(f"chat failed: {e}")
            break

    # Save history to Supabase
    sb.table("chat_history").insert([
        {"session_id": session_id, "role": "user", "content": user_message},
        {"session_id": session_id, "role": "assistant", "content": reply},
    ]).execute()

    return reply
