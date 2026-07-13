"""
Admin-Router: Benutzerverwaltung, Einladungen, Vereinseinstellungen.
"""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Request, Form, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db
from app.models import User, Invitation, InvitationStatus, UserRole, ClubSetting
from app.auth import require_admin, create_invitation_token, hash_password
from app.email_service import sende_email
from app.crypto_utils import verschluesseln
from app.i18n import AVAILABLE_LANGUAGES
from app.config import settings

router = APIRouter(prefix="/admin", tags=["admin"])
from app.templating import templates

INVITATION_DAYS = 7


@router.get("/", response_class=HTMLResponse)
async def admin_dashboard(request: Request, db: AsyncSession = Depends(get_db)):
    user = await require_admin(request, db)

    user_result = await db.execute(select(User).order_by(User.name))
    all_users = user_result.scalars().all()

    invitation_result = await db.execute(
        select(Invitation)
        .where(Invitation.status == InvitationStatus.PENDING)
        .order_by(Invitation.created_at.desc())
    )
    open_invitations = invitation_result.scalars().all()

    return templates.TemplateResponse(
        "admin/dashboard.html",
        {
            "request": request,
            "user": user,
            "all_users": all_users,
            "open_invitations": open_invitations,
            "UserRole": UserRole,
        },
    )


@router.post("/invite")
async def user_invite(
    request: Request,
    email: str = Form(...),
    role: str = Form("readonly"),
    db: AsyncSession = Depends(get_db),
):
    admin = await require_admin(request, db)

    email = email.strip().lower()

    # Bereits registriert?
    existing = await db.execute(select(User).where(User.email == email))
    if existing.scalar_one_or_none():
        return RedirectResponse("/admin/?fehler=E-Mail+bereits+registriert", status_code=302)

    if role not in [r.value for r in UserRole]:
        role = "readonly"

    token = create_invitation_token(email)
    expires_at = datetime.now(timezone.utc) + timedelta(days=INVITATION_DAYS)

    invitation = Invitation(
        email=email,
        token=token,
        role=UserRole(role),
        invited_by_id=admin.id,
        expires_at=expires_at,
    )
    db.add(invitation)
    await db.commit()

    # Link zusammenbauen
    base_url = str(request.base_url).rstrip("/")
    einladungslink = f"{base_url}/auth/einladung/{token}"

    betreff = f"Einladung zur {settings.app_name}"
    html = f"""
    <html><body style="font-family: sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
    <h2>Einladung zur {settings.app_name}</h2>
    <p>Sie wurden von <strong>{admin.name}</strong> eingeladen, der Verwaltungssoftware beizutreten.</p>
    <p>Klicken Sie auf den folgenden Link, um Ihr Konto einzurichten:</p>
    <p style="margin: 20px 0;">
        <a href="{einladungslink}" style="background: #2d6a4f; color: white; padding: 10px 20px;
           text-decoration: none; border-radius: 4px;">Einladung annehmen</a>
    </p>
    <p style="color: #666; font-size: 0.9em;">
        Dieser Link ist {INVITATION_DAYS} Tage gültig.<br>
        Falls der Button nicht funktioniert: {einladungslink}
    </p>
    </body></html>
    """

    email_gesendet = await sende_email(email, betreff, html, db=db)

    # Im Entwicklungsmodus: Link in der URL zurückgeben
    if settings.is_development and not email_gesendet:
        return RedirectResponse(
            f"/admin/?info=Einladungslink+%28Dev%29%3A+{einladungslink}", status_code=302
        )

    return RedirectResponse("/admin/?erfolg=Einladung+gesendet", status_code=302)


@router.post("/users/{user_id}/deactivate")
async def user_deactivate(
    user_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    admin = await require_admin(request, db)

    if user_id == admin.id:
        return RedirectResponse("/admin/?fehler=Eigenes+Konto+nicht+deaktivierbar", status_code=302)

    result = await db.execute(select(User).where(User.id == user_id))
    target = result.scalar_one_or_none()
    if target:
        target.is_active = not target.is_active
        await db.commit()

    return RedirectResponse("/admin/", status_code=302)


# ---------------------------------------------------------------------------
# Vereinseinstellungen
# ---------------------------------------------------------------------------

SETTINGS_FIELDS = [
    ("verein_name", "Name des Vereins"),
    ("verein_strasse", "Straße"),
    ("verein_plz", "PLZ"),
    ("verein_ort", "Ort"),
    ("vereinsnummer", "Vereinsnummer"),
    ("registergericht", "Registergericht"),
    ("flaeche_gesamt_qm", "Gesamtfläche (qm)"),
    ("flaeche_a_qm", "A-Fläche (Stadt, qm)"),
    ("flaeche_b_qm", "B-Fläche (Gemeinschaft, qm)"),
    ("flaeche_c_qm", "C-Fläche (Parzellen, qm)"),
    ("smtp_host", "SMTP-Server"),
    ("smtp_port", "SMTP-Port"),
    ("smtp_user", "SMTP-Benutzer"),
    ("smtp_password", "SMTP-Passwort"),
    ("smtp_from", "Absender-E-Mail"),
    ("imap_host", "IMAP-Server (für Ticket-Postfach)"),
    ("imap_port", "IMAP-Port"),
    ("imap_ssl", "IMAP SSL (true/false)"),
    ("spam_domain_blocklist", "Spam: gesperrte Absender-Domains (kommagetrennt)"),
    ("spam_keyword_blocklist", "Spam: gesperrte Schlüsselwörter (kommagetrennt)"),
    ("spam_schwellenwert", "Spam: Schwellenwert (0.0–1.0, Standard 0.5)"),
    ("spam_api_url", "Spam: externe Prüf-API-URL (optional)"),
    ("spam_api_key", "Spam: externer API-Key (optional)"),
]

# Optionale Funktionsbereiche, die sich pro Verein ein-/ausschalten lassen.
# Schlüssel folgen der Konvention "modul_<n>" (siehe app/module_flags.py).
MODULE_FELDER = [
    ("modul_work_hours", "Pflichtstunden-Verwaltung",
     "Arbeitseinsätze, Patenschaften, Vereinsrollen und Jahresauswertung. "
     "Deaktivieren, falls der Verein keine Pflichtstunden erhebt."),
    ("modul_water", "Wasserverwaltung",
     "Wasserzähler, Zählerstände und Verbrauchsauswertung. "
     "Deaktivieren, falls der Verein keine eigene Wasserversorgung verwaltet."),
    ("modul_electricity", "Stromverwaltung",
     "Stromzähler, Zählerstände und Verbrauchsauswertung. "
     "Deaktivieren, falls der Verein keine eigene Stromversorgung verwaltet."),
    ("modul_insurance", "Versicherungsverwaltung",
     "Sach- und Unfallversicherung pro Parcel inkl. Jahresauswertung. "
     "Deaktivieren, falls der Verein keine Versicherungen über das Programm abwickelt."),
    ("modul_tickets", "Ticketsystem",
     "Support-Tickets mit Zuweisung, Status und Member-Zuordnung. "
     "Deaktivieren, falls der Verein kein internes Ticketsystem nutzt."),
    ("modul_purchase_requests", "Einkaufswünsche",
     "Vier-Augen-Prinzip für Vereinsausgaben: Anträge stellen, von zwei "
     "Vorstandsmitgliedern freigeben oder ablehnen lassen."),
]


@router.get("/settings", response_class=HTMLResponse)
async def einstellungen_seite(request: Request, db: AsyncSession = Depends(get_db)):
    user = await require_admin(request, db)

    result = await db.execute(select(ClubSetting))
    settings_map = {e.key: e.value for e in result.scalars().all()}

    return templates.TemplateResponse(
        "admin/einstellungen.html",
        {
            "request": request,
            "user": user,
            "einstellungen": settings_map,
            "felder": SETTINGS_FIELDS,
            "module_felder": MODULE_FELDER,
            "available_languages": AVAILABLE_LANGUAGES,
        },
    )


@router.post("/settings")
async def einstellungen_speichern(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    await require_admin(request, db)
    form = await request.form()

    for key, description in SETTINGS_FIELDS:
        value = form.get(key, "").strip() or None

        result = await db.execute(
            select(ClubSetting).where(ClubSetting.key == key)
        )
        entry = result.scalar_one_or_none()

        if key.endswith("_password") or key.endswith("_api_key"):
            # Leeres Feld = "unverändert lassen" (wie im Platzhaltertext
            # versprochen), damit man nicht bei jedem Speichern das
            # Passwort neu eintippen muss. Nur ein NEUER Wert wird
            # verschlüsselt gespeichert.
            if not value:
                continue
            value = verschluesseln(value)

        if entry:
            entry.value = value
        else:
            db.add(ClubSetting(
                key=key,
                value=value,
                description=description,
            ))

    # Modul-Umschalter: Checkboxen senden bei "aus" gar keinen Wert im
    # Formular, daher explizit "true"/"false" statt nur form.get(...).
    for key, description, _hinweis in MODULE_FELDER:
        value = "true" if key in form else "false"

        result = await db.execute(
            select(ClubSetting).where(ClubSetting.key == key)
        )
        entry = result.scalar_one_or_none()

        if entry:
            entry.value = value
        else:
            db.add(ClubSetting(
                key=key,
                value=value,
                description=description,
            ))

    # Sprache: eigenes Feld (Dropdown, kein Freitext) – gegen die Liste
    # bekannter Sprachen validiert, damit kein ungültiger Code landen kann,
    # für den es keine Übersetzungsdatei gibt.
    language_value = form.get("language", "").strip()
    if language_value in AVAILABLE_LANGUAGES:
        result = await db.execute(select(ClubSetting).where(ClubSetting.key == "language"))
        entry = result.scalar_one_or_none()
        if entry:
            entry.value = language_value
        else:
            db.add(ClubSetting(key="language", value=language_value, description="Sprache der Oberfläche"))

    await db.commit()
    return RedirectResponse("/admin/settings?erfolg=1", status_code=302)
