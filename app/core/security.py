"""
Password hashing (bcrypt) + JWT issuance/verification for artisan auth.

Uses the `bcrypt` module directly rather than passlib's CryptContext —
passlib 1.7.4's bcrypt backend probes `bcrypt.__about__.__version__`,
which was removed in bcrypt>=4.1 (the version pinned in requirements.txt).
"""
from datetime import datetime, timedelta, timezone

import bcrypt
from jose import jwt

from app.config import settings

ALGORITHM = "HS256"
EXPIRE_MINUTES = 60 * 24 * 7  # 7 days — no refresh tokens for the hackathon demo


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    if not hashed:
        return False
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        return False


def create_access_token(data: dict) -> str:
    to_encode = data.copy()
    to_encode["exp"] = datetime.now(timezone.utc) + timedelta(minutes=EXPIRE_MINUTES)
    return jwt.encode(to_encode, settings.secret_key, algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict:
    return jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
