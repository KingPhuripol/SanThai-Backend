"""
อัปเดต text_embedding สำหรับทุก FabricPattern ที่มีอยู่ใน DB
รัน: cd backend && python fix_embeddings.py
"""
import asyncio
import json
import os
import sys
from pathlib import Path

# ต้อง set tokenizers parallelism ก่อน import transformers
os.environ["TOKENIZERS_PARALLELISM"] = "false"

sys.path.insert(0, str(Path(__file__).parent))

from app.database import AsyncSessionLocal
from app.models.models import FabricPattern
from app.services.search_service import encode_query
from sqlalchemy import select


async def fix_embeddings():
    seed_path = Path(__file__).parent / "seed_data.json"
    with open(seed_path, "r", encoding="utf-8") as f:
        seed = {item["name_th"]: item for item in json.load(f)}

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(FabricPattern))
        fabrics = result.scalars().all()
        print(f"พบ {len(fabrics)} records ใน DB\n")

        for i, fabric in enumerate(fabrics):
            item = seed.get(fabric.name_th, {})
            text = (
                f"{fabric.name_th} {fabric.name_en or ''} "
                f"{fabric.weave_technique or ''} {fabric.dye_method or ''} "
                f"{fabric.fiber_type or ''} {fabric.cultural_meaning_th or ''} "
                f"{item.get('province', '')} "
                f"{' '.join(item.get('cultural_tags', []))}"
            )
            fabric.text_embedding = encode_query(text)

            if item.get("image_url"):
                fabric.image_url = item["image_url"]

            print(f"  [{i+1:2}/{len(fabrics)}] ✅ {fabric.name_th}")

        await db.commit()
        print(f"\n✅ อัปเดต text_embedding ครบ {len(fabrics)} records")


if __name__ == "__main__":
    asyncio.run(fix_embeddings())
