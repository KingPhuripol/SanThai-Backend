"""
Auth for all three roles (artisan/designer/customer) — email/password
registration + login, backed by Supabase `users_profile` (+ `artisans` for
the artisan role only). Issues a JWT carrying the artisan_id (when
applicable) so downstream endpoints never need to trust a client-supplied id.
"""
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr

from app.api.deps import get_current_user
from app.core.security import create_access_token, hash_password, verify_password
from app.supabase_client import get_supabase

router = APIRouter()

VALID_ROLES = ("artisan", "designer", "customer")


class RegisterInput(BaseModel):
    role: str = "artisan"
    email: EmailStr
    password: str
    full_name: str
    community_name: Optional[str] = None
    province: Optional[str] = None
    region: Optional[str] = None
    bio_th: Optional[str] = None


class LoginInput(BaseModel):
    email: EmailStr
    password: str


class SessionOut(BaseModel):
    token: str
    user_id: str
    email: str
    full_name: str
    role: str
    artisan_id: Optional[int] = None
    artisan_name: Optional[str] = None


def _issue(user_id: str, email: str, full_name: str, role: str,
           artisan_id: Optional[int] = None, artisan_name: Optional[str] = None) -> SessionOut:
    token = create_access_token({
        "sub": user_id,
        "email": email,
        "full_name": full_name,
        "role": role,
        "artisan_id": artisan_id,
        "artisan_name": artisan_name,
    })
    return SessionOut(
        token=token, user_id=user_id, email=email, full_name=full_name,
        role=role, artisan_id=artisan_id, artisan_name=artisan_name,
    )


@router.post("/register", response_model=SessionOut)
async def register(input_data: RegisterInput):
    sb = get_supabase()

    if input_data.role not in VALID_ROLES:
        raise HTTPException(status_code=400, detail="Invalid role")
    if len(input_data.password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters")

    existing = sb.table("users_profile").select("id").eq("email", input_data.email).execute()
    if existing.data:
        raise HTTPException(status_code=400, detail="Email already registered")

    if input_data.role == "artisan":
        if not (input_data.community_name and input_data.province and input_data.region):
            raise HTTPException(
                status_code=400,
                detail="community_name, province, and region are required for store accounts",
            )

        user_id = str(uuid.uuid4())
        sb.table("users_profile").insert({
            "id": user_id,
            "email": input_data.email,
            "full_name": input_data.full_name,
            "role": "artisan",
            "password_hash": hash_password(input_data.password),
        }).execute()

        # Insert into Store instead of artisans
        store_res = sb.table("Store").insert({
            "name": input_data.community_name,
            "province": input_data.province,
            "region": input_data.region,
            "entrepreneur": user_id,  # Use entrepreneur field to store user_id for linkage
        }).execute()
        store = store_res.data[0]

        return _issue(user_id, input_data.email, input_data.full_name, "artisan",
                      store["id"], store["name"])

    # designer / customer — a plain users_profile row, no linked entity table
    user_id = str(uuid.uuid4())
    sb.table("users_profile").insert({
        "id": user_id,
        "email": input_data.email,
        "full_name": input_data.full_name,
        "role": input_data.role,
        "password_hash": hash_password(input_data.password),
    }).execute()

    return _issue(user_id, input_data.email, input_data.full_name, input_data.role)


@router.post("/login", response_model=SessionOut)
async def login(input_data: LoginInput):
    sb = get_supabase()
    res = sb.table("users_profile").select("*").eq("email", input_data.email).execute()
    if not res.data or not verify_password(input_data.password, res.data[0].get("password_hash") or ""):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    user = res.data[0]

    if user.get("is_suspended"):
        raise HTTPException(status_code=403, detail="บัญชีถูกระงับการใช้งาน")

    artisan_id = artisan_name = None
    if user["role"] == "artisan":
        art_res = sb.table("Store").select("id, name").eq("entrepreneur", user["id"]).execute()
        if art_res.data:
            artisan_id = art_res.data[0]["id"]
            artisan_name = art_res.data[0]["name"]

    return _issue(user["id"], user["email"], user.get("full_name") or "",
                  user["role"], artisan_id, artisan_name)


@router.get("/me", response_model=SessionOut)
async def me(user: dict = Depends(get_current_user)):
    return SessionOut(
        token="",
        user_id=user.get("sub", ""),
        email=user.get("email", ""),
        full_name=user.get("full_name", ""),
        role=user.get("role", ""),
        artisan_id=user.get("artisan_id"),
        artisan_name=user.get("artisan_name"),
    )
