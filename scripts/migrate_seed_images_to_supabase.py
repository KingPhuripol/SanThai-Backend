"""Move legacy localhost seed images into Supabase Storage.

The script only changes URLs that start with localhost/127.0.0.1 uploads.
Run without --apply to inspect; use --apply to upload and update database rows.
"""

import argparse
import mimetypes
import sys
from pathlib import Path
from urllib.parse import quote, urlparse

import httpx

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.config import settings
from app.supabase_client import get_supabase

LOCAL_PREFIXES = (
    "http://localhost:8000/uploads/",
    "http://127.0.0.1:8000/uploads/",
)
ASSET_DIR = ROOT_DIR / "uploads"
STORAGE_PREFIX = "seed-migration/2026-07-18"


def is_legacy_url(value: object) -> bool:
    return isinstance(value, str) and value.startswith(LOCAL_PREFIXES)


def filename_from_url(url: str) -> str:
    return Path(urlparse(url).path).name


def public_url(filename: str) -> str:
    key = quote(f"{STORAGE_PREFIX}/{filename}")
    return f"{settings.supabase_url}/storage/v1/object/public/santhai/{key}"


def upload_asset(client: httpx.Client, filename: str) -> str:
    source = ASSET_DIR / filename
    if not source.is_file():
        raise FileNotFoundError(f"Missing local seed asset: {source}")
    key = quote(f"{STORAGE_PREFIX}/{filename}")
    response = client.post(
        f"{settings.supabase_url}/storage/v1/object/santhai/{key}",
        content=source.read_bytes(),
        headers={
            "Authorization": f"Bearer {settings.supabase_secret_key}",
            "apikey": settings.supabase_secret_key,
            "Content-Type": mimetypes.guess_type(filename)[0] or "image/jpeg",
            "x-upsert": "true",
        },
    )
    response.raise_for_status()
    return public_url(filename)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Upload files and update Supabase rows")
    args = parser.parse_args()
    sb = get_supabase()

    products = sb.table("products").select("id,images").execute().data or []
    fabrics = sb.table("fabric_patterns").select("id,image_url").execute().data or []
    legacy_products = [p for p in products if any(is_legacy_url(url) for url in (p.get("images") or []))]
    legacy_fabrics = [f for f in fabrics if is_legacy_url(f.get("image_url"))]
    filenames = sorted({
        filename_from_url(url)
        for product in legacy_products
        for url in (product.get("images") or [])
        if is_legacy_url(url)
    } | {
        filename_from_url(fabric["image_url"])
        for fabric in legacy_fabrics
    })

    report = {
        "dry_run": not args.apply,
        "legacy_product_ids": [p["id"] for p in legacy_products],
        "legacy_fabric_ids": [f["id"] for f in legacy_fabrics],
        "assets": filenames,
    }
    if not args.apply:
        print(report)
        return

    with httpx.Client(timeout=45) as client:
        migrated_urls = {filename: upload_asset(client, filename) for filename in filenames}

    for product in legacy_products:
        images = [
            migrated_urls[filename_from_url(url)] if is_legacy_url(url) else url
            for url in (product.get("images") or [])
        ]
        sb.table("products").update({"images": images}).eq("id", product["id"]).execute()

    for fabric in legacy_fabrics:
        sb.table("fabric_patterns").update(
            {"image_url": migrated_urls[filename_from_url(fabric["image_url"])]}
        ).eq("id", fabric["id"]).execute()

    report.update({
        "uploaded_assets": len(migrated_urls),
        "updated_products": len(legacy_products),
        "updated_fabrics": len(legacy_fabrics),
    })
    print(report)


if __name__ == "__main__":
    main()
