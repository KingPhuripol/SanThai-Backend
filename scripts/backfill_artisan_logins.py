"""
One-off, idempotent: creates a real login (users_profile + password) for every
existing `artisans` row that doesn't have one yet (artisans.user_id IS NULL),
so seeded/demo artisans can log in as themselves instead of sharing artisan_id=1.

Run from backend/: python backfill_artisan_logins.py
Prints the generated email/password for each account — save this output.
"""
import secrets
import uuid

from app.core.security import hash_password
from app.supabase_client import get_supabase


def main():
    sb = get_supabase()
    artisans = sb.table("artisans").select("id, name, user_id").order("id").execute().data or []

    to_create = [a for a in artisans if not a.get("user_id")]
    if not to_create:
        print("All artisans already have a login. Nothing to do.")
        return

    print(f"{'artisan_id':<12}{'name':<28}{'email':<32}{'password'}")
    print("-" * 90)
    for a in to_create:
        email = f"artisan{a['id']}@santhai.demo"
        password = secrets.token_urlsafe(6)
        user_id = str(uuid.uuid4())

        sb.table("users_profile").insert({
            "id": user_id,
            "email": email,
            "full_name": a["name"],
            "role": "artisan",
            "password_hash": hash_password(password),
        }).execute()
        sb.table("artisans").update({"user_id": user_id}).eq("id", a["id"]).execute()

        print(f"{a['id']:<12}{a['name']:<28}{email:<32}{password}")


if __name__ == "__main__":
    main()
