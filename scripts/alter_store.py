import asyncio
import os
import sys
sys.path.append('backend')
from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text

async def alter_store():
    load_dotenv('backend/.env')
    db_url = os.environ.get('DATABASE_URL')
    engine = create_async_engine(db_url)
    async with engine.begin() as conn:
        await conn.execute(text('ALTER TABLE "Store" ADD COLUMN IF NOT EXISTS user_id UUID REFERENCES users_profile(id);'))
        print("Column added successfully.")
    await engine.dispose()

if __name__ == '__main__':
    asyncio.run(alter_store())
