"""
API-Router: Authentifizierung (JWT-Token-Ausgabe).
"""
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.api_auth import authenticate_user, create_access_token, ACCESS_TOKEN_VALID_MINUTES, get_current_api_user
from app.schemas import TokenResponse, LoginRequest, UserOut
from app.models import User

router = APIRouter(prefix="/api/v1/auth", tags=["API: Auth"])


@router.post(
    "/token",
    response_model=TokenResponse,
    summary="Request access token",
    description=(
        "Authenticates with email and password and returns a JWT bearer token. "
        "Compatible with the OAuth2 password flow (for the Swagger UI \"Authorize\" button) AND "
        "with a JSON body (for programmatic clients, see /api/v1/auth/login)."
    ),
)
async def token_anfordern(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db),
):
    user = await authenticate_user(db, form_data.username, form_data.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="E-Mail oder Passwort falsch, oder Konto deaktiviert.",
        )
    token = create_access_token(user.id, user.email)
    return TokenResponse(access_token=token, expires_in_minutes=ACCESS_TOKEN_VALID_MINUTES)


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Request access token (JSON)",
    description="Like /token, but with a JSON body instead of form data -- more convenient for most HTTP clients.",
)
async def login_json(
    daten: LoginRequest,
    db: AsyncSession = Depends(get_db),
):
    user = await authenticate_user(db, daten.email, daten.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="E-Mail oder Passwort falsch, oder Konto deaktiviert.",
        )
    token = create_access_token(user.id, user.email)
    return TokenResponse(access_token=token, expires_in_minutes=ACCESS_TOKEN_VALID_MINUTES)


@router.get(
    "/me",
    response_model=UserOut,
    summary="Retrieve own user profile",
)
async def eigenes_profil(user: User = Depends(get_current_api_user)):
    return user
