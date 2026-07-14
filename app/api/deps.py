"""
Shared FastAPI auth dependencies. Import from here in fabrics.py / products.py /
artisan.py / designer.py to avoid duplicating token-decoding logic.
"""
from typing import Optional

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError

from app.core.security import decode_access_token

bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    creds: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> dict:
    if creds is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        return decode_access_token(creds.credentials)
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")


async def get_optional_user(
    creds: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> Optional[dict]:
    """Soft auth: never raises. Used where a request may or may not carry a
    token (e.g. product upload, reachable both by a logged-in designer and by
    an artisan's own auto-listing flow, which attaches its own artisan token)."""
    if creds is None:
        return None
    try:
        return decode_access_token(creds.credentials)
    except JWTError:
        return None


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
