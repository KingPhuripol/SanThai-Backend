import ssl
from sqlalchemy.ext.asyncio import (
    create_async_engine,
    AsyncSession,
    async_sessionmaker,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import text
from app.config import settings


def _make_engine_url(url: str) -> str:
    if url.startswith("postgresql://") and "+asyncpg" not in url:
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if url.startswith("postgres://") and "+asyncpg" not in url:
        url = url.replace("postgres://", "postgresql+asyncpg://", 1)
    return url


_ssl_ctx = ssl.create_default_context()
_ssl_ctx.check_hostname = False
_ssl_ctx.verify_mode = ssl.CERT_NONE

import os

if os.environ.get("VERCEL"):
    engine = None
    AsyncSessionLocal = None
else:
    engine = create_async_engine(
        _make_engine_url(settings.database_url),
        echo=False,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
        connect_args={"ssl": _ssl_ctx},
    )

    AsyncSessionLocal = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )


class Base(DeclarativeBase):
    pass


async def create_tables() -> None:
    """สร้างตารางใน DB (ถ้า Supabase ทำไว้แล้วจะ skip อัตโนมัติ)"""
    try:
        async with engine.begin() as conn:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            from app.models import models  # noqa
            await conn.run_sync(Base.metadata.create_all)
    except Exception as e:
        print(f"⚠️  Direct DB create_tables skipped (using Supabase): {e}")


async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()
