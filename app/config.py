from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # Database
    database_url: str = "postgresql+asyncpg://santhai:santhai_dev_2026@localhost:5432/santhai_db"

    # Redis
    redis_url: str = "redis://localhost:6379"

    # OpenAI (legacy — ใช้ Groq แทน)
    openai_api_key: str = ""

    # Groq
    groq_api_key: str = ""
    
    # Typhoon
    typhoon_api_key: str = ""

    # Storage (Cloudflare R2 / AWS S3 compatible)
    storage_bucket: str = "santhai-fabrics"
    storage_endpoint_url: str = ""
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""

    # App
    app_url: str = "http://localhost:3000"
    secret_key: str = "santhai-secret-key-change-in-production"

    # Supabase (fallback when direct DB is unavailable)
    supabase_url: str = "https://shqgmstbrwkxycyellgn.supabase.co"
    supabase_secret_key: str = ""

    class Config:
        env_file = ".env"
        extra = "ignore"


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
