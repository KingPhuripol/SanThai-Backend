"""
Shared asyncpg connection helper for one-off admin/migration scripts.
Reads DATABASE_URL from backend/.env — never hardcode credentials here.
"""
import os
from pathlib import Path

import asyncpg
from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parent.parent  # repo root
load_dotenv(_ROOT / ".env")
load_dotenv(Path(__file__).resolve().parent / ".env", override=True)  # backend/.env wins


async def get_connection() -> asyncpg.Connection:
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        raise RuntimeError("DATABASE_URL is not set in backend/.env")
    if db_url.startswith("postgresql+asyncpg://"):
        db_url = db_url.replace("postgresql+asyncpg://", "postgresql://", 1)

    candidates = [db_url]
    if ":5432/" in db_url:
        candidates.append(db_url.replace(":5432/", ":6543/", 1))
    elif ":6543/" in db_url:
        candidates.append(db_url.replace(":6543/", ":5432/", 1))

    last_err = None
    for url in candidates:
        try:
            return await asyncpg.connect(url, timeout=10)
        except Exception as e:
            last_err = e
    raise RuntimeError(f"Could not connect to database via any candidate URL: {last_err}")
