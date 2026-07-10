"""
Admin-Router: Benutzerverwaltung, Einladungen, Vereinseinstellungen.
"""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Request, Form, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db
from app.models import Benutzer, Einladung, EinladungStatus, BenutzerRolle, Vereinseinstellung
from app.auth import require_admin, erstelle_einladungstoken, hash_passwort
from app.email_service import sende_email
from app.crypto_utils import verschluesseln
from app.config import settings

router = APIRouter(prefix="/admin", tags=["admin"])
templates = Jinja2Templates(directory="app/templates")

EINLADUNG_TAGE = 7


@router.get("/", response_class=HTMLResponse)
async def admin_dashboard(request: Request, db: AsyncSession = Depends(get_db)):
    benutzer = await require_admin(request, db)

    benutzer_result = await db.execute(select(Benutzer).order_by(Benutzer.name))
    alle_benutzer = benutzer_result.scalars().all()

    einladung_result = await db.execute(
        select(Einladung)
        .where(Einladung.status == EinladungStatus.AUSSTEHEND)
        .order_by(Einladung.created_at.desc())
    )
    offene_einladungen = einladung_result.scalars().all()

    return templates.TemplateResponse(
        "admin/dashboard.html",
        {
            "request": request,
            "benutzer": benutzer,
            "alle_benutzer": alle_benutzer,
            "offene_einladungen": offene_einladungen,
            "BenutzerRolle": BenutzerRolle,
        },
    )


@router.post("/einladen")
async def benutzer_einladen(
    request: Request,
    email: str = Form(...),
    rolle: str = Form("lesend"),
    db: AsyncSession = Depends(get_db),
):
    admin = await require_admin(request, db)

    email = email.strip().lower()

    # Bereits registriert?
    existing = await db.execute(select(Benutzer).where(Benutzer.email == email))
    if existing.scalar_one_or_none():
        return RedirectResponse("/admin/?fehler=E-Mail+bereits+registriert", status_code=302)

    if rolle not in [r.value for r in BenutzerRolle]:
        rolle = "lesend"

    token = erstelle_einladungstoken(email)
    gueltig_bis = datetime.now(timezone.utc) + timedelta(days=EINLADUNG_TAGE)

    einladung = Einladung(
        email=email,
        token=token,
        rolle=BenutzerRolle(rolle),
        eingeladen_von_id=admin.id,
        gueltig_bis=gueltig_bis,
    )
    db.add(einladung)
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
        Dieser Link ist {EINLADUNG_TAGE} Tage gültig.<br>
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


@router.post("/benutzer/{benutzer_id}/deaktivieren")
async def benutzer_deaktivieren(
    benutzer_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    admin = await require_admin(request, db)

    if benutzer_id == admin.id:
        return RedirectResponse("/admin/?fehler=Eigenes+Konto+nicht+deaktivierbar", status_code=302)

    result = await db.execute(select(Benutzer).where(Benutzer.id == benutzer_id))
    ziel = result.scalar_one_or_none()
    if ziel:
        ziel.ist_aktiv = not ziel.ist_aktiv
        await db.commit()

    return RedirectResponse("/admin/", status_code=302)


# ---------------------------------------------------------------------------
# Vereinseinstellungen
# ---------------------------------------------------------------------------

EINSTELLUNGEN_FELDER = [
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
]

# Optionale Funktionsbereiche, die sich pro Verein ein-/ausschalten lassen.
# Schlüssel folgen der Konvention "modul_<name>" (siehe app/module_flags.py).
MODULE_FELDER = [
    ("modul_pflichtstunden", "Pflichtstunden-Verwaltung",
     "Arbeitseinsätze, Patenschaften, Vereinsrollen und Jahresauswertung. "
     "Deaktivieren, falls der Verein keine Pflichtstunden erhebt."),
    ("modul_wasser", "Wasserverwaltung",
     "Wasserzähler, Zählerstände und Verbrauchsauswertung. "
     "Deaktivieren, falls der Verein keine eigene Wasserversorgung verwaltet."),
    ("modul_strom", "Stromverwaltung",
     "Stromzähler, Zählerstände und Verbrauchsauswertung. "
     "Deaktivieren, falls der Verein keine eigene Stromversorgung verwaltet."),
    ("modul_versicherungen", "Versicherungsverwaltung",
     "Sach- und Unfallversicherung pro Parzelle inkl. Jahresauswertung. "
     "Deaktivieren, falls der Verein keine Versicherungen über das Programm abwickelt."),
    ("modul_tickets", "Ticketsystem",
     "Support-Tickets mit Zuweisung, Status und Mitglied-Zuordnung. "
     "Deaktivieren, falls der Verein kein internes Ticketsystem nutzt."),
]


@router.get("/einstellungen", response_class=HTMLResponse)
async def einstellungen_seite(request: Request, db: AsyncSession = Depends(get_db)):
    benutzer = await require_admin(request, db)

    result = await db.execute(select(Vereinseinstellung))
    einstellungen = {e.schluessel: e.wert for e in result.scalars().all()}

    return templates.TemplateResponse(
        "admin/einstellungen.html",
        {
            "request": request,
            "benutzer": benutzer,
            "einstellungen": einstellungen,
            "felder": EINSTELLUNGEN_FELDER,
            "module_felder": MODULE_FELDER,
        },
    )


@router.post("/einstellungen")
async def einstellungen_speichern(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    await require_admin(request, db)
    form = await request.form()

    for schluessel, beschreibung in EINSTELLUNGEN_FELDER:
        wert = form.get(schluessel, "").strip() or None

        result = await db.execute(
            select(Vereinseinstellung).where(Vereinseinstellung.schluessel == schluessel)
        )
        eintrag = result.scalar_one_or_none()

        if schluessel == "smtp_password":
            # Leeres Feld = "unverändert lassen" (wie im Platzhaltertext
            # versprochen), damit man nicht bei jedem Speichern das
            # Passwort neu eintippen muss. Nur ein NEUER Wert wird
            # verschlüsselt gespeichert.
            if not wert:
                continue
            wert = verschluesseln(wert)

        if eintrag:
            eintrag.wert = wert
        else:
            db.add(Vereinseinstellung(
                schluessel=schluessel,
                wert=wert,
                beschreibung=beschreibung,
            ))

    # Modul-Umschalter: Checkboxen senden bei "aus" gar keinen Wert im
    # Formular, daher explizit "true"/"false" statt nur form.get(...).
    for schluessel, beschreibung, _hinweis in MODULE_FELDER:
        wert = "true" if schluessel in form else "false"

        result = await db.execute(
            select(Vereinseinstellung).where(Vereinseinstellung.schluessel == schluessel)
        )
        eintrag = result.scalar_one_or_none()

        if eintrag:
            eintrag.wert = wert
        else:
            db.add(Vereinseinstellung(
                schluessel=schluessel,
                wert=wert,
                beschreibung=beschreibung,
            ))

    await db.commit()
    return RedirectResponse("/admin/einstellungen?erfolg=1", status_code=302)
