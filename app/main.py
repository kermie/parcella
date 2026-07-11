"""
Gartenverein-Verwaltung – Hauptanwendung.
"""
from contextlib import asynccontextmanager
from datetime import date
import asyncio
import logging

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database import get_db, AsyncSessionLocal, aktives_mitglied_filter
from app.models import Benutzer, BenutzerRolle, Mitglied, Parzelle, ParzelleStatus, MitgliedParzelle
from app.auth import hash_passwort, get_current_user
from app.module_flags import lade_modul_flags
from app.ticket_mailer import verarbeite_eingehende_mails
from app.routers import auth, mitglieder, parzellen, admin as admin_router, pflichtstunden, versicherungen, tickets, einkaufswuensche
from app.routers.zaehlerwesen import erstelle_zaehler_router
from app.models import ZaehlerMedium
from app.routers import api_auth, api_mitglieder, api_parzellen, api_einstellungen, api_stats
from app.routers import api_pflichtstunden, api_versicherungen, api_tickets, api_einkaufswuensche
from app.routers.api_zaehlerwesen import erstelle_zaehler_api_router

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def _ticket_postfach_polling_schleife():
    """
    Fragt alle 2 Minuten das konfigurierte Ticket-Postfach nach neuen
    E-Mails ab. Läuft dauerhaft im Hintergrund; Fehler werden abgefangen,
    damit die Schleife nicht durch einen einzelnen fehlgeschlagenen
    Abruf beendet wird.
    """
    while True:
        try:
            async with AsyncSessionLocal() as db:
                anzahl = await verarbeite_eingehende_mails(db)
                if anzahl:
                    logger.info(f"Ticket-Postfach: {anzahl} neue E-Mail(s) verarbeitet.")
        except Exception as e:
            logger.error(f"Ticket-Postfach-Polling fehlgeschlagen: {e}")

        await asyncio.sleep(120)  # 2 Minuten


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: ersten Admin anlegen falls die Benutzertabelle leer ist."""
    async with AsyncSessionLocal() as db:
        anzahl_benutzer = await db.scalar(select(func.count()).select_from(Benutzer))
        if not anzahl_benutzer:
            erster_admin = Benutzer(
                email="admin@gartenverein.local",
                name="Administrator",
                passwort_hash=hash_passwort("admin1234"),
                rolle=BenutzerRolle.ADMIN,
            )
            db.add(erster_admin)
            await db.commit()
            logger.warning(
                "Erster Admin-Benutzer angelegt: admin@gartenverein.local / admin1234 "
                "– BITTE SOFORT PASSWORT ÄNDERN!"
            )

    polling_task = asyncio.create_task(_ticket_postfach_polling_schleife())
    yield
    polling_task.cancel()


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description=(
        "REST-API zur Verwaltung eines Kleingärtnervereins: Mitglieder, Parzellen, "
        "Zuordnungen und Vereinseinstellungen. Authentifizierung über JWT-Bearer-Token "
        "(siehe `/api/v1/auth/token` bzw. `/api/v1/auth/login`).\n\n"
        "Die interaktive Web-Oberfläche (Jinja2-Templates) läuft parallel unter `/`, "
        "`/mitglieder/`, `/parzellen/` usw. und nutzt eine separate, cookie-basierte "
        "Session-Authentifizierung."
    ),
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)

# Statische Dateien
app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.middleware("http")
async def modul_flags_middleware(request: Request, call_next):
    """
    Lädt einmal pro Request die Modul-Flags (z.B. ob Pflichtstunden aktiv
    ist) und legt sie unter request.state.module_flags ab. Templates und
    Router-Dependencies (require_modul) lesen von dort, ohne die DB
    erneut abzufragen.
    """
    async with AsyncSessionLocal() as db:
        request.state.module_flags = await lade_modul_flags(db)
    response = await call_next(request)
    return response

# Router registrieren – Web-UI (Jinja2)
app.include_router(auth.router)
app.include_router(mitglieder.router)
app.include_router(parzellen.router)
app.include_router(admin_router.router)
app.include_router(pflichtstunden.router)
app.include_router(versicherungen.router)
app.include_router(tickets.router)
app.include_router(einkaufswuensche.router)

# Zählerwesen: EINE Codebasis (app/routers/zaehlerwesen.py), zweimal
# instanziiert für Wasser und Strom – siehe erstelle_zaehler_router().
wasser_router = erstelle_zaehler_router(
    medium=ZaehlerMedium.WASSER, url_prefix="/wasser", modul_name="wasser",
    medium_label="Wasser", einheit="m³", icon="bi-droplet", dezimalstellen=1,
)
strom_router = erstelle_zaehler_router(
    medium=ZaehlerMedium.STROM, url_prefix="/strom", modul_name="strom",
    medium_label="Strom", einheit="kWh", icon="bi-lightning-charge", dezimalstellen=0,
)
app.include_router(wasser_router)
app.include_router(strom_router)

# Router registrieren – REST-API (JSON, JWT-Auth)
app.include_router(api_auth.router)
app.include_router(api_mitglieder.router)
app.include_router(api_parzellen.router)
app.include_router(api_einstellungen.router)
app.include_router(api_stats.router)
app.include_router(api_pflichtstunden.router)
app.include_router(api_versicherungen.router)
app.include_router(api_tickets.router)
app.include_router(api_einkaufswuensche.router)

api_wasser_router = erstelle_zaehler_api_router(ZaehlerMedium.WASSER, "/wasser", "wasser")
api_strom_router = erstelle_zaehler_api_router(ZaehlerMedium.STROM, "/strom", "strom")
app.include_router(api_wasser_router)
app.include_router(api_strom_router)

templates = Jinja2Templates(directory="app/templates")


@app.get("/", response_class=HTMLResponse)
async def startseite(request: Request):
    async with AsyncSessionLocal() as db:
        benutzer = await get_current_user(request, db)

        if not benutzer:
            return RedirectResponse("/auth/login", status_code=302)

        mitglieder_gesamt = await db.scalar(
            select(func.count()).where(aktives_mitglied_filter())
        )
        mitglieder_aktiv = mitglieder_gesamt  # gesamt zählt bereits nur aktive
        parzellen_aktiv = await db.scalar(
            select(func.count()).select_from(Parzelle).where(
                Parzelle.status == ParzelleStatus.AKTIV
            )
        )
        parzellen_gekuendigt = await db.scalar(
            select(func.count()).select_from(Parzelle).where(
                Parzelle.status == ParzelleStatus.GEKUENDIGT
            )
        )
        besetzte_ids = select(MitgliedParzelle.parzelle_id).distinct()
        parzellen_unbesetzt = await db.scalar(
            select(func.count()).select_from(Parzelle).where(
                Parzelle.status == ParzelleStatus.AKTIV,
                Parzelle.id.not_in(besetzte_ids)
            )
        )
        flaeche_gesamt = await db.scalar(
            select(func.coalesce(func.sum(Parzelle.flaeche_qm), 0)).where(
                Parzelle.status == ParzelleStatus.AKTIV
            )
        )
        neueste_result = await db.execute(
            select(Mitglied)
            .where(aktives_mitglied_filter())
            .order_by(Mitglied.created_at.desc())
            .limit(5)
        )
        neueste_mitglieder = neueste_result.scalars().all()

    stats = {
        "mitglieder_gesamt": mitglieder_gesamt or 0,
        "mitglieder_aktiv": mitglieder_aktiv or 0,
        "parzellen_aktiv": parzellen_aktiv or 0,
        "parzellen_gekuendigt": parzellen_gekuendigt or 0,
        "parzellen_unbesetzt": parzellen_unbesetzt or 0,
        "flaeche_gesamt_qm": float(flaeche_gesamt or 0),
    }

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "benutzer": benutzer,
            "stats": stats,
            "neueste_mitglieder": neueste_mitglieder,
        },
    )


@app.exception_handler(403)
async def forbidden_handler(request: Request, exc):
    async with AsyncSessionLocal() as db:
        benutzer = await get_current_user(request, db)
    return templates.TemplateResponse(
        "fehler.html",
        {"request": request, "benutzer": benutzer, "code": 403, "meldung": "Keine Berechtigung"},
        status_code=403,
    )


@app.exception_handler(404)
async def not_found_handler(request: Request, exc):
    async with AsyncSessionLocal() as db:
        benutzer = await get_current_user(request, db)
    return templates.TemplateResponse(
        "fehler.html",
        {"request": request, "benutzer": benutzer, "code": 404, "meldung": "Seite nicht gefunden"},
        status_code=404,
    )
