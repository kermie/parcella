"""
Admin-Router: Benutzerverwaltung, Einladungen, Vereinseinstellungen.
"""
from datetime import datetime, timedelta, timezone
import urllib.parse

from fastapi import APIRouter, Request, Form, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db
from app.models import User, Invitation, InvitationStatus, UserRole, ClubSetting
from app.auth import require_admin, create_invitation_token, hash_password
from app.email_service import sende_email
from app.crypto_utils import verschluesseln
from app.i18n import AVAILABLE_LANGUAGES, t_for
from app.l10n import AVAILABLE_REGIONS, AVAILABLE_CURRENCIES
from app.branding import save_logo_upload, remove_logo_file
from app.config import settings
from app.public_api_auth import get_or_create_public_api_token, regenerate_public_api_token

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
        return RedirectResponse(
            f"/admin/?fehler={urllib.parse.quote(t_for(request, 'errors.email_already_registered'))}",
            status_code=302,
        )

    # Bereits eine ausstehende Einladung für diese Adresse? Dann die alte
    # ungültig machen, statt eine zweite parallel bestehen zu lassen (sonst
    # kollidiert der neue Token in seltenen Fällen mit dem alten, wenn beide
    # innerhalb derselben Sekunde erzeugt werden, und der Insert schlägt mit
    # einem harten Datenbankfehler fehl statt einer verständlichen Meldung).
    pending = await db.execute(
        select(Invitation).where(
            Invitation.email == email,
            Invitation.status == InvitationStatus.PENDING,
        )
    )
    for old_invitation in pending.scalars().all():
        old_invitation.status = InvitationStatus.EXPIRED

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
    einladungslink = f"{base_url}/auth/invitation/{token}"

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

    return RedirectResponse(
        f"/admin/?erfolg={urllib.parse.quote(t_for(request, 'errors.invitation_sent'))}",
        status_code=302,
    )


@router.post("/users/{user_id}/deactivate")
async def user_deactivate(
    user_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    admin = await require_admin(request, db)

    if user_id == admin.id:
        return RedirectResponse(
            f"/admin/?fehler={urllib.parse.quote(t_for(request, 'errors.own_account_cannot_deactivate'))}",
            status_code=302,
        )

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
    ("verein_name", "admin.settings.fields.club_name"),
    ("verein_strasse", "admin.settings.fields.street"),
    ("verein_plz", "admin.settings.fields.postal_code"),
    ("verein_ort", "admin.settings.fields.city"),
    ("vereinsnummer", "admin.settings.fields.club_number"),
    ("registergericht", "admin.settings.fields.register_court"),
    ("flaeche_gesamt_qm", "admin.settings.fields.total_area"),
    ("flaeche_a_qm", "admin.settings.fields.area_a"),
    ("flaeche_b_qm", "admin.settings.fields.area_b"),
    ("flaeche_c_qm", "admin.settings.fields.area_c"),
    ("smtp_host", "admin.settings.fields.smtp_host"),
    ("smtp_port", "admin.settings.fields.smtp_port"),
    ("smtp_user", "admin.settings.fields.smtp_user"),
    ("smtp_password", "admin.settings.fields.smtp_password"),
    ("smtp_from", "admin.settings.fields.sender_email"),
    ("imap_host", "admin.settings.fields.imap_host"),
    ("imap_port", "admin.settings.fields.imap_port"),
    ("imap_ssl", "admin.settings.fields.imap_ssl"),
    ("spam_domain_blocklist", "admin.settings.fields.spam_domain_blocklist"),
    ("spam_keyword_blocklist", "admin.settings.fields.spam_keyword_blocklist"),
    ("spam_schwellenwert", "admin.settings.fields.spam_threshold"),
    ("spam_api_url", "admin.settings.fields.spam_api_url"),
    ("spam_api_key", "admin.settings.fields.spam_api_key"),
]

# Optionale Funktionsbereiche, die sich pro Verein ein-/ausschalten lassen.
# Schlüssel folgen der Konvention "modul_<n>" (siehe app/module_flags.py).
# Name/Beschreibung werden über Übersetzungsschlüssel aufgelöst (siehe unten).
MODULE_FELDER = [
    ("modul_work_hours", "admin.settings.modules.work_hours_name", "admin.settings.modules.work_hours_desc"),
    ("modul_water", "admin.settings.modules.water_name", "admin.settings.modules.water_desc"),
    ("modul_electricity", "admin.settings.modules.electricity_name", "admin.settings.modules.electricity_desc"),
    ("modul_insurance", "admin.settings.modules.insurance_name", "admin.settings.modules.insurance_desc"),
    ("modul_tickets", "admin.settings.modules.tickets_name", "admin.settings.modules.tickets_desc"),
    ("modul_purchase_requests", "admin.settings.modules.purchase_requests_name", "admin.settings.modules.purchase_requests_desc"),
    ("modul_calendar", "admin.settings.modules.calendar_name", "admin.settings.modules.calendar_desc"),
    ("modul_public_signup_api", "admin.settings.modules.public_signup_api_name", "admin.settings.modules.public_signup_api_desc"),
    ("modul_announcements", "admin.settings.modules.announcements_name", "admin.settings.modules.announcements_desc"),
]


@router.get("/settings", response_class=HTMLResponse)
async def einstellungen_seite(request: Request, db: AsyncSession = Depends(get_db)):
    user = await require_admin(request, db)

    result = await db.execute(select(ClubSetting))
    settings_map = {e.key: e.value for e in result.scalars().all()}

    resolved_felder = [(key, t_for(request, label_key)) for key, label_key in SETTINGS_FIELDS]
    resolved_module_felder = [
        (key, t_for(request, name_key), t_for(request, desc_key))
        for key, name_key, desc_key in MODULE_FELDER
    ]

    return templates.TemplateResponse(
        "admin/settings.html",
        {
            "request": request,
            "user": user,
            "einstellungen": settings_map,
            "felder": resolved_felder,
            "module_felder": resolved_module_felder,
            "available_languages": AVAILABLE_LANGUAGES,
            "available_regions": AVAILABLE_REGIONS,
            "available_currencies": AVAILABLE_CURRENCIES,
        },
    )


@router.post("/settings")
async def einstellungen_speichern(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    await require_admin(request, db)
    form = await request.form()

    # Logo: hochladen, entfernen, oder unverändert lassen (kein Feld in
    # SETTINGS_FIELDS, da UploadFile eine Datei statt einem Textwert ist).
    logo_error = None
    remove_logo = form.get("remove_logo", "") == "true"
    logo_upload = form.get("logo")

    if remove_logo:
        remove_logo_file()
        result = await db.execute(select(ClubSetting).where(ClubSetting.key == "logo_filename"))
        entry = result.scalar_one_or_none()
        if entry:
            await db.delete(entry)
    elif logo_upload is not None and getattr(logo_upload, "filename", ""):
        try:
            filename = await save_logo_upload(logo_upload)
            result = await db.execute(select(ClubSetting).where(ClubSetting.key == "logo_filename"))
            entry = result.scalar_one_or_none()
            if entry:
                entry.value = filename
            else:
                db.add(ClubSetting(key="logo_filename", value=filename, description="Uploaded club logo filename"))
        except ValueError as e:
            logo_error = str(e)

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

    # Region und Währung: bewusst getrennt von der Sprache (siehe
    # app/l10n.py) -- eigene Felder, ebenfalls gegen bekannte Werte
    # validiert.
    region_value = form.get("region", "").strip()
    if region_value in AVAILABLE_REGIONS:
        result = await db.execute(select(ClubSetting).where(ClubSetting.key == "region"))
        entry = result.scalar_one_or_none()
        if entry:
            entry.value = region_value
        else:
            db.add(ClubSetting(key="region", value=region_value, description="Region (Zahlen-/Adressformat)"))

    currency_value = form.get("currency", "").strip()
    if currency_value in AVAILABLE_CURRENCIES:
        result = await db.execute(select(ClubSetting).where(ClubSetting.key == "currency"))
        entry = result.scalar_one_or_none()
        if entry:
            entry.value = currency_value
        else:
            db.add(ClubSetting(key="currency", value=currency_value, description="Währung"))

    await db.commit()
    if logo_error:
        return RedirectResponse(f"/admin/settings?logo_error={logo_error}", status_code=302)
    return RedirectResponse("/admin/settings?erfolg=1", status_code=302)


# ---------------------------------------------------------------------------
# Integrations: public signup API for external CMS connectors (WordPress,
# TYPO3, Contao, ...). See docs/module-public-api.md and app/routers/api_public.py.
# A dedicated page rather than a field on the settings page, matching the
# calendar module's ICS-token hub -- a shared secret is sensitive enough to
# warrant its own explicit "yes, show/regenerate this" screen.
# ---------------------------------------------------------------------------

@router.get("/integrations", response_class=HTMLResponse)
async def integrations_seite(request: Request, db: AsyncSession = Depends(get_db)):
    user = await require_admin(request, db)
    token = await get_or_create_public_api_token(db)

    result = await db.execute(select(ClubSetting).where(ClubSetting.key == "modul_public_signup_api"))
    entry = result.scalar_one_or_none()
    modul_aktiv = (entry.value.strip().lower() in ("true", "1", "ja", "an")) if entry else False

    return templates.TemplateResponse("admin/integrations.html", {
        "request": request, "user": user,
        "api_token": token,
        "modul_aktiv": modul_aktiv,
        "base_url": str(request.base_url).rstrip("/"),
    })


@router.post("/integrations/regenerate-token")
async def integrations_token_neu(request: Request, db: AsyncSession = Depends(get_db)):
    await require_admin(request, db)
    await regenerate_public_api_token(db)
    return RedirectResponse("/admin/integrations?erfolg=1", status_code=302)
