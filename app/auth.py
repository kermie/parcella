"""
Authentifizierung: Passwort-Hashing, Sessions, Einladungstoken.
"""
import secrets
import bcrypt
from datetime import datetime, timedelta, timezone
from typing import Optional

from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from fastapi import Request, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.config import settings
from app.models import User

serializer = URLSafeTimedSerializer(settings.secret_key)

INVITATION_VALID_DAYS = 7


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())


def create_invitation_token(email: str) -> str:
    # secrets.token_urlsafe sorgt dafür, dass zwei Einladungen an dieselbe
    # Adresse innerhalb derselben Sekunde garantiert unterschiedliche Tokens
    # bekommen (itsdangerous' eigener Zeitstempel hat nur Sekunden-Genauigkeit
    # und wäre bei reinem email+Zeitstempel sonst deterministisch identisch).
    nonce = secrets.token_urlsafe(8)
    return serializer.dumps(f"{email}:{nonce}", salt="einladung")


def verify_invitation_token(token: str, max_age: int = INVITATION_VALID_DAYS * 86400) -> Optional[str]:
    try:
        payload = serializer.loads(token, salt="einladung", max_age=max_age)
        email, _, _nonce = payload.rpartition(":")
        return email or payload  # Rückwärtskompatibel mit älteren Tokens ohne Nonce
    except (BadSignature, SignatureExpired):
        return None


def create_session_token(user_id: str) -> str:
    return serializer.dumps(user_id, salt="session")


def verify_session_token(token: str) -> Optional[str]:
    try:
        user_id = serializer.loads(
            token, salt="session", max_age=settings.session_max_age
        )
        return user_id
    except (BadSignature, SignatureExpired):
        return None


async def get_current_user(request: Request, db: AsyncSession) -> Optional[User]:
    token = request.cookies.get("session")
    if not token:
        return None
    user_id = verify_session_token(token)
    if not user_id:
        return None
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user and not user.is_active:
        return None
    return user


async def require_user(request: Request, db: AsyncSession) -> User:
    user = await get_current_user(request, db)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": "/auth/login"}
        )
    return user


async def require_admin(request: Request, db: AsyncSession) -> User:
    from app.models import UserRole
    user = await require_user(request, db)
    if user.role not in (UserRole.ADMIN, UserRole.BOARD):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Keine Berechtigung")
    return user
