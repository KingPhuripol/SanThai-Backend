"""
One-off, idempotent: creates the single admin account for the platform.
There is no self-signup path for role="admin" (see auth.py's VALID_ROLES).

Run from backend/: python seed_admin.py
Prints the email/password to use for login — save this output.
"""
import secrets
import uuid

from app.core.security import hash_password
from app.supabase_client import get_supabase

ADMIN_EMAIL = "admin@santhai.demo"


def main():
    sb = get_supabase()

    existing = sb.table("users_profile").select("id").eq("email", ADMIN_EMAIL).execute()
    if existing.data:
        print(f"Admin account already exists: {ADMIN_EMAIL}")
        return

    password = secrets.token_urlsafe(9)
    user_id = str(uuid.uuid4())

    sb.table("users_profile").insert({
        "id": user_id,
        "email": ADMIN_EMAIL,
        "full_name": "SanThai Admin",
        "role": "admin",
        "password_hash": hash_password(password),
    }).execute()

    print("Admin account created.")
    print(f"email:    {ADMIN_EMAIL}")
    print(f"password: {password}")


if __name__ == "__main__":
    main()
