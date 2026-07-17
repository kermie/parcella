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
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database import get_db, AsyncSessionLocal, active_member_filter
from app.models import User, UserRole, Member, Parcel, ParcelStatus, MemberParcel
from app.models import PurchaseRequest, PurchaseRequestStatus
from app.models import Ticket, TicketStatus
from app.auth import hash_password, get_current_user
from app.module_flags import lade_modul_flags
from app.i18n import load_translations, load_current_language
from app.l10n import load_current_region, load_current_currency

# Wird beim Modul-Import geladen (nicht erst im Lifespan-Startup-Event),
# da ASGI-Test-Clients (z.B. httpx mit ASGITransport) Lifespan-Events
# nicht zwingend auslösen. load_translations() ist eine reine, schnelle
# Dateilese-Operation ohne DB-Zugriff – unproblematisch beim Import.
load_translations()
from app.templating import templates
from app.ticket_mailer import process_incoming_mails
from app.routers import auth, members, parcels, admin as admin_router, work_hours, insurance, tickets, purchase_requests
from app.routers.metering import erstelle_metering_router
from app.models import MeteringMedium
from app.routers import api_auth, api_members, api_parcels, api_club_settings, api_stats
from app.routers import api_work_hours, api_insurance, api_tickets, api_purchase_requests
from app.routers.api_metering import erstelle_metering_api_router

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def _ticket_inbox_polling_loop():
    """
    Fragt alle 2 Minuten das konfigurierte Ticket-Postfach nach neuen
    E-Mails ab. Läuft dauerhaft im Hintergrund; Fehler werden abgefangen,
    damit die Schleife nicht durch einen einzelnen fehlgeschlagenen
    Abruf beendet wird.
    """
    while True:
        try:
            async with AsyncSessionLocal() as db:
                anzahl = await process_incoming_mails(db)
                if anzahl:
                    logger.info(f"Ticket-Postfach: {anzahl} neue E-Mail(s) verarbeitet.")
        except Exception as e:
            logger.error(f"Ticket-Postfach-Polling fehlgeschlagen: {e}")

        await asyncio.sleep(120)  # 2 Minuten


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: ersten Admin anlegen falls die Benutzertabelle leer ist."""
    async with AsyncSessionLocal() as db:
        user_count = await db.scalar(select(func.count()).select_from(User))
        if not user_count:
            erster_admin = User(
                email="admin@gartenverein.local",
                name="Administrator",
                password_hash=hash_password("admin1234"),
                role=UserRole.ADMIN,
            )
            db.add(erster_admin)
            await db.commit()
            logger.warning(
                "Erster Admin-Benutzer angelegt: admin@gartenverein.local / admin1234 "
                "– BITTE SOFORT PASSWORT ÄNDERN!"
            )

    polling_task = asyncio.create_task(_ticket_inbox_polling_loop())
    yield
    polling_task.cancel()


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description=(
        "REST API for managing an allotment garden association: members, parcels, "
        "assignments, and club settings. Authentication via JWT bearer token "
        "(see `/api/v1/auth/token` or `/api/v1/auth/login`).\n\n"
        "The interactive web UI (Jinja2 templates) runs in parallel at `/`, "
        "`/members/`, `/parcels/`, etc., and uses separate, cookie-based "
        "session authentication."
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


@app.middleware("http")
async def sprache_middleware(request: Request, call_next):
    """
    Lädt einmal pro Request die aktuell eingestellte Sprache (siehe
    app/i18n.py) und legt sie unter request.state.language ab. Templates
    (über die Jinja-Funktion `t`) und Router (über t_for(request, ...))
    lesen von dort.
    """
    async with AsyncSessionLocal() as db:
        request.state.language = await load_current_language(db)
    response = await call_next(request)
    return response


@app.middleware("http")
async def l10n_middleware(request: Request, call_next):
    """
    Lädt einmal pro Request Region und Währung (siehe app/l10n.py) und
    legt sie unter request.state.region / request.state.currency ab.
    Bewusst getrennt von der Sprache (sprache_middleware oben) -- Region/
    Währung sind unabhängige Einstellungen, siehe app/l10n.py-Docstring.
    Templates nutzen die Filter/Funktion `money`, `number`, `address`.
    """
    async with AsyncSessionLocal() as db:
        request.state.region = await load_current_region(db)
        request.state.currency = await load_current_currency(db)
    response = await call_next(request)
    return response

# Router registrieren – Web-UI (Jinja2)
app.include_router(auth.router)
app.include_router(members.router)
app.include_router(parcels.router)
app.include_router(admin_router.router)
app.include_router(work_hours.router)
app.include_router(insurance.router)
app.include_router(tickets.router)
app.include_router(purchase_requests.router)

# Zählerwesen: EINE Codebasis (app/routers/metering.py), zweimal
# instanziiert für Wasser und Strom – siehe erstelle_metering_router().
water_router = erstelle_metering_router(
    medium=MeteringMedium.WATER, url_prefix="/water", modul_name="water",
    medium_label_key="metering.medium.water", unit="m³", icon="bi-droplet", dezimalstellen=1,
)
electricity_router = erstelle_metering_router(
    medium=MeteringMedium.ELECTRICITY, url_prefix="/electricity", modul_name="electricity",
    medium_label_key="metering.medium.electricity", unit="kWh", icon="bi-lightning-charge", dezimalstellen=0,
)
app.include_router(water_router)
app.include_router(electricity_router)

# Router registrieren – REST-API (JSON, JWT-Auth)
app.include_router(api_auth.router)
app.include_router(api_members.router)
app.include_router(api_parcels.router)
app.include_router(api_club_settings.router)
app.include_router(api_stats.router)
app.include_router(api_work_hours.router)
app.include_router(api_insurance.router)
app.include_router(api_tickets.router)
app.include_router(api_purchase_requests.router)

api_water_router = erstelle_metering_api_router(MeteringMedium.WATER, "/water", "water")
api_electricity_router = erstelle_metering_api_router(MeteringMedium.ELECTRICITY, "/electricity", "electricity")
app.include_router(api_water_router)
app.include_router(api_electricity_router)


@app.get("/", response_class=HTMLResponse)
async def startseite(request: Request):
    async with AsyncSessionLocal() as db:
        user = await get_current_user(request, db)

        if not user:
            return RedirectResponse("/auth/login", status_code=302)

        members_total = await db.scalar(
            select(func.count()).where(active_member_filter())
        )
        members_active = members_total  # gesamt zählt bereits nur aktive
        parcels_active = await db.scalar(
            select(func.count()).select_from(Parcel).where(
                Parcel.status == ParcelStatus.ACTIVE
            )
        )
        parcels_terminated = await db.scalar(
            select(func.count()).select_from(Parcel).where(
                Parcel.status == ParcelStatus.TERMINATED
            )
        )
        besetzte_ids = select(MemberParcel.parcel_id).distinct()
        parcels_vacant = await db.scalar(
            select(func.count()).select_from(Parcel).where(
                Parcel.status == ParcelStatus.ACTIVE,
                Parcel.id.not_in(besetzte_ids)
            )
        )
        area_total = await db.scalar(
            select(func.coalesce(func.sum(Parcel.area_sqm), 0)).where(
                Parcel.status == ParcelStatus.ACTIVE
            )
        )
        neueste_result = await db.execute(
            select(Member)
            .where(active_member_filter())
            .order_by(Member.created_at.desc())
            .limit(5)
        )
        recent_members = neueste_result.scalars().all()

        # Für die Dashboard-Kachel "Offene Einkaufswünsche" -- nur relevant,
        # wenn das Modul aktiv ist (siehe request.state.module_flags im
        # Template), aber die Abfrage kostet nichts, wenn leer/deaktiviert.
        purchase_requests_open_count = await db.scalar(
            select(func.count()).select_from(PurchaseRequest).where(
                PurchaseRequest.status == PurchaseRequestStatus.OPEN
            )
        )

        # Für die Dashboard-Kachel "Tickets" -- "offen" zählt hier genau wie
        # der "Active"-Filter auf /tickets/ (ACTIVE/ASSIGNED/WAITING, siehe
        # app/routers/tickets.py), NICHT postponed/closed/deleted.
        tickets_open_count = await db.scalar(
            select(func.count()).select_from(Ticket).where(
                Ticket.status.in_([TicketStatus.ACTIVE, TicketStatus.ASSIGNED, TicketStatus.WAITING])
            )
        )
        tickets_spam_count = await db.scalar(
            select(func.count()).select_from(Ticket).where(
                Ticket.spam_suspected == True, Ticket.status != TicketStatus.DELETED
            )
        )

    stats = {
        "mitglieder_gesamt": members_total or 0,
        "mitglieder_aktiv": members_active or 0,
        "parzellen_aktiv": parcels_active or 0,
        "parzellen_gekuendigt": parcels_terminated or 0,
        "parzellen_unbesetzt": parcels_vacant or 0,
        "flaeche_gesamt_qm": float(area_total or 0),
        "einkaufswuensche_offen": purchase_requests_open_count or 0,
        "tickets_offen": tickets_open_count or 0,
        "tickets_spam": tickets_spam_count or 0,
    }

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "user": user,
            "stats": stats,
            "neueste_mitglieder": recent_members,
        },
    )


@app.exception_handler(403)
async def forbidden_handler(request: Request, exc):
    async with AsyncSessionLocal() as db:
        user = await get_current_user(request, db)
    # exc.detail trägt oft eine konkrete, hilfreiche Begründung (z.B. "Der
    # Antragsteller darf seinen eigenen Einkaufswunsch nicht mitfreigeben").
    # Bisher wurde das hier immer verworfen und nur "Keine Berechtigung"
    # angezeigt – das machte etliche an anderer Stelle sorgfältig formulierte
    # (und übersetzte) Fehlermeldungen faktisch unsichtbar. Jetzt: konkrete
    # Meldung anzeigen, falls vorhanden. Wichtig: FastAPI füllt "detail"
    # automatisch mit der generischen englischen HTTP-Statustext-Phrase
    # ("Forbidden"), wenn beim Auslösen kein eigener Text übergeben wurde –
    # genau diesen Fall müssen wir erkennen und stattdessen weiter den
    # deutschen Standardtext zeigen, statt versehentlich Englisch durchsickern
    # zu lassen.
    detail = getattr(exc, "detail", None)
    meldung = detail if detail and detail != "Forbidden" else "Keine Berechtigung"
    return templates.TemplateResponse(
        "fehler.html",
        {"request": request, "user": user, "code": 403, "meldung": meldung},
        status_code=403,
    )


@app.exception_handler(404)
async def not_found_handler(request: Request, exc):
    async with AsyncSessionLocal() as db:
        user = await get_current_user(request, db)
    detail = getattr(exc, "detail", None)
    meldung = detail if detail and detail != "Not Found" else "Seite nicht gefunden"
    return templates.TemplateResponse(
        "fehler.html",
        {"request": request, "user": user, "code": 404, "meldung": meldung},
        status_code=404,
    )
