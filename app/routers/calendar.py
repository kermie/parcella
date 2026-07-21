"""
Calendar module router.

Four calendars, each with a simple upcoming-items list (no full
month-grid view -- deliberately kept simple per the feature request)
and an ICS export:

- Community calendar: member meetings + parcel inspections (entered
  here) merged with work sessions (read directly from WorkSession, not
  duplicated). Its ICS feed is fully public -- meant to be embedded on
  the club's public WordPress site, which can't authenticate with this
  app's session cookies.
- Birthdays: derived entirely from Member.date_of_birth, nothing stored.
- Council presence: scheduled on-site slots for board/council members.
- Council absence: self-reported absence periods for any user account.

The latter three ICS feeds require a secret token (see app/ics_utils.py)
rather than a login, since calendar apps subscribing to a feed URL
can't do session-cookie authentication either -- the token is the
practical equivalent of a login for a subscription URL.
"""
from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Request, Form, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models import (
    CalendarEvent, CalendarEventType, WorkSession, SessionType,
    CouncilPresence, CouncilAbsence, User,
)
from app.auth import require_user, require_admin
from app.module_flags import require_module
from app.i18n import t_for
from app.birthdays import upcoming_birthdays, all_birthdays_for_calendar, ROUND_BIRTHDAY_INTERVAL
from app.ics_utils import (
    get_or_create_ics_token, verify_ics_token,
    build_community_calendar, build_birthday_calendar,
    build_council_presence_calendar, build_council_absence_calendar,
)
from app.templating import templates

router = APIRouter(
    prefix="/calendar",
    tags=["calendar"],
    dependencies=[Depends(require_module("calendar"))],
)


def _ics_response(ical_bytes: bytes, filename: str) -> Response:
    return Response(
        content=ical_bytes,
        media_type="text/calendar; charset=utf-8",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# Hub -- removed as a standalone overview page (redundant once every
# sub-calendar links its own ICS feed directly, see below); kept as a
# redirect so old bookmarks/links to /calendar/ still land somewhere
# useful rather than 404ing.
# ---------------------------------------------------------------------------

@router.get("/", response_class=HTMLResponse)
async def calendar_hub(request: Request, db: AsyncSession = Depends(get_db)):
    await require_user(request, db)
    return RedirectResponse("/calendar/community", status_code=302)


# ---------------------------------------------------------------------------
# Community calendar (member meetings, parcel inspections, work sessions)
# ---------------------------------------------------------------------------

@router.get("/community", response_class=HTMLResponse)
async def community_overview(request: Request, db: AsyncSession = Depends(get_db)):
    user = await require_user(request, db)

    events_result = await db.execute(
        select(CalendarEvent).where(CalendarEvent.start_date >= date.today()).order_by(CalendarEvent.start_date)
    )
    events = events_result.scalars().all()

    # STANDARD only -- SPECIAL sessions are spontaneous/unplanned (e.g.
    # "paint the garden bench today") and deliberately don't show up
    # here, since they aren't something members plan their week around.
    # Same filter as the ICS feed (app/ics_utils.py's
    # build_community_calendar) -- keep both in sync if this changes.
    sessions_result = await db.execute(
        select(WorkSession)
        .where(WorkSession.date >= date.today(), WorkSession.type == SessionType.STANDARD)
        .order_by(WorkSession.date)
    )
    sessions = sessions_result.scalars().all()

    # Merge into one date-sorted list for display: (date, kind, item)
    combined = [("event", e.start_date, e) for e in events] + [("session", s.date, s) for s in sessions]
    combined.sort(key=lambda row: row[1])

    return templates.TemplateResponse("calendar/community.html", {
        "request": request, "user": user, "combined": combined,
        "CalendarEventType": CalendarEventType,
    })


@router.post("/community/new")
async def community_event_create(
    request: Request,
    title: str = Form(...),
    event_type: str = Form("OTHER"),
    description: str = Form(""),
    location: str = Form(""),
    start_date: str = Form(...),
    start_time: str = Form(""),
    end_date: str = Form(""),
    end_time: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    user = await require_admin(request, db)

    event = CalendarEvent(
        title=title.strip(),
        event_type=CalendarEventType(event_type),
        description=description.strip() or None,
        location=location.strip() or None,
        start_date=date.fromisoformat(start_date),
        start_time=start_time or None,
        end_date=date.fromisoformat(end_date) if end_date else None,
        end_time=end_time or None,
        created_by_id=user.id,
    )
    db.add(event)
    await db.commit()
    return RedirectResponse("/calendar/community", status_code=302)


@router.post("/community/{event_id}/delete")
async def community_event_delete(event_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    await require_admin(request, db)
    result = await db.execute(select(CalendarEvent).where(CalendarEvent.id == event_id))
    event = result.scalar_one_or_none()
    if not event:
        raise HTTPException(status_code=404, detail=t_for(request, "calendar.errors.event_not_found"))
    await db.delete(event)
    await db.commit()
    return RedirectResponse("/calendar/community", status_code=302)


@router.get("/community.ics")
async def community_ics(request: Request, db: AsyncSession = Depends(get_db)):
    """Public, unauthenticated -- meant for embedding on the club's
    public WordPress site. Contains only already-public information
    (meeting/inspection announcements and work session schedules)."""
    cal = await build_community_calendar(db, base_url=request.url.hostname or "parcella.local")
    return _ics_response(cal.to_ical(), "community.ics")


# ---------------------------------------------------------------------------
# Birthdays
# ---------------------------------------------------------------------------

@router.get("/birthdays", response_class=HTMLResponse)
async def birthdays_overview(request: Request, db: AsyncSession = Depends(get_db)):
    user = await require_user(request, db)
    upcoming = await upcoming_birthdays(db, within_days=90)
    ics_token = await get_or_create_ics_token(db)
    return templates.TemplateResponse("calendar/birthdays.html", {
        "request": request, "user": user, "upcoming": upcoming,
        "ROUND_BIRTHDAY_INTERVAL": ROUND_BIRTHDAY_INTERVAL,
        "ics_token": ics_token,
    })


@router.get("/birthdays.ics")
async def birthdays_ics(request: Request, token: Optional[str] = Query(None), db: AsyncSession = Depends(get_db)):
    """Token-protected -- member birth dates are personal data and must
    never be reachable without the installation's secret token."""
    actual_token = await get_or_create_ics_token(db)
    if not verify_ics_token(token, actual_token):
        raise HTTPException(status_code=403, detail=t_for(request, "calendar.errors.invalid_token"))
    cal = await build_birthday_calendar(db, base_url=request.url.hostname or "parcella.local")
    return _ics_response(cal.to_ical(), "birthdays.ics")


# ---------------------------------------------------------------------------
# Council presence
# ---------------------------------------------------------------------------

@router.get("/council-presence", response_class=HTMLResponse)
async def council_presence_overview(request: Request, db: AsyncSession = Depends(get_db)):
    user = await require_user(request, db)

    result = await db.execute(
        select(CouncilPresence)
        .options(selectinload(CouncilPresence.user))
        .where(CouncilPresence.date >= date.today())
        .order_by(CouncilPresence.date)
    )
    entries = result.scalars().all()

    users_result = await db.execute(select(User).where(User.is_active == True).order_by(User.name))
    all_users = users_result.scalars().all()

    ics_token = await get_or_create_ics_token(db)
    return templates.TemplateResponse("calendar/council_presence.html", {
        "request": request, "user": user, "entries": entries, "all_users": all_users,
        "ics_token": ics_token,
    })


@router.post("/council-presence/new")
async def council_presence_create(
    request: Request,
    user_id: str = Form(...),
    presence_date: str = Form(...),
    time_from: str = Form(""),
    time_until: str = Form(""),
    note: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    await require_admin(request, db)
    entry = CouncilPresence(
        user_id=user_id,
        date=date.fromisoformat(presence_date),
        time_from=time_from or None,
        time_until=time_until or None,
        note=note.strip() or None,
    )
    db.add(entry)
    await db.commit()
    return RedirectResponse("/calendar/council-presence", status_code=302)


@router.post("/council-presence/{entry_id}/delete")
async def council_presence_delete(entry_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    await require_admin(request, db)
    result = await db.execute(select(CouncilPresence).where(CouncilPresence.id == entry_id))
    entry = result.scalar_one_or_none()
    if not entry:
        raise HTTPException(status_code=404, detail=t_for(request, "calendar.errors.entry_not_found"))
    await db.delete(entry)
    await db.commit()
    return RedirectResponse("/calendar/council-presence", status_code=302)


@router.get("/council-presence.ics")
async def council_presence_ics(request: Request, token: Optional[str] = Query(None), db: AsyncSession = Depends(get_db)):
    actual_token = await get_or_create_ics_token(db)
    if not verify_ics_token(token, actual_token):
        raise HTTPException(status_code=403, detail=t_for(request, "calendar.errors.invalid_token"))
    cal = await build_council_presence_calendar(db, base_url=request.url.hostname or "parcella.local")
    return _ics_response(cal.to_ical(), "council-presence.ics")


# ---------------------------------------------------------------------------
# Council absence
# ---------------------------------------------------------------------------

@router.get("/council-absence", response_class=HTMLResponse)
async def council_absence_overview(request: Request, db: AsyncSession = Depends(get_db)):
    user = await require_user(request, db)

    result = await db.execute(
        select(CouncilAbsence)
        .options(selectinload(CouncilAbsence.user))
        .where(CouncilAbsence.end_date >= date.today())
        .order_by(CouncilAbsence.start_date)
    )
    entries = result.scalars().all()

    ics_token = await get_or_create_ics_token(db)
    return templates.TemplateResponse("calendar/council_absence.html", {
        "request": request, "user": user, "entries": entries,
        "ics_token": ics_token,
    })


@router.post("/council-absence/new")
async def council_absence_create(
    request: Request,
    start_date: str = Form(...),
    end_date: str = Form(...),
    note: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    """Anyone with a system account can log their OWN absence -- there's
    no user_id form field; it's always the logged-in user, so nobody can
    log an absence on someone else's behalf."""
    user = await require_user(request, db)
    entry = CouncilAbsence(
        user_id=user.id,
        start_date=date.fromisoformat(start_date),
        end_date=date.fromisoformat(end_date),
        note=note.strip() or None,
    )
    db.add(entry)
    await db.commit()
    return RedirectResponse("/calendar/council-absence", status_code=302)


@router.post("/council-absence/{entry_id}/delete")
async def council_absence_delete(entry_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    """Anyone can delete their OWN absence entry; admin/board can delete
    anyone's (e.g. to clean up an entry someone else got wrong for them)."""
    user = await require_user(request, db)
    result = await db.execute(select(CouncilAbsence).where(CouncilAbsence.id == entry_id))
    entry = result.scalar_one_or_none()
    if not entry:
        raise HTTPException(status_code=404, detail=t_for(request, "calendar.errors.entry_not_found"))
    if entry.user_id != user.id and user.role.value not in ("admin", "board"):
        raise HTTPException(status_code=403, detail=t_for(request, "calendar.errors.not_your_entry"))
    await db.delete(entry)
    await db.commit()
    return RedirectResponse("/calendar/council-absence", status_code=302)


@router.get("/council-absence.ics")
async def council_absence_ics(request: Request, token: Optional[str] = Query(None), db: AsyncSession = Depends(get_db)):
    actual_token = await get_or_create_ics_token(db)
    if not verify_ics_token(token, actual_token):
        raise HTTPException(status_code=403, detail=t_for(request, "calendar.errors.invalid_token"))
    cal = await build_council_absence_calendar(db, base_url=request.url.hostname or "parcella.local")
    return _ics_response(cal.to_ical(), "council-absence.ics")
