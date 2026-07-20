"""
Auth for all three roles (artisan/designer/customer) — email/password
registration + login, backed by Supabase `users_profile` (+ `artisans` for
the artisan role only). Issues a JWT carrying the artisan_id (when
applicable) so downstream endpoints never need to trust a client-supplied id.
"""
import os
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr

from app.api.deps import get_current_user
from app.core.security import create_access_token, hash_password, verify_password
from app.supabase_client import get_supabase

router = APIRouter()

VALID_ROLES = ("artisan", "designer", "customer")
TERMS_VERSION = "2026-07-18"
PRIVACY_VERSION = "2026-07-18"
STORE_TERMS_VERSION = "2026-07-18-store-v1"
ALLOW_STORE_SELF_REGISTRATION = os.getenv("ALLOW_STORE_SELF_REGISTRATION", "true").lower() == "true"


class RegisterInput(BaseModel):
    role: str = "artisan"
    email: EmailStr
    password: str
    full_name: str
    community_name: Optional[str] = None
    province: Optional[str] = None
    region: Optional[str] = None
    bio_th: Optional[str] = None
    accept_terms: bool = False
    accept_privacy: bool = False


class LoginInput(BaseModel):
    email: EmailStr
    password: str


class StoreTermsAcceptanceInput(BaseModel):
    accepted: bool = False


class ProfileUpdateInput(BaseModel):
    full_name: Optional[str] = None
    email: Optional[EmailStr] = None
    password: Optional[str] = None
    community_name: Optional[str] = None


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
    if input_data.role == "artisan" and not ALLOW_STORE_SELF_REGISTRATION:
        raise HTTPException(
            status_code=403,
            detail="การเปิดร้านค้าอยู่ระหว่างเปิดรับแบบคำเชิญจาก SanThai เท่านั้น",
        )
    if len(input_data.password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters")
    if not input_data.accept_terms or not input_data.accept_privacy:
        raise HTTPException(status_code=400, detail="Terms of Service and Privacy Notice must be accepted")

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

        community_res = (
            sb.table("communities")
            .select("id")
            .eq("name", input_data.community_name)
            .eq("province", input_data.province)
            .execute()
        )
        if community_res.data:
            community_id = community_res.data[0]["id"]
        else:
            created_community = sb.table("communities").insert({
                "name": input_data.community_name,
                "province": input_data.province,
                "region": input_data.region,
            }).execute()
            community_id = created_community.data[0]["id"]

        artisan_res = sb.table("artisans").insert({
            "user_id": user_id,
            "name": input_data.full_name,
            "community_id": community_id,
            "bio_th": input_data.bio_th,
        }).execute()
        artisan = artisan_res.data[0]

        sb.table("legal_acceptances").insert([
            {"user_id": user_id, "document_type": "terms", "document_version": TERMS_VERSION},
            {"user_id": user_id, "document_type": "privacy", "document_version": PRIVACY_VERSION},
        ]).execute()

        return _issue(user_id, input_data.email, input_data.full_name, "artisan",
                      artisan["id"], artisan["name"])

    # designer / customer — a plain users_profile row, no linked entity table
    user_id = str(uuid.uuid4())
    sb.table("users_profile").insert({
        "id": user_id,
        "email": input_data.email,
        "full_name": input_data.full_name,
        "role": input_data.role,
        "password_hash": hash_password(input_data.password),
    }).execute()
    sb.table("legal_acceptances").insert([
        {"user_id": user_id, "document_type": "terms", "document_version": TERMS_VERSION},
        {"user_id": user_id, "document_type": "privacy", "document_version": PRIVACY_VERSION},
    ]).execute()

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
        art_res = sb.table("artisans").select("id, name").eq("user_id", user["id"]).execute()
        if art_res.data:
            artisan_id = art_res.data[0]["id"]
            artisan_name = art_res.data[0]["name"]

    return _issue(user["id"], user["email"], user.get("full_name") or "",
                  user["role"], artisan_id, artisan_name)


@router.get("/store-terms-status")
async def store_terms_status(user: dict = Depends(get_current_user)):
    """Return the gate status for an invite-only store owner.

    Store terms use the existing versioned legal_acceptances table with a
    dedicated context, so account terms and store agreement remain distinct.
    """
    if user.get("role") != "artisan" or not user.get("artisan_id"):
        raise HTTPException(status_code=403, detail="Store account required")
    sb = get_supabase()
    artisan = (
        sb.table("artisans")
        .select("id, store_status, verified_at, store_terms_version, store_terms_accepted_at")
        .eq("id", user["artisan_id"])
        .single()
        .execute()
    ).data
    if not artisan:
        raise HTTPException(status_code=404, detail="Store not found")
    accepted = (
        artisan.get("store_terms_version") == STORE_TERMS_VERSION
        and bool(artisan.get("store_terms_accepted_at"))
    )
    return {
        "accepted": accepted,
        "version": STORE_TERMS_VERSION,
        "store_status": artisan.get("store_status"),
        "verified": bool(artisan.get("verified_at")),
    }


@router.post("/store-terms-acceptance")
async def accept_store_terms(
    payload: StoreTermsAcceptanceInput,
    request: Request,
    user: dict = Depends(get_current_user),
):
    if user.get("role") != "artisan" or not user.get("artisan_id"):
        raise HTTPException(status_code=403, detail="Store account required")
    if not payload.accepted:
        raise HTTPException(status_code=422, detail="Store Terms of Service must be accepted")
    sb = get_supabase()
    now = datetime.now(timezone.utc).isoformat()
    sb.table("artisans").update({
        "store_terms_version": STORE_TERMS_VERSION,
        "store_terms_accepted_at": now,
    }).eq("id", user["artisan_id"]).execute()
    sb.table("legal_acceptances").upsert({
        "user_id": user["sub"],
        "document_type": "terms",
        "document_version": STORE_TERMS_VERSION,
        "acceptance_context": "store",
        "accepted_by_name": user.get("full_name"),
        "accepted_ip": request.client.host if request.client else None,
        "accepted_user_agent": request.headers.get("user-agent"),
    }, on_conflict="user_id,document_type,document_version").execute()
    return {"accepted": True, "version": STORE_TERMS_VERSION, "accepted_at": now}


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


@router.put("/profile", response_model=SessionOut)
async def update_profile(input_data: ProfileUpdateInput, user: dict = Depends(get_current_user)):
    sb = get_supabase()
    user_id = user["sub"]
    
    if input_data.email and input_data.email != user.get("email"):
        existing = sb.table("users_profile").select("id").eq("email", input_data.email).execute()
        if existing.data:
            raise HTTPException(status_code=400, detail="Email already taken")
            
    update_data = {}
    if input_data.full_name is not None:
        update_data["full_name"] = input_data.full_name
    if input_data.email is not None:
        update_data["email"] = input_data.email
    if input_data.password:
        update_data["password_hash"] = hash_password(input_data.password)
        
    if update_data:
        sb.table("users_profile").update(update_data).eq("id", user_id).execute()
        
    updated_artisan_name = user.get("artisan_name")
    if user.get("role") == "artisan" and user.get("artisan_id"):
        artisan_updates = {}
        if input_data.full_name is not None:
            artisan_updates["name"] = input_data.full_name
            updated_artisan_name = input_data.full_name
            
        if input_data.community_name is not None:
            artisan_res = sb.table("artisans").select("community_id").eq("id", user["artisan_id"]).execute()
            if artisan_res.data:
                community_id = artisan_res.data[0]["community_id"]
                if community_id:
                    sb.table("communities").update({"name": input_data.community_name}).eq("id", community_id).execute()
                    
        if artisan_updates:
            sb.table("artisans").update(artisan_updates).eq("id", user["artisan_id"]).execute()

    res = sb.table("users_profile").select("*").eq("id", user_id).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="User not found")
        
    updated_user = res.data[0]
    
    return _issue(
        user_id=updated_user["id"],
        email=updated_user["email"],
        full_name=updated_user.get("full_name") or "",
        role=updated_user["role"],
        artisan_id=user.get("artisan_id"),
        artisan_name=updated_artisan_name,
    )
