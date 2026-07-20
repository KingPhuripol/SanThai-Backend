"""Apply data/phase2_operational_schema.sql using DATABASE_URL from backend/.env."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from db_conn import get_connection


async def main():
    sql_path = Path(__file__).resolve().parents[2] / "data" / "phase2_operational_schema.sql"
    conn = await get_connection()
    try:
        await conn.execute(sql_path.read_text(encoding="utf-8"))
        print("Phase 2 operational schema applied successfully.")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
