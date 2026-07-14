#!/usr/bin/env python3
"""
SanThai Database Seed Script
Usage: cd backend && python seed_database.py
Requirements: PostgreSQL running with pgvector extension
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from app.database import create_tables, AsyncSessionLocal
from app.models.models import Artisan, Community, FabricPattern, Product
from app.services.provenance_service import add_provenance_event
from app.services.search_service import encode_query
from sqlalchemy import select


async def seed() -> None:
    print("🌱 SanThai — Starting database seed...")

    await create_tables()
    print("✅ Tables ready")

    data_path = Path(__file__).parent / "seed_data.json"
    with open(data_path, "r", encoding="utf-8") as f:
        items: list[dict] = json.load(f)

    async with AsyncSessionLocal() as db:
        created = 0

        for item in items:
            # ── Community ────────────────────────────────────────────────
            result = await db.execute(
                select(Community).where(Community.name == item["community_name"])
            )
            community = result.scalar_one_or_none()
            if not community:
                community = Community(
                    name=item["community_name"],
                    province=item["province"],
                    region=item["region"],
                    latitude=item.get("latitude"),
                    longitude=item.get("longitude"),
                )
                db.add(community)
                await db.flush()

            # ── Artisan ──────────────────────────────────────────────────
            result = await db.execute(
                select(Artisan).where(Artisan.name == item["artisan_name"])
            )
            artisan = result.scalar_one_or_none()
            if not artisan:
                artisan = Artisan(
                    name=item["artisan_name"],
                    community_id=community.id,
                    bio_th=item.get("artisan_bio_th"),
                    bio_en=item.get("artisan_bio_en"),
                )
                db.add(artisan)
                await db.flush()

            # ── Fabric Pattern ───────────────────────────────────────────
            result = await db.execute(
                select(FabricPattern).where(FabricPattern.name_th == item["name_th"])
            )
            if result.scalar_one_or_none():
                print(f"  ⏭️  Skip (exists): {item['name_th']}")
                continue

            # Build text embedding for semantic search + RAG
            search_text = (
                f"{item['name_th']} {item.get('name_en', '')} "
                f"{item.get('weave_technique', '')} {item.get('dye_method', '')} "
                f"{item.get('fiber_type', '')} {item.get('cultural_meaning_th', '')} "
                f"{item.get('story_th', '')} {item['province']} "
                f"{' '.join(item.get('cultural_tags', []))}"
            )
            text_emb = encode_query(search_text)
            print(f"  📦 Adding: {item['name_th']}")

            fabric = FabricPattern(
                name_th=item["name_th"],
                name_en=item.get("name_en"),
                artisan_id=artisan.id,
                community_id=community.id,
                weave_technique=item.get("weave_technique"),
                dye_method=item.get("dye_method"),
                fiber_type=item.get("fiber_type"),
                cultural_meaning_th=item.get("cultural_meaning_th"),
                cultural_meaning_en=item.get("cultural_meaning_en"),
                usage_rights=item.get("usage_rights", "commercial"),
                image_url=item.get("image_url"),
                story_tags={
                    "occasions": item.get("occasions", []),
                    "cultural_tags": item.get("cultural_tags", []),
                },
                ai_processed=True,
                text_embedding=text_emb,
            )
            db.add(fabric)
            await db.flush()

            # ── Provenance chain ─────────────────────────────────────────
            fiber = item.get("fiber_type", "เส้นใยธรรมชาติ")
            dye = item.get("dye_method", "สีธรรมชาติ")
            weave = item.get("weave_technique", "ทอมือ")
            province = item["province"]

            for event_type, desc_th, desc_en in [
                (
                    "raw_material",
                    f"วัตถุดิบ: {fiber} จาก{province}",
                    f"Raw material: {fiber} from {province}",
                ),
                (
                    "dyeing",
                    f"กระบวนการย้อมสี: {dye}",
                    f"Dyeing: {dye}",
                ),
                (
                    "weaving",
                    f"การทอ: {weave} โดย {artisan.name}",
                    f"Weaving: {weave} by {artisan.name}",
                ),
                (
                    "finished",
                    "ผ้าสำเร็จรูปพร้อมจำหน่าย",
                    "Fabric completed and ready for listing",
                ),
            ]:
                await add_provenance_event(
                    db=db,
                    fabric_id=fabric.id,
                    event_type=event_type,
                    actor_name=artisan.name,
                    location=province,
                    description_th=desc_th,
                    description_en=desc_en,
                )

            # ── Product ───────────────────────────────────────────────────
            product = Product(
                fabric_id=fabric.id,
                artisan_id=artisan.id,
                title_th=item["name_th"],
                title_en=item.get("name_en"),
                description_th=item.get("story_th") or item.get("cultural_meaning_th"),
                description_en=item.get("cultural_meaning_en"),
                price_thb=float(item.get("price_thb", 3000)),
                price_usd=float(item.get("price_usd", 85)),
                stock=5,
                images=[item["image_url"]] if item.get("image_url") else [],
                is_active=True,
            )
            db.add(product)
            created += 1

        await db.commit()

    print(f"\n🎉 Seeded {created} fabric patterns with provenance chains")
    print("✅ Done! Run the backend and visit http://localhost:8000/api/products")


if __name__ == "__main__":
    asyncio.run(seed())
