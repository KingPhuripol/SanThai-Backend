#!/usr/bin/env python3
"""
Seed SanThai data directly into Supabase via REST API.
No direct PostgreSQL connection needed.

Reads SUPABASE_URL / SUPABASE_SECRET_KEY from backend/.env by default. To
seed a different project (e.g. production) without editing .env, export
the vars first — real environment values win over .env (load_dotenv never
overrides an already-set var):
    SUPABASE_URL=... SUPABASE_SECRET_KEY=... python seed_supabase.py
"""
import json
import os
import sys
from pathlib import Path
import hashlib
import datetime

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent))
load_dotenv(Path(__file__).parent / ".env")

from supabase import create_client

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SECRET_KEY"]

sb = create_client(SUPABASE_URL, SUPABASE_KEY)


def sha256_hash(data: str) -> str:
    return hashlib.sha256(data.encode()).hexdigest()


def add_provenance_event(fabric_id, event_type, actor_name, location, desc_th, desc_en, prev_hash=None):
    raw = f"{fabric_id}{event_type}{actor_name}{location}{desc_th}"
    current_hash = sha256_hash((prev_hash or "") + raw)
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


def seed():
    print("🌱 SanThai — Seeding into Supabase...")

    data_path = Path(__file__).parent / "seed_data.json"
    with open(data_path, "r", encoding="utf-8") as f:
        items = json.load(f)

    created = 0
    for item in items:
        # ── Community ─────────────────────────────────────────
        res = sb.table("communities").select("id").eq("name", item["community_name"]).execute()
        if res.data:
            community_id = res.data[0]["id"]
        else:
            r = sb.table("communities").insert({
                "name": item["community_name"],
                "province": item["province"],
                "region": item["region"],
                "latitude": item.get("latitude"),
                "longitude": item.get("longitude"),
            }).execute()
            community_id = r.data[0]["id"]
            print(f"  🏘️  Community: {item['community_name']}")

        # ── Artisan ───────────────────────────────────────────
        res = sb.table("artisans").select("id").eq("name", item["artisan_name"]).execute()
        if res.data:
            artisan_id = res.data[0]["id"]
        else:
            r = sb.table("artisans").insert({
                "name": item["artisan_name"],
                "community_id": community_id,
                "bio_th": item.get("artisan_bio_th"),
                "bio_en": item.get("artisan_bio_en"),
            }).execute()
            artisan_id = r.data[0]["id"]

        # ── Check if fabric exists ────────────────────────────
        res = sb.table("fabric_patterns").select("id").eq("name_th", item["name_th"]).execute()
        if res.data:
            print(f"  ⏭️  Skip (exists): {item['name_th']}")
            continue

        # ── Fabric Pattern ────────────────────────────────────
        r = sb.table("fabric_patterns").insert({
            "name_th": item["name_th"],
            "name_en": item.get("name_en"),
            "artisan_id": artisan_id,
            "community_id": community_id,
            "weave_technique": item.get("weave_technique"),
            "dye_method": item.get("dye_method"),
            "fiber_type": item.get("fiber_type"),
            "cultural_meaning_th": item.get("cultural_meaning_th"),
            "cultural_meaning_en": item.get("cultural_meaning_en"),
            "usage_rights": item.get("usage_rights", "commercial"),
            "image_url": item.get("image_url"),
            "story_tags": {
                "occasions": item.get("occasions", []),
                "cultural_tags": item.get("cultural_tags", []),
            },
            "ai_processed": False,
        }).execute()
        fabric_id = r.data[0]["id"]
        print(f"  📦 Added: {item['name_th']}")

        # ── Provenance chain ──────────────────────────────────
        fiber = item.get("fiber_type", "เส้นใยธรรมชาติ")
        dye = item.get("dye_method", "สีธรรมชาติ")
        weave = item.get("weave_technique", "ทอมือ")
        province = item["province"]
        artisan_name = item["artisan_name"]

        h = None
        for event_type, th, en in [
            ("raw_material", f"วัตถุดิบ: {fiber} จาก{province}", f"Raw material: {fiber} from {province}"),
            ("dyeing", f"กระบวนการย้อมสี: {dye}", f"Dyeing: {dye}"),
            ("weaving", f"การทอ: {weave} โดย {artisan_name}", f"Weaving: {weave} by {artisan_name}"),
            ("finished", "ผ้าสำเร็จรูปพร้อมจำหน่าย", "Fabric completed and ready for listing"),
        ]:
            h = add_provenance_event(fabric_id, event_type, artisan_name, province, th, en, h)

        # ── Product ───────────────────────────────────────────
        sb.table("products").insert({
            "fabric_id": fabric_id,
            "artisan_id": artisan_id,
            "title_th": item["name_th"],
            "title_en": item.get("name_en"),
            "description_th": item.get("story_th") or item.get("cultural_meaning_th"),
            "description_en": item.get("cultural_meaning_en"),
            "price_thb": float(item.get("price_thb", 3000)),
            "price_usd": float(item.get("price_usd", 85)),
            "stock": 5,
            "images": [item["image_url"]] if item.get("image_url") else [],
            "is_active": True,
        }).execute()

        created += 1

    print(f"\n🎉 Seeded {created} fabric patterns into Supabase!")
    print("✅ Done!")


if __name__ == "__main__":
    seed()
