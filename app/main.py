"""
Allotment garden association management -- main application.
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
from app.birthdays import upcoming_birthdays
from app.auth import hash_password, get_current_user
from app.module_flags import load_module_flags
from app.nav_order import load_nav_order
from app.i18n import load_translations, load_current_language, t_for
from app.l10n import load_current_region, load_current_currency
from app.branding import load_branding
from app.update_check import refresh_update_check_cache
from app.permissions import get_user_permissions, is_full_access_user, is_system_admin_user

# Loaded at module import time (not only in the lifespan startup
# event), since ASGI test clients (e.g. httpx with ASGITransport) don't
# necessarily trigger lifespan events. load_translations() is a pure,
# fast file-read operation with no DB access -- unproblematic at import.
load_translations()
from app.templating import templates
from app.ticket_mailer import process_incoming_mails
from app.routers import auth, members, parcels, admin as admin_router, admin_groups as admin_groups_router, work_hours, insurance, tickets, purchase_requests, calendar as calendar_router, announcements as announcements_router, inventory as inventory_router, tasks as tasks_router, finances as finances_router
from app.routers.metering import create_metering_router
from app.models import MeteringMedium
from app.routers import api_auth, api_members, api_parcels, api_club_settings, api_stats
from app.routers import api_work_hours, api_insurance, api_tickets, api_purchase_requests, api_inventory, api_tasks
from app.routers import api_public
from app.routers.api_metering import create_metering_api_router

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def _ticket_inbox_polling_loop():
    """
    Polls the configured ticket mailbox for new emails every 2 minutes.
    Runs permanently in the background; errors are caught so a single
    failed poll doesn't end the loop.
    """
    while True:
        try:
            async with AsyncSessionLocal() as db:
                anzahl = await process_incoming_mails(db)
                if anzahl:
                    logger.info(f"Ticket mailbox: {anzahl} new email(s) processed.")
        except Exception as e:
            logger.error(f"Ticket mailbox polling failed: {e}")

        await asyncio.sleep(120)  # 2 minutes


async def _update_check_polling_loop():
    """
    Periodically checks GitHub releases for a newer Parcella version
    than the one currently running, caching the result (see
    app/update_check.py) so the admin dashboard can show it without an
    outbound call on every page load. Skipped when disabled in
    Admin -> Settings.
    """
    while True:
        try:
            async with AsyncSessionLocal() as db:
                await refresh_update_check_cache(db)
        except Exception as e:
            logger.error(f"Update check failed: {e}")

        await asyncio.sleep(6 * 60 * 60)  # 6 hours


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: create the first admin if the users table is empty."""
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
                "First admin user created: admin@gartenverein.local / admin1234 "
                "-- PLEASE CHANGE THE PASSWORD IMMEDIATELY!"
            )

    polling_task = asyncio.create_task(_ticket_inbox_polling_loop())
    update_check_task = asyncio.create_task(_update_check_polling_loop())
    yield
    polling_task.cancel()
    update_check_task.cancel()


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

# Static files
app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.middleware("http")
async def modul_flags_middleware(request: Request, call_next):
    """
    Loads the module flags once per request (e.g. whether work hours is
    active) and stores them under request.state.module_flags. Templates
    and router dependencies (require_module) read from there without
    querying the DB again.
    """
    async with AsyncSessionLocal() as db:
        request.state.module_flags = await load_module_flags(db)
    response = await call_next(request)
    return response


@app.middleware("http")
async def nav_order_middleware(request: Request, call_next):
    """
    Loads the club's configured sidebar nav order once per request (see
    app/nav_order.py, issue #60) and stores it under
    request.state.nav_order. base.html sorts its nav-item macros by
    this instead of a fixed source order.
    """
    async with AsyncSessionLocal() as db:
        request.state.nav_order = await load_nav_order(db)
    response = await call_next(request)
    return response


@app.middleware("http")
async def sprache_middleware(request: Request, call_next):
    """
    Loads the currently configured language once per request (see
    app/i18n.py) and stores it under request.state.language. Templates
    (via the Jinja function `t`) and routers (via t_for(request, ...))
    read from there.
    """
    async with AsyncSessionLocal() as db:
        request.state.language = await load_current_language(db)
    response = await call_next(request)
    return response


@app.middleware("http")
async def l10n_middleware(request: Request, call_next):
    """
    Loads region and currency once per request (see app/l10n.py) and
    stores them under request.state.region / request.state.currency.
    Deliberately separate from language (sprache_middleware above) --
    region/currency are independent settings, see the app/l10n.py
    module docstring. Templates use the `money`, `number`, `address`
    filters/function.
    """
    async with AsyncSessionLocal() as db:
        request.state.region = await load_current_region(db)
        request.state.currency = await load_current_currency(db)
    response = await call_next(request)
    return response


@app.middleware("http")
async def permissions_middleware(request: Request, call_next):
    """
    Loads the current user's effective per-module permissions once per
    request and stores them under request.state.permissions, plus the
    two ADR 0041 group-derived flags (is_full_access/is_system_admin)
    -- see app/permissions.py. require_permission()/require_admin()/
    require_system_admin() and the has_perm/is_full_access/is_system_admin
    Jinja globals all read from here instead of re-querying. Anonymous
    requests get all-False permissions, same as get_user_permissions(None).
    """
    async with AsyncSessionLocal() as db:
        user = await get_current_user(request, db)
        request.state.permissions = await get_user_permissions(db, user)
        request.state.is_full_access = await is_full_access_user(db, user)
        request.state.is_system_admin = await is_system_admin_user(db, user)
    response = await call_next(request)
    return response


@app.middleware("http")
async def branding_middleware(request: Request, call_next):
    """Loads the club's display name and custom logo once per request
    (same pattern as module flags, language, and l10n above) and stores
    them under request.state.club_name / request.state.logo_url. See
    app/branding.py."""
    async with AsyncSessionLocal() as db:
        branding = await load_branding(db)
        request.state.club_name = branding["club_name"]
        request.state.logo_url = branding["logo_url"]
    response = await call_next(request)
    return response

# Register routers -- Web UI (Jinja2)
app.include_router(auth.router)
app.include_router(members.router)
app.include_router(parcels.router)
app.include_router(admin_router.router)
app.include_router(admin_groups_router.router)
app.include_router(work_hours.router)
app.include_router(insurance.router)
app.include_router(tickets.router)
app.include_router(purchase_requests.router)
app.include_router(calendar_router.router)
app.include_router(announcements_router.router)
app.include_router(inventory_router.router)
app.include_router(tasks_router.router)
app.include_router(finances_router.router)

# Metering: ONE codebase (app/routers/metering.py), instantiated twice
# for water and electricity -- see create_metering_router().
water_router = create_metering_router(
    medium=MeteringMedium.WATER, url_prefix="/water", modul_name="water",
    medium_label_key="metering.medium.water", unit="m³", icon="bi-droplet", decimal_places=1,
)
electricity_router = create_metering_router(
    medium=MeteringMedium.ELECTRICITY, url_prefix="/electricity", modul_name="electricity",
    medium_label_key="metering.medium.electricity", unit="kWh", icon="bi-lightning-charge", decimal_places=0,
)
app.include_router(water_router)
app.include_router(electricity_router)

# Register routers -- REST API (JSON, JWT auth)
app.include_router(api_auth.router)
app.include_router(api_members.router)
app.include_router(api_parcels.router)
app.include_router(api_club_settings.router)
app.include_router(api_stats.router)
app.include_router(api_work_hours.router)
app.include_router(api_insurance.router)
app.include_router(api_inventory.router)
app.include_router(api_tasks.router)
app.include_router(api_tickets.router)
app.include_router(api_purchase_requests.router)
app.include_router(api_public.router)

api_water_router = create_metering_api_router(MeteringMedium.WATER, "/water", "water")
api_electricity_router = create_metering_api_router(MeteringMedium.ELECTRICITY, "/electricity", "electricity")
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
        members_active = members_total  # total already counts only active members
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

        # For the dashboard tile "Open purchase requests" -- only relevant
        # when the module is active (see request.state.module_flags in
        # the template), but the query costs nothing when empty/disabled.
        purchase_requests_open_count = await db.scalar(
            select(func.count()).select_from(PurchaseRequest).where(
                PurchaseRequest.status == PurchaseRequestStatus.OPEN
            )
        )

        # For the dashboard tile "Tickets" -- "open" here counts exactly
        # like the "Active" filter on /tickets/ (ACTIVE/ASSIGNED/WAITING,
        # see app/routers/tickets.py), NOT postponed/closed/deleted.
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

    # Dashboard tile "Birthdays this week" -- independent of the Calendar
    # module flag, since birthdays are shown here purely for information
    # (no link/dependency on the calendar routes).
    birthdays_this_week = await upcoming_birthdays(db, within_days=7)

    stats = {
        "members_total": members_total or 0,
        "members_active": members_active or 0,
        "parcels_active": parcels_active or 0,
        "parcels_terminated": parcels_terminated or 0,
        "parcels_vacant": parcels_vacant or 0,
        "area_total_sqm": float(area_total or 0),
        "purchase_requests_open": purchase_requests_open_count or 0,
        "tickets_open": tickets_open_count or 0,
        "tickets_spam": tickets_spam_count or 0,
    }

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "user": user,
            "stats": stats,
            "recent_members": recent_members,
            "birthdays_this_week": birthdays_this_week,
            "today_date": date.today(),
        },
    )


@app.exception_handler(403)
async def forbidden_handler(request: Request, exc):
    async with AsyncSessionLocal() as db:
        user = await get_current_user(request, db)
    # exc.detail often carries a specific, helpful reason (e.g. "the
    # requester may not also approve their own purchase request"). This
    # used to be discarded entirely in favor of a generic fallback --
    # which made a number of carefully worded (and translated) error
    # messages elsewhere effectively invisible. Now: show the specific
    # message if present. Important: FastAPI auto-fills "detail" with
    # the generic English HTTP status phrase ("Forbidden") when no
    # custom text was given at raise time -- detect exactly that case
    # and fall back to the translated generic message instead.
    detail = getattr(exc, "detail", None)
    meldung = detail if detail and detail != "Forbidden" else t_for(request, "errors.no_permission")
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
    meldung = detail if detail and detail != "Not Found" else t_for(request, "errors.page_not_found")
    return templates.TemplateResponse(
        "fehler.html",
        {"request": request, "user": user, "code": 404, "meldung": meldung},
        status_code=404,
    )
