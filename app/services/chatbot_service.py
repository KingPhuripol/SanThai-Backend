"""
Agentic RAG Chatbot service — "สานไทยสนทนา".
Uses OpenTyphoon (typhoon-v2.5-30b-a3b-instruct) with Tool Calling 
to query relevant fabric patterns from pgvector.
"""
import uuid
import json
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text
from openai import AsyncOpenAI

from app.config import settings
from app.models.models import ChatHistory
from app.services.search_service import encode_query

client = AsyncOpenAI(
    api_key=settings.typhoon_api_key or "YOUR_API_KEY_HERE",
    base_url="https://api.opentyphoon.ai/v1",
)

_SYSTEM_PROMPT = """\
คุณคือ "สานไทย" (SanThai AI Architect) ผู้เชี่ยวชาญด้านผ้าไทย วัฒนธรรมการทอ และหัตถกรรมพื้นบ้าน
คุณมีเครื่องมือ (Tool) `search_fabric_db` สำหรับค้นหาข้อมูลผ้าไทยในฐานข้อมูล
ให้เรียกใช้ `search_fabric_db` ทุกครั้งที่ผู้ใช้ถามหาลายผ้า จังหวัด หรือต้องการคำแนะนำ 
หากคำถามซับซ้อน เช่น เปรียบเทียบผ้า 2 ชนิด ให้เรียกใช้ Tool หลายครั้งเพื่อหาข้อมูลให้ครบก่อนตอบ
ใช้ภาษาไทยที่อบอุ่น เป็นมิตร และเชี่ยวชาญ เพิ่มข้อมูลเชิงลึกทางวัฒนธรรมให้กับทุกคำตอบ
"""

async def search_fabric_db(db: AsyncSession, query: str, limit: int = 4) -> str:
    query_embedding = encode_query(query)
    result = await db.execute(
        text("""
            SELECT f.name_th, f.name_en, f.cultural_meaning_th, f.weave_technique, f.dye_method, c.province
            FROM fabric_patterns f
            JOIN communities c ON f.community_id = c.id
            WHERE f.text_embedding IS NOT NULL
            ORDER BY f.text_embedding <=> :emb::vector
            LIMIT :lim
        """),
        {"emb": str(query_embedding), "lim": limit},
    )
    rows = result.fetchall()
    if not rows:
        return "ไม่มีข้อมูลที่เกี่ยวข้อง"

    docs = []
    for r in rows:
        docs.append({
            "name": r.name_th,
            "name_en": r.name_en,
            "province": r.province,
            "technique": r.weave_technique,
            "dye": r.dye_method,
            "cultural_meaning": r.cultural_meaning_th
        })
    return json.dumps(docs, ensure_ascii=False)


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


async def chat(
    db: AsyncSession,
    session_id: str,
    user_message: str,
) -> str:
    if not session_id:
        session_id = str(uuid.uuid4())

    # Build message history (last 10 turns)
    history_result = await db.execute(
        select(ChatHistory)
        .where(ChatHistory.session_id == session_id)
        .order_by(ChatHistory.id.desc())
        .limit(10)
    )
    history = list(reversed(history_result.scalars().all()))

    messages = [{"role": "system", "content": _SYSTEM_PROMPT}]
    for h in history:
        messages.append({"role": h.role, "content": h.content})
    messages.append({"role": "user", "content": user_message})

    # Agentic ReAct Loop
    while True:
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
                # Add assistant message with tool calls
                messages.append(response_message)
                
                # Execute tools
                for tool_call in response_message.tool_calls:
                    function_name = tool_call.function.name
                    try:
                        function_args = json.loads(tool_call.function.arguments)
                    except Exception:
                        function_args = {"query": user_message}
                    
                    if function_name == "search_fabric_db":
                        tool_result = await search_fabric_db(
                            db, 
                            query=function_args.get("query", user_message),
                            limit=function_args.get("limit", 4)
                        )
                        
                        messages.append({
                            "tool_call_id": tool_call.id,
                            "role": "tool",
                            "name": function_name,
                            "content": tool_result,
                        })
            else:
                reply = response_message.content
                break
        except Exception as e:
            reply = f"ขออภัยค่ะ เกิดข้อผิดพลาดในการประมวลผล: {str(e)}"
            break

    # Persist to history
    db.add(ChatHistory(session_id=session_id, role="user", content=user_message))
    db.add(ChatHistory(session_id=session_id, role="assistant", content=reply))
    await db.commit()



async def search_fabric_db_supabase(sb, query: str, limit: int = 4) -> str:
    """ค้นหา fabric ผ่าน Supabase REST API (fallback เมื่อ DB ตรง ไม่ work)"""
    try:
        query_embedding = encode_query(query)
        res = sb.rpc("search_fabrics_by_text", {
            "query_embedding": str(query_embedding),
            "match_count": limit,
            "province_filter": "",
        }).execute()
        rows = res.data or []
    except Exception:
        rows = []

    if not rows:
        # Keyword fallback
        res = (
            sb.table("fabric_patterns")
            .select("name_th, name_en, cultural_meaning_th, weave_technique, dye_method, communities(province)")
            .or_(f"name_th.ilike.%{query}%,weave_technique.ilike.%{query}%,cultural_meaning_th.ilike.%{query}%")
            .limit(limit)
            .execute()
        )
        rows = [
            {
                "name": r["name_th"],
                "name_en": r.get("name_en"),
                "province": (r.get("communities") or {}).get("province"),
                "technique": r.get("weave_technique"),
                "dye": r.get("dye_method"),
                "cultural_meaning": r.get("cultural_meaning_th"),
            }
            for r in (res.data or [])
        ]
    
    if not rows:
        return "ไม่มีข้อมูลที่เกี่ยวข้องในฐานข้อมูล"
    return json.dumps(rows, ensure_ascii=False)


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
    while True:
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
                        limit=function_args.get("limit", 4),
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
            reply = f"ขออภัยค่ะ เกิดข้อผิดพลาด: {str(e)}"
            break

    # Save history to Supabase
    sb.table("chat_history").insert([
        {"session_id": session_id, "role": "user", "content": user_message},
        {"session_id": session_id, "role": "assistant", "content": reply},
    ]).execute()

    return reply
