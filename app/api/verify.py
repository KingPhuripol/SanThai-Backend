import os
import json
import hashlib
from datetime import datetime
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from openai import OpenAI
import time

router = APIRouter()

client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

class VerifyRequest(BaseModel):
    image_base64: str

class VerifyResponse(BaseModel):
    fabric_type: str
    pattern: str
    colors: list[str]
    confidence_score: float
    blockchain_hash: str
    timestamp: str

@router.post("/", response_model=VerifyResponse)
async def verify_fabric(req: VerifyRequest):
    if not client.api_key:
        raise HTTPException(status_code=500, detail="OpenAI API key not configured")

    # The incoming base64 might have "data:image/jpeg;base64," prefix.
    # We pass the full URL string to OpenAI vision
    image_url = req.image_base64
    if not image_url.startswith("data:image"):
        image_url = f"data:image/jpeg;base64,{image_url}"

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": """คุณคือผู้เชี่ยวชาญด้านผ้าไทย วิเคราะห์ภาพผ้าที่ส่งมาและส่งผลลัพธ์กลับมาเป็น JSON FORMAT เท่านั้น:
{
    "fabric_type": "ประเภทของผ้า (เช่น ผ้ามัดหมี่, ผ้าขิด, ผ้าจก)",
    "pattern": "ชื่อลวดลาย (เช่น ลายขอ, ลายน้ำไหล)",
    "colors": ["สีที่ 1", "สีที่ 2"],
    "confidence_score": 98.5
}
"""
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "วิเคราะห์ภาพผ้านี้ให้หน่อย"},
                        {"type": "image_url", "image_url": {"url": image_url}}
                    ]
                }
            ],
            response_format={"type": "json_object"},
            max_tokens=300
        )
        
        result_text = response.choices[0].message.content
        ai_data = json.loads(result_text)
        
        # Generate a fake blockchain hash based on timestamp
        mock_hash_source = f"SANTHAI-{time.time()}-{ai_data.get('fabric_type')}"
        mock_hash = hashlib.sha256(mock_hash_source.encode()).hexdigest()
        
        return VerifyResponse(
            fabric_type=ai_data.get("fabric_type", "ไม่ทราบประเภท"),
            pattern=ai_data.get("pattern", "ไม่ทราบลวดลาย"),
            colors=ai_data.get("colors", []),
            confidence_score=ai_data.get("confidence_score", 90.0),
            blockchain_hash=mock_hash,
            timestamp=datetime.now().isoformat()
        )
    except Exception as e:
        print("Error calling OpenAI:", str(e))
        raise HTTPException(status_code=500, detail="Failed to analyze image")
