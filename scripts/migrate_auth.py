"""
One-off migration: adds auth-related columns needed for real artisan login
and fixes the checkout_cart orders.buyer_phone bug. Safe to re-run (IF NOT EXISTS).
"""
import asyncio

from db_conn import get_connection


async def main():
    conn = await get_connection()

    print("Creating users_profile (did not exist on the live DB yet)...")
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS users_profile (
            id            UUID PRIMARY KEY,
            email         VARCHAR(255) UNIQUE NOT NULL,
            full_name     VARCHAR(200),
            role          VARCHAR(50) NOT NULL DEFAULT 'customer',
            password_hash VARCHAR(255),
            created_at    TIMESTAMPTZ DEFAULT NOW()
        );
    """)

    print("Adding users_profile.password_hash (in case table pre-existed without it)...")
    await conn.execute("ALTER TABLE users_profile ADD COLUMN IF NOT EXISTS password_hash VARCHAR(255);")

    print("Adding artisans.user_id...")
    await conn.execute(
        "ALTER TABLE artisans ADD COLUMN IF NOT EXISTS user_id UUID REFERENCES users_profile(id);"
    )

    print("Adding orders.buyer_phone...")
    await conn.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS buyer_phone VARCHAR(50);")

    print("Done!")
    await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
