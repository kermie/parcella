"""
Authentifizierungs-Router: Login, Logout, Einladungen.
"""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Request, Form, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db
from app.models import Benutzer, Einladung, EinladungStatus, BenutzerRolle
from app.auth import (
    verify_passwort, hash_passwort, erstelle_session_token,
    pruefe_einladungstoken, erstelle_einladungstoken, get_current_user, require_admin
)
from app.config import settings

router = APIRouter(prefix="/auth", tags=["auth"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/login", response_class=HTMLResponse)
async def login_seite(request: Request, db: AsyncSession = Depends(get_db)):
    benutzer = await get_current_user(request, db)
    if benutzer:
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse("auth/login.html", {"request": request})


@router.post("/login")
async def login(
    request: Request,
    email: str = Form(...),
    passwort: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Benutzer).where(Benutzer.email == email.lower()))
    benutzer = result.scalar_one_or_none()

    if not benutzer or not benutzer.passwort_hash or not verify_passwort(passwort, benutzer.passwort_hash):
        return templates.TemplateResponse(
            "auth/login.html",
            {"request": request, "fehler": "E-Mail oder Passwort falsch."},
            status_code=401,
        )

    if not benutzer.ist_aktiv:
        return templates.TemplateResponse(
            "auth/login.html",
            {"request": request, "fehler": "Ihr Konto ist deaktiviert."},
            status_code=403,
        )

    # Letzten Login aktualisieren
    benutzer.letzter_login = datetime.now(timezone.utc)
    await db.commit()

    token = erstelle_session_token(benutzer.id)
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

@router.get("/einladung/{token}", response_class=HTMLResponse)
async def einladung_seite(token: str, request: Request, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Einladung).where(
            Einladung.token == token,
            Einladung.status == EinladungStatus.AUSSTEHEND,
        )
    )
    einladung = result.scalar_one_or_none()

    if not einladung or einladung.gueltig_bis < datetime.now(timezone.utc):
        return templates.TemplateResponse(
            "auth/einladung_abgelaufen.html", {"request": request}
        )

    return templates.TemplateResponse(
        "auth/einladung.html",
        {"request": request, "token": token, "email": einladung.email},
    )


@router.post("/einladung/{token}")
async def einladung_annehmen(
    token: str,
    request: Request,
    name: str = Form(...),
    passwort: str = Form(...),
    passwort_wdh: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Einladung).where(
            Einladung.token == token,
            Einladung.status == EinladungStatus.AUSSTEHEND,
        )
    )
    einladung = result.scalar_one_or_none()

    if not einladung or einladung.gueltig_bis < datetime.now(timezone.utc):
        return templates.TemplateResponse(
            "auth/einladung_abgelaufen.html", {"request": request}
        )

    if passwort != passwort_wdh:
        return templates.TemplateResponse(
            "auth/einladung.html",
            {
                "request": request,
                "token": token,
                "email": einladung.email,
                "fehler": "Passwörter stimmen nicht überein.",
            },
        )

    if len(passwort) < 8:
        return templates.TemplateResponse(
            "auth/einladung.html",
            {
                "request": request,
                "token": token,
                "email": einladung.email,
                "fehler": "Passwort muss mindestens 8 Zeichen haben.",
            },
        )

    # Benutzer anlegen
    benutzer = Benutzer(
        email=einladung.email.lower(),
        name=name,
        passwort_hash=hash_passwort(passwort),
        rolle=einladung.rolle,
    )
    db.add(benutzer)

    einladung.status = EinladungStatus.ANGENOMMEN
    await db.commit()

    session_token = erstelle_session_token(benutzer.id)
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
