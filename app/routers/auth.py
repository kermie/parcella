"""
Authentifizierungs-Router: Login, Logout, Einladungen.
"""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Request, Form, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db
from app.models import User, Invitation, InvitationStatus, UserRole
from app.auth import (
    verify_password, hash_password, create_session_token,
    verify_invitation_token, create_invitation_token, get_current_user, require_admin
)
from app.config import settings

router = APIRouter(prefix="/auth", tags=["auth"])
from app.templating import templates


@router.get("/login", response_class=HTMLResponse)
async def login_seite(request: Request, db: AsyncSession = Depends(get_db)):
    user = await get_current_user(request, db)
    if user:
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse("auth/login.html", {"request": request})


@router.post("/login")
async def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(User).where(User.email == email.lower()))
    user = result.scalar_one_or_none()

    if not user or not user.password_hash or not verify_password(password, user.password_hash):
        return templates.TemplateResponse(
            "auth/login.html",
            {"request": request, "fehler": "E-Mail oder Passwort falsch."},
            status_code=401,
        )

    if not user.is_active:
        return templates.TemplateResponse(
            "auth/login.html",
            {"request": request, "fehler": "Ihr Konto ist deaktiviert."},
            status_code=403,
        )

    # Letzten Login aktualisieren
    user.last_login = datetime.now(timezone.utc)
    await db.commit()

    token = create_session_token(user.id)
    response = RedirectResponse("/", status_code=302)
    response.set_cookie(
        "session",
        token,
        max_age=settings.session_max_age,
        httponly=True,
        samesite="lax",
        secure=not settings.is_development,
    )
    return response


@router.get("/logout")
async def logout():
    response = RedirectResponse("/auth/login", status_code=302)
    response.delete_cookie("session")
    return response


# ---------------------------------------------------------------------------
# Einladungssystem
# ---------------------------------------------------------------------------

@router.get("/invitation/{token}", response_class=HTMLResponse)
async def invitation_page(token: str, request: Request, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Invitation).where(
            Invitation.token == token,
            Invitation.status == InvitationStatus.PENDING,
        )
    )
    invitation = result.scalar_one_or_none()

    if not invitation or invitation.expires_at < datetime.now(timezone.utc):
        return templates.TemplateResponse(
            "auth/invitation_expired.html", {"request": request}
        )

    return templates.TemplateResponse(
        "auth/invitation.html",
        {"request": request, "token": token, "email": invitation.email},
    )


@router.post("/invitation/{token}")
async def invitation_accept(
    token: str,
    request: Request,
    name: str = Form(...),
    password: str = Form(...),
    password_confirm: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Invitation).where(
            Invitation.token == token,
            Invitation.status == InvitationStatus.PENDING,
        )
    )
    invitation = result.scalar_one_or_none()

    if not invitation or invitation.expires_at < datetime.now(timezone.utc):
        return templates.TemplateResponse(
            "auth/invitation_expired.html", {"request": request}
        )

    if password != password_confirm:
        return templates.TemplateResponse(
            "auth/invitation.html",
            {
                "request": request,
                "token": token,
                "email": invitation.email,
                "fehler": "Passwörter stimmen nicht überein.",
            },
        )

    if len(password) < 8:
        return templates.TemplateResponse(
            "auth/invitation.html",
            {
                "request": request,
                "token": token,
                "email": invitation.email,
                "fehler": "Passwort muss mindestens 8 Zeichen haben.",
            },
        )

    # Benutzer anlegen
    user = User(
        email=invitation.email.lower(),
        name=name,
        password_hash=hash_password(password),
        role=invitation.role,
    )
    db.add(user)

    invitation.status = InvitationStatus.ACCEPTED
    await db.commit()

    session_token = create_session_token(user.id)
    response = RedirectResponse("/", status_code=302)
    response.set_cookie(
        "session",
        session_token,
        max_age=settings.session_max_age,
        httponly=True,
        samesite="lax",
        secure=not settings.is_development,
    )
    return response
