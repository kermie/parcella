"""
JWT authentication for the REST API.

Separate from the web UI's cookie-based session authentication (see
app/auth.py). The API uses classic bearer tokens in the Authorization
header.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

from jose import jwt, JWTError
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.config import settings
from app.database import get_db
from app.models import User, UserRole
from app.auth import verify_password

JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_VALID_MINUTES = 60 * 24  # 24 Stunden

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/token", auto_error=False)


def create_access_token(user_id: str, email: str) -> str:
    expiry = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_VALID_MINUTES)
    payload = {
        "sub": user_id,
        "email": email,
        "exp": expiry,
        "iat": datetime.now(timezone.utc),
        "type": "access",
    }
    return jwt.encode(payload, settings.secret_key, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> Optional[dict]:
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[JWT_ALGORITHM])
        if payload.get("type") != "access":
            return None
        return payload
    except JWTError:
        return None


async def authenticate_user(db: AsyncSession, email: str, password: str) -> Optional[User]:
    result = await db.execute(select(User).where(User.email == email.lower()))
    user = result.scalar_one_or_none()
    if not user or not user.password_hash:
        return None
    if not verify_password(password, user.password_hash):
        return None
    if not user.is_active:
        return None
    return user


async def get_current_api_user(
    token: Optional[str] = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Dependency for protected API endpoints. Requires a valid bearer token."""
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or missing authentication",
        headers={"WWW-Authenticate": "Bearer"},
    )

    if not token:
        raise unauthorized

    payload = decode_access_token(token)
    if not payload:
        raise unauthorized

    user_id = payload.get("sub")
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if not user or not user.is_active:
        raise unauthorized

    return user


def require_api_role(*allowed_roles: UserRole):
    """Dependency factory: restricts endpoints to specific roles."""

    async def checker(user: User = Depends(get_current_api_user)) -> User:
        if user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"This action requires one of these roles: {', '.join(r.value for r in allowed_roles)}",
            )
        return user

    return checker


# Common combinations as ready-made dependencies
require_write_access = require_api_role(
    UserRole.ADMIN, UserRole.BOARD, UserRole.TREASURER
)
require_admin_api = require_api_role(UserRole.ADMIN, UserRole.BOARD)
