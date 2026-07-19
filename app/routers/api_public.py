"""
Public signup API: lets an external CMS (WordPress, TYPO3, Contao, or
anything else) create work-session signups without a Parcella login.

Read endpoints (upcoming sessions, parcel list) are intentionally
unauthenticated -- the same posture as the public community ICS feed in
app/ics_utils.py, and for the same reason: an external site's frontend
can't send this app's session cookie, and the data exposed (session
dates/times, plot numbers) isn't sensitive on its own.

The write endpoint (signup) requires the shared API token (see
app/public_api_auth.py) plus a lightweight honeypot and per-IP rate
limit, since -- unlike the read endpoints -- it creates data and is a
much more attractive target for abuse.

Design note (this is the important part): the public form only ever
collects a PARCEL NUMBER, never a member name selected from a list --
the club's public website must not expose which members live on which
parcel. So a signup here creates real SessionParticipation rows
directly (status REGISTERED), matched by an optionally-submitted free-
text name against the parcel's current residents where that's
unambiguous, and falling back to registering EVERY current resident of
the parcel when it isn't (no name given, no match, or more than one
plausible match) -- overregistering and letting the board delete the
wrong ones from the normal participants table is safer than silently
registering nobody, or guessing wrong without a trace. See
docs/module-public-api.md for the full rationale and the reference
WordPress connector under integrations/wordpress/.
"""
import re
import time
import logging
from collections import defaultdict, deque
from typing import Dict, Deque, List

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models import (
    WorkSession, Parcel, ParcelStatus, MemberParcel, Member,
    SessionParticipation, ParticipationStatus,
)
from app.module_flags import require_modul
from app.public_api_auth import require_public_api_token
from app.schemas import (
    PublicWorkSessionOut, PublicParcelOut, PublicSignupCreate,
    PublicSignupResult, PublicSignupSessionResult,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/public",
    tags=["Public Signup API"],
    dependencies=[Depends(require_modul("public_signup_api"))],
)

# ---------------------------------------------------------------------------
# Simple in-memory sliding-window rate limit for the write endpoint, keyed
# by client IP. Deliberately not a new dependency (no Redis/slowapi) --
# this is a small, single-process app; a per-process in-memory limiter
# resets on deploy and doesn't share state across workers, which is an
# accepted tradeoff for a lightweight deterrent layered on top of the
# actual access control (the API token).
# ---------------------------------------------------------------------------
_RATE_LIMIT_WINDOW_SECONDS = 3600
_RATE_LIMIT_MAX_REQUESTS = 20
_recent_requests: Dict[str, Deque[float]] = defaultdict(deque)


def _check_rate_limit(client_ip: str) -> None:
    now = time.monotonic()
    window = _recent_requests[client_ip]
    while window and now - window[0] > _RATE_LIMIT_WINDOW_SECONDS:
        window.popleft()
    if len(window) >= _RATE_LIMIT_MAX_REQUESTS:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many signup requests from this address, please try again later",
        )
    window.append(now)


def _normalize_name(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip().lower()


def _find_matching_members(submitted_name: str, current_tenants: List[Member]) -> List[Member]:
    """Tries to match a free-text submitted name against the parcel's
    current residents. Only returns a match if exactly one tenant fits
    -- anything else (zero or multiple plausible matches) is the
    caller's cue to fall back to registering everyone, rather than
    guess."""
    if not submitted_name:
        return []
    target = _normalize_name(submitted_name)
    matches = []
    for member in current_tenants:
        forms = {
            _normalize_name(member.full_name),
            _normalize_name(f"{member.last_name} {member.first_name}"),
        }
        if target in forms:
            matches.append(member)
    return matches if len(matches) == 1 else []


def _build_note(parcel_number: str, payload: PublicSignupCreate, was_matched: bool, tenant_count: int) -> str:
    parts = []
    if was_matched:
        parts.append(f"Public signup, matched by name (parcel {parcel_number})")
    elif tenant_count > 1:
        parts.append(
            f"Public signup (parcel {parcel_number}) -- could not confidently match a "
            f"submitted name to one resident, so all {tenant_count} current residents of "
            f"this parcel were registered. Please verify and remove whoever didn't actually sign up."
        )
    else:
        parts.append(f"Public signup (parcel {parcel_number})")
    if payload.name:
        parts.append(f"Name given: {payload.name}")
    if payload.phone:
        parts.append(f"Phone: {payload.phone}")
    if payload.email:
        parts.append(f"Email: {payload.email}")
    if payload.remarks:
        parts.append(f"Remarks: {payload.remarks}")
    return " | ".join(parts)


@router.get("/work-sessions/upcoming", response_model=list[PublicWorkSessionOut])
async def list_upcoming_sessions(db: AsyncSession = Depends(get_db)):
    from datetime import date as date_cls

    result = await db.execute(
        select(WorkSession)
        .where(WorkSession.date >= date_cls.today())
        .options(selectinload(WorkSession.participations))
        .order_by(WorkSession.date, WorkSession.time_from)
    )
    sessions = result.scalars().all()
    return [
        PublicWorkSessionOut(
            id=s.id, title=s.title, date=s.date,
            time_from=s.time_from, time_until=s.time_until,
            spots_left=s.available_spots,
        )
        for s in sessions
        # Hide sessions that are already full, rather than showing a
        # dead-end option a visitor could still try to check.
        if s.available_spots is None or s.available_spots > 0
    ]


@router.get("/parcels", response_model=list[PublicParcelOut])
async def list_parcels(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Parcel).where(Parcel.status == ParcelStatus.ACTIVE).order_by(Parcel.plot_number)
    )
    return result.scalars().all()


@router.post(
    "/work-sessions/signup",
    response_model=PublicSignupResult,
    dependencies=[Depends(require_public_api_token)],
)
async def submit_signup(
    payload: PublicSignupCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    # Honeypot: a real visitor never fills this field. Return a
    # believable-looking success without creating anything, so the bot
    # doesn't learn its submission was rejected.
    if payload.website:
        logger.info("Public signup honeypot triggered, silently ignoring submission")
        return PublicSignupResult(results=[
            PublicSignupSessionResult(session_id=sid, accepted=True) for sid in payload.session_ids
        ])

    _check_rate_limit(request.client.host if request.client else "unknown")

    parcel_result = await db.execute(
        select(Parcel).where(Parcel.plot_number == payload.parcel_number)
    )
    parcel = parcel_result.scalar_one_or_none()
    if not parcel:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown parcel number")

    tenants_result = await db.execute(
        select(MemberParcel)
        .options(selectinload(MemberParcel.member))
        .where(MemberParcel.parcel_id == parcel.id, MemberParcel.assigned_until.is_(None))
    )
    current_tenants = [
        mp.member for mp in tenants_result.scalars().all()
        if mp.member and mp.member.deleted_at is None
    ]

    matched = _find_matching_members(payload.name, current_tenants)
    was_matched = bool(matched)
    members_to_register = matched if was_matched else current_tenants

    sessions_result = await db.execute(
        select(WorkSession)
        .where(WorkSession.id.in_(payload.session_ids))
        .options(selectinload(WorkSession.participations))
    )
    sessions_by_id = {s.id: s for s in sessions_result.scalars().all()}

    results: list[PublicSignupSessionResult] = []
    any_created = False

    if not members_to_register:
        for session_id in payload.session_ids:
            results.append(PublicSignupSessionResult(
                session_id=session_id, accepted=False,
                reason="No members are currently assigned to this parcel",
            ))
        return PublicSignupResult(results=results)

    note = _build_note(parcel.plot_number, payload, was_matched, len(current_tenants))

    for session_id in payload.session_ids:
        session = sessions_by_id.get(session_id)
        if not session:
            results.append(PublicSignupSessionResult(session_id=session_id, accepted=False, reason="Session not found"))
            continue

        already_registered_member_ids = {p.member_id for p in session.participations}
        to_create = [m for m in members_to_register if m.id not in already_registered_member_ids]

        if session.available_spots is not None and session.available_spots < len(to_create):
            results.append(PublicSignupSessionResult(session_id=session_id, accepted=False, reason="Session is full"))
            continue

        for member in to_create:
            db.add(SessionParticipation(
                session_id=session.id, member_id=member.id,
                status=ParticipationStatus.REGISTERED, note=note,
            ))
            any_created = True

        results.append(PublicSignupSessionResult(session_id=session_id, accepted=True))

    if any_created:
        await db.commit()
    else:
        await db.rollback()

    return PublicSignupResult(results=results)
