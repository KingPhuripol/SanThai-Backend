"""
Supabase REST client — ใช้เมื่อ direct PostgreSQL connection ไม่พร้อม
ให้ FastAPI อ่าน/เขียนข้อมูลผ่าน Supabase REST API แทน
"""
from supabase import create_client, Client
from app.config import settings
from functools import lru_cache


@lru_cache()
def get_supabase() -> Client:
    return create_client(settings.supabase_url, settings.supabase_secret_key)
