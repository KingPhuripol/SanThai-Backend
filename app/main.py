import os
import sys
import traceback
from dotenv import load_dotenv
from fastapi import FastAPI

# Some API modules initialise third-party clients at import time. Load the
# backend environment before importing routers so local Uvicorn behaves like
# Settings(env_file='.env') and does not enter the Vercel fallback app.
load_dotenv(".env")

app = FastAPI(
    title="SanThai API",
    description="ถักทอภูมิปัญญาไทย พลิกโฉมหัตถกรรมสู่โลกดิจิทัล",
    version="1.0.0",
)

_import_error = None
try:
    from contextlib import asynccontextmanager
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.staticfiles import StaticFiles

    from app.database import create_tables
    from app.api import analytics, fabrics, products, chat, search, artisan, auth, designer, admin, verify
except Exception as e:
    _import_error = traceback.format_exc()


if _import_error:
    @app.get("/{path:path}")
    async def catch_all(path: str):
        return {
            "status": "error",
            "message": "SanThai FastAPI import failed on Vercel",
            "traceback": _import_error,
        }
else:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "https://santhaishop.vercel.app",  # production (stable alias)
            "https://frontend-kingphuripols-projects.vercel.app",
            "https://frontend-kingphuripol-kingphuripols-projects.vercel.app",
            "http://localhost:3000",
            "http://localhost:3006",
            "http://127.0.0.1:3000",
            "http://127.0.0.1:3006",
        ],
        allow_origin_regex=r"https://frontend-.*-kingphuripols-projects\.vercel\.app",  # preview deploys
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Serve local uploads in development
    try:
        os.makedirs("uploads", exist_ok=True)
        app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")
    except Exception:
        pass

    app.include_router(fabrics.router, prefix="/api/fabrics", tags=["fabrics"])
    app.include_router(products.router, prefix="/api/products", tags=["products"])
    app.include_router(chat.router, prefix="/api/chat", tags=["chat"])
    app.include_router(search.router, prefix="/api/search", tags=["search"])
    app.include_router(artisan.router, prefix="/api/artisan", tags=["artisan"])
    app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
    app.include_router(designer.router, prefix="/api/designer", tags=["designer"])
    app.include_router(admin.router, prefix="/api/admin", tags=["admin"])
    app.include_router(verify.router, prefix="/api/verify/fabric", tags=["verify"])
    app.include_router(analytics.router, prefix="/api/analytics", tags=["analytics"])

    @app.get("/")
    async def root():
        return {"message": "SanThai API — ถักทอภูมิปัญญาไทย สู่โลกดิจิทัล"}

    @app.get("/health")
    async def health():
        return {"status": "ok"}
