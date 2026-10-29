"""
Shared FastAPI auth dependencies. Import from here in fabrics.py / products.py /
artisan.py / designer.py to avoid duplicating token-decoding logic.
"""
import os
import time
from collections import defaultdict, deque
from typing import Optional

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError

from app.core.security import decode_access_token
from app.supabase_client import get_supabase

bearer_scheme = HTTPBearer(auto_error=False)


def _is_active(user: dict) -> bool:
    """Tokens live 7 days, so suspension is checked against the DB on every request."""
    res = get_supabase().table("users_profile").select("is_suspended").eq("id", user.get("sub")).execute()
    return bool(res.data) and not res.data[0].get("is_suspended")


async def get_current_user(
    creds: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> dict:
    if creds is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        user = decode_access_token(creds.credentials)
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    if not _is_active(user):
        raise HTTPException(status_code=403, detail="Account is suspended or no longer exists")
    return user


async def get_optional_user(
    creds: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> Optional[dict]:
    """Soft auth: never raises. Used where a request may or may not carry a
    token (e.g. product upload, reachable both by a logged-in designer and by
    an artisan's own auto-listing flow, which attaches its own artisan token)."""
    if creds is None:
        return None
    try:
        user = decode_access_token(creds.credentials)
    except JWTError:
        return None
    return user if _is_active(user) else None


async def get_current_artisan_id(user: dict = Depends(get_current_user)) -> int:
    if user.get("role") != "artisan" or not user.get("artisan_id"):
        raise HTTPException(status_code=403, detail="Artisan account required")
    return int(user["artisan_id"])


async def get_current_designer_id(user: dict = Depends(get_current_user)) -> str:
    if user.get("role") != "designer":
        raise HTTPException(status_code=403, detail="Designer account required")
    return user["sub"]


async def get_current_user_id(user: dict = Depends(get_current_user)) -> str:
    return user["sub"]


async def get_current_admin(user: dict = Depends(get_current_user)) -> dict:
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin account required")
    return user


def rate_limit(max_calls: int, per_seconds: int):
    """Per-IP sliding-window limit for endpoints that call paid AI models."""
    # ponytail: in-memory, per server instance; move to Redis/Upstash if abuse spans instances
    hits: dict[str, deque] = defaultdict(deque)

    async def check(request: Request) -> None:
        # Only trust a forwarded IP header that Vercel's edge sets itself.
        ip = (os.getenv("VERCEL") and request.headers.get("x-vercel-forwarded-for")) or (request.client.host if request.client else "?")
        now = time.monotonic()
        if len(hits) > 5000:  # bound memory: drop clients idle longer than the window
            for key in [k for k, w in hits.items() if not w or now - w[-1] > per_seconds]:
                del hits[key]
        window = hits[ip]
        while window and now - window[0] > per_seconds:
            window.popleft()
        if len(window) >= max_calls:
            raise HTTPException(status_code=429, detail="Too many requests, please try again shortly")
        window.append(now)

    return check
