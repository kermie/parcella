"""
Work hours router: work sessions, sponsorships, club roles, configuration.
"""
import csv
import io
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional, List

from fastapi import APIRouter, Request, Form, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_
from sqlalchemy.orm import selectinload

from app.database import get_db, active_member_filter
from app.models import (
    WorkSession, SessionParticipation, SessionType, ParticipationStatus,
    Sponsorship, ClubRole, MemberClubRole, ExemptionReason,
    WorkHoursConfiguration, WorkHoursMode,
    Member, MemberParcel, Parcel, ParcelStatus,
    WorkTask, TaskWorkload,
)
from app.auth import require_user
from app.i18n import t_for
from app.branding import load_branding
from app.l10n import load_current_region, format_number
from app.session_attendee_sheet import render_session_attendee_sheet_pdf, AttendeeRow

from app.module_flags import require_module

router = APIRouter(
    prefix="/work-hours",
    tags=["work-hours"],
    dependencies=[Depends(require_module("work_hours"))],
)
from app.templating import templates


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

async def _get_config_for_year(db: AsyncSession, year: int) -> Optional[WorkHoursConfiguration]:
    result = await db.execute(
        select(WorkHoursConfiguration).where(WorkHoursConfiguration.year == year)
    )
    return result.scalar_one_or_none()


async def _calculate_hours_for_member(
    db: AsyncSession, member_id: str, year: int
) -> dict:
    """Calculates a member's required-work-hours standing for a year."""

    # Session participations (only ATTENDED counts)
    session_hours = await db.scalar(
        select(func.coalesce(func.sum(SessionParticipation.hours_completed), 0))
        .join(WorkSession)
        .where(
            SessionParticipation.member_id == member_id,
            SessionParticipation.status == ParticipationStatus.ATTENDED,
            func.extract("year", WorkSession.date) == year,
        )
    ) or 0

    # Sponsorship (active in the queried year)
    sponsorship_hours = await db.scalar(
        select(func.coalesce(func.sum(Sponsorship.credited_hours), 0))
        .where(
            Sponsorship.member_id == member_id,
            Sponsorship.valid_from <= date(year, 12, 31),
            (Sponsorship.valid_until.is_(None)) | (Sponsorship.valid_until >= date(year, 1, 1)),
        )
    ) or 0

    return {
        "session_hours": float(session_hours),
        "sponsorship_hours": float(sponsorship_hours),
        "total": float(session_hours) + float(sponsorship_hours),
    }


async def _is_exempt(db: AsyncSession, member_id: str, year: int) -> bool:
    """Checks whether a member is exempt from required work hours for a year."""
    result = await db.execute(
        select(MemberClubRole)
        .join(ClubRole, MemberClubRole.club_role_id == ClubRole.id)
        .where(
            MemberClubRole.member_id == member_id,
            MemberClubRole.year == year,
            ClubRole.hours_exempt == True,
        )
    )
    return result.scalar_one_or_none() is not None


# ---------------------------------------------------------------------------
# Dashboard / Overview
# ---------------------------------------------------------------------------

@router.get("/", response_class=HTMLResponse)
async def work_hours_overview(
    request: Request,
    year: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
):
    user = await require_user(request, db)

    if not year:
        year = date.today().year

    config = await _get_config_for_year(db, year)

    # All available years, for the dropdown
    years_result = await db.execute(
        select(WorkHoursConfiguration.year).order_by(WorkHoursConfiguration.year.desc())
    )
    available_years = [r[0] for r in years_result.all()]

    # Sessions of the year
    sessions_result = await db.execute(
        select(WorkSession)
        .options(selectinload(WorkSession.participations))
        .where(func.extract("year", WorkSession.date) == year)
        .order_by(WorkSession.date.desc())
    )
    sessions = sessions_result.scalars().all()

    return templates.TemplateResponse(
        "work_hours/overview.html",
        {
            "request": request,
            "user": user,
            "year": year,
            "config": config,
            "sessions": sessions,
            "available_years": available_years,
            "SessionType": SessionType,
            "ParticipationStatus": ParticipationStatus,
        },
    )


# ---------------------------------------------------------------------------
# Work hours configuration
# ---------------------------------------------------------------------------

@router.get("/configuration", response_class=HTMLResponse)
async def configuration_page(request: Request, db: AsyncSession = Depends(get_db)):
    user = await require_user(request, db)

    result = await db.execute(
        select(WorkHoursConfiguration).order_by(WorkHoursConfiguration.year.desc())
    )
    configurations = result.scalars().all()

    return templates.TemplateResponse(
        "work_hours/configuration.html",
        {
            "request": request,
            "user": user,
            "configurations": configurations,
            "WorkHoursMode": WorkHoursMode,
            "current_year": date.today().year,
        },
    )


@router.get("/configuration/{configuration_id}/edit", response_class=HTMLResponse)
async def configuration_edit_page(
    configuration_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await require_user(request, db)

    result = await db.execute(
        select(WorkHoursConfiguration).where(WorkHoursConfiguration.id == configuration_id)
    )
    configuration = result.scalar_one_or_none()
    if not configuration:
        raise HTTPException(status_code=404, detail=t_for(request, "work_hours.errors.configuration_not_found"))

    return templates.TemplateResponse(
        "work_hours/configuration_form.html",
        {
            "request": request,
            "user": user,
            "configuration": configuration,
            "WorkHoursMode": WorkHoursMode,
        },
    )


@router.post("/configuration/{configuration_id}/edit")
async def configuration_update(
    configuration_id: str,
    request: Request,
    year: int = Form(...),
    hours_required: str = Form(...),
    rate_per_hour_eur: str = Form(...),
    mode: str = Form("PER_PARCEL"),
    note: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    await require_user(request, db)

    result = await db.execute(
        select(WorkHoursConfiguration).where(WorkHoursConfiguration.id == configuration_id)
    )
    configuration = result.scalar_one_or_none()
    if not configuration:
        raise HTTPException(status_code=404, detail=t_for(request, "work_hours.errors.configuration_not_found"))

    # If the year is being changed: check for a collision with another entry
    if year != configuration.year:
        kollision = await _get_config_for_year(db, year)
        if kollision and kollision.id != configuration_id:
            raise HTTPException(
                status_code=400,
                detail=t_for(request, "work_hours.errors.configuration_year_exists", year=year)
            )

    configuration.year = year
    configuration.hours_required = float(hours_required.replace(",", "."))
    configuration.rate_per_hour_eur = float(rate_per_hour_eur.replace(",", "."))
    configuration.mode = WorkHoursMode(mode)
    configuration.note = note.strip() or None

    await db.commit()
    return RedirectResponse("/work-hours/configuration", status_code=302)


@router.post("/configuration/{configuration_id}/delete")
async def configuration_delete(
    configuration_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    await require_user(request, db)

    result = await db.execute(
        select(WorkHoursConfiguration).where(WorkHoursConfiguration.id == configuration_id)
    )
    configuration = result.scalar_one_or_none()
    if configuration:
        await db.delete(configuration)
        await db.commit()

    return RedirectResponse("/work-hours/configuration", status_code=302)


@router.post("/configuration/new")
async def configuration_create(
    request: Request,
    year: int = Form(...),
    hours_required: str = Form(...),
    rate_per_hour_eur: str = Form(...),
    mode: str = Form("PER_PARCEL"),
    note: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    await require_user(request, db)

    existing = await _get_config_for_year(db, year)
    if existing:
        existing.hours_required = float(hours_required.replace(",", "."))
        existing.rate_per_hour_eur = float(rate_per_hour_eur.replace(",", "."))
        existing.mode = WorkHoursMode(mode)
        existing.note = note.strip() or None
    else:
        config = WorkHoursConfiguration(
            year=year,
            hours_required=float(hours_required.replace(",", ".")),
            rate_per_hour_eur=float(rate_per_hour_eur.replace(",", ".")),
            mode=WorkHoursMode(mode),
            note=note.strip() or None,
        )
        db.add(config)

    await db.commit()
    return RedirectResponse("/work-hours/configuration", status_code=302)


# ---------------------------------------------------------------------------
# Work Sessions
# ---------------------------------------------------------------------------

@router.get("/sessions/new", response_class=HTMLResponse)
async def session_new_page(request: Request, db: AsyncSession = Depends(get_db)):
    user = await require_user(request, db)
    return templates.TemplateResponse(
        "work_hours/session_form.html",
        {
            "request": request,
            "user": user,
            "session": None,
            "SessionType": SessionType,
        },
    )


@router.post("/sessions/new")
async def session_create(
    request: Request,
    title: str = Form(...),
    description: str = Form(""),
    type: str = Form("STANDARD"),
    date_value: str = Form(..., alias="date"),
    time_from: str = Form(""),
    time_until: str = Form(""),
    max_participants: str = Form(""),
    hours_per_participant: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    user = await require_user(request, db)

    session = WorkSession(
        title=title.strip(),
        description=description.strip() or None,
        type=SessionType(type),
        date=date.fromisoformat(date_value),
        time_from=time_from.strip() or None,
        time_until=time_until.strip() or None,
        max_participants=int(max_participants) if max_participants.strip() else None,
        hours_per_participant=float(hours_per_participant.replace(",", ".")) if hours_per_participant.strip() else None,
        created_by_id=user.id,
    )
    db.add(session)
    await db.commit()
    return RedirectResponse(f"/work-hours/sessions/{session.id}", status_code=302)


@router.get("/sessions/{session_id}/edit", response_class=HTMLResponse)
async def session_edit_page(
    session_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await require_user(request, db)

    result = await db.execute(select(WorkSession).where(WorkSession.id == session_id))
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail=t_for(request, "work_hours.errors.session_not_found"))

    return templates.TemplateResponse(
        "work_hours/session_form.html",
        {
            "request": request,
            "user": user,
            "session": session,
            "SessionType": SessionType,
        },
    )


@router.post("/sessions/{session_id}/edit")
async def session_update(
    session_id: str,
    request: Request,
    title: str = Form(...),
    description: str = Form(""),
    type: str = Form("STANDARD"),
    date_value: str = Form(..., alias="date"),
    time_from: str = Form(""),
    time_until: str = Form(""),
    max_participants: str = Form(""),
    hours_per_participant: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    await require_user(request, db)

    result = await db.execute(select(WorkSession).where(WorkSession.id == session_id))
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail=t_for(request, "work_hours.errors.session_not_found"))

    session.title = title.strip()
    session.description = description.strip() or None
    session.type = SessionType(type)
    session.date = date.fromisoformat(date_value)
    session.time_from = time_from.strip() or None
    session.time_until = time_until.strip() or None
    session.max_participants = int(max_participants) if max_participants.strip() else None
    session.hours_per_participant = (
        float(hours_per_participant.replace(",", ".")) if hours_per_participant.strip() else None
    )

    await db.commit()
    return RedirectResponse(f"/work-hours/sessions/{session_id}", status_code=302)


@router.post("/sessions/{session_id}/delete")
async def session_delete(
    session_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    await require_user(request, db)

    result = await db.execute(select(WorkSession).where(WorkSession.id == session_id))
    session = result.scalar_one_or_none()
    if session:
        year = session.date.year
        await db.delete(session)
        await db.commit()
        return RedirectResponse(f"/work-hours/?year={year}", status_code=302)

    return RedirectResponse("/work-hours/", status_code=302)


@router.get("/sessions/{session_id}", response_class=HTMLResponse)
async def session_detail(
    session_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await require_user(request, db)

    result = await db.execute(
        select(WorkSession)
        .options(
            selectinload(WorkSession.participations).selectinload(SessionParticipation.member)
        )
        .where(WorkSession.id == session_id)
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail=t_for(request, "work_hours.errors.session_not_found"))

    # All active members, for the signup dropdown
    members_result = await db.execute(
        select(Member)
        .where(active_member_filter())
        .order_by(Member.last_name, Member.first_name)
    )
    all_members = members_result.scalars().all()
    already_registered = {t.member_id for t in session.participations}

    tasks_result = await db.execute(
        select(WorkTask)
        .options(selectinload(WorkTask.assigned_participation).selectinload(SessionParticipation.member))
        .where(WorkTask.session_id == session_id)
        .order_by(WorkTask.is_done, WorkTask.created_at)
    )
    session_tasks = tasks_result.scalars().all()

    return templates.TemplateResponse(
        "work_hours/session_detail.html",
        {
            "request": request,
            "user": user,
            "session": session,
            "all_members": all_members,
            "already_registered": already_registered,
            "ParticipationStatus": ParticipationStatus,
            "SessionType": SessionType,
            "session_tasks": session_tasks,
            "TaskWorkload": TaskWorkload,
        },
    )


@router.get("/sessions/{session_id}/attendee-sheet")
async def session_attendee_sheet_pdf(
    session_id: str, request: Request, db: AsyncSession = Depends(get_db),
):
    """Generates the attendee sheet PDF for this session: registered
    participants with parcel, expected hours, any task assigned to
    them for this session, and a blank signature line -- meant for
    printing and bringing to the actual session so the coordinator can
    confirm attendance/hours on paper. Multi-page, like the general-
    meeting sign-in sheet (app/meeting_signin_sheet.py) -- a big
    session can have more attendees than fit on one page."""
    await require_user(request, db)

    result = await db.execute(
        select(WorkSession)
        .options(
            selectinload(WorkSession.participations)
            .selectinload(SessionParticipation.member)
            .selectinload(Member.parcel_assignments)
            .selectinload(MemberParcel.parcel)
        )
        .where(WorkSession.id == session_id)
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail=t_for(request, "work_hours.errors.session_not_found"))

    # One task can be assigned to a participation; a participant could
    # in principle have more than one (e.g. two small tasks) -- collect
    # all of them per participation rather than assuming exactly one.
    tasks_result = await db.execute(
        select(WorkTask).where(WorkTask.session_id == session_id, WorkTask.assigned_participation_id.isnot(None))
    )
    tasks_by_participation = {}
    for task in tasks_result.scalars().all():
        tasks_by_participation.setdefault(task.assigned_participation_id, []).append(task.title)

    region = await load_current_region(db)

    def current_parcel_numbers(member: Member) -> str:
        current = [pa.parcel.plot_number for pa in member.parcel_assignments if pa.assigned_until is None]
        return "; ".join(current)

    def sort_key(participation: SessionParticipation):
        parcels = current_parcel_numbers(participation.member)
        return (parcels, participation.member.last_name, participation.member.first_name)

    rows = []
    for participation in sorted(session.participations, key=sort_key):
        hours_value = participation.hours_completed
        if hours_value is None:
            hours_value = session.hours_per_participant
        hours_text = format_number(hours_value, region, decimals=1) if hours_value is not None else ""

        task_titles = tasks_by_participation.get(participation.id, [])

        rows.append(AttendeeRow(
            parcel=current_parcel_numbers(participation.member),
            member_name=participation.member.full_name,
            hours=hours_text,
            tasks="; ".join(task_titles),  # left blank if none assigned yet, per request
        ))

    subtitle_parts = [session.date.isoformat()]
    if session.time_from:
        time_range = session.time_from + (f" - {session.time_until}" if session.time_until else "")
        subtitle_parts.append(time_range)
    subtitle = ", ".join(subtitle_parts)

    branding = await load_branding(db)
    logo_path = Path("app" + branding["logo_url"]) if branding["logo_url"] else None

    pdf_bytes = render_session_attendee_sheet_pdf(
        session.title, subtitle, branding["club_name"], logo_path, rows,
    )

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="attendee-sheet.pdf"'},
    )


@router.post("/sessions/{session_id}/participants/add")
async def participant_add(
    session_id: str,
    request: Request,
    member_id: str = Form(...),
    status: str = Form("ATTENDED"),
    hours_completed: str = Form(""),
    note: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    await require_user(request, db)

    # Already registered?
    existing = await db.execute(
        select(SessionParticipation).where(
            SessionParticipation.session_id == session_id,
            SessionParticipation.member_id == member_id,
        )
    )
    if existing.scalar_one_or_none():
        return RedirectResponse(f"/work-hours/sessions/{session_id}", status_code=302)

    participation = SessionParticipation(
        session_id=session_id,
        member_id=member_id,
        status=ParticipationStatus(status),
        hours_completed=float(hours_completed.replace(",", ".")) if hours_completed.strip() else None,
        note=note.strip() or None,
    )
    db.add(participation)
    await db.commit()
    return RedirectResponse(f"/work-hours/sessions/{session_id}", status_code=302)


@router.post("/sessions/{session_id}/participants/{participation_id}/status")
async def participation_status_change(
    session_id: str,
    participation_id: str,
    request: Request,
    status: str = Form(...),
    hours_completed: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    await require_user(request, db)

    result = await db.execute(
        select(SessionParticipation).where(SessionParticipation.id == participation_id)
    )
    participation = result.scalar_one_or_none()
    if participation:
        participation.status = ParticipationStatus(status)
        if hours_completed.strip():
            participation.hours_completed = float(hours_completed.replace(",", "."))
        await db.commit()

    return RedirectResponse(f"/work-hours/sessions/{session_id}", status_code=302)


@router.post("/sessions/{session_id}/participants/{participation_id}/remove")
async def participation_remove(
    session_id: str,
    participation_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    await require_user(request, db)

    result = await db.execute(
        select(SessionParticipation).where(SessionParticipation.id == participation_id)
    )
    participation = result.scalar_one_or_none()
    if participation:
        await db.delete(participation)
        await db.commit()

    return RedirectResponse(f"/work-hours/sessions/{session_id}", status_code=302)


# ---------------------------------------------------------------------------
# Club Roles
# ---------------------------------------------------------------------------

@router.get("/club-roles", response_class=HTMLResponse)
async def club_roles_page(
    request: Request,
    year: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
):
    user = await require_user(request, db)

    if not year:
        year = date.today().year

    roles_result = await db.execute(
        select(ClubRole).order_by(ClubRole.name)
    )
    roles = roles_result.scalars().all()

    assignments_result = await db.execute(
        select(MemberClubRole)
        .options(
            selectinload(MemberClubRole.member),
            selectinload(MemberClubRole.club_role),
        )
        .where(MemberClubRole.year == year)
        .order_by(MemberClubRole.club_role_id)
    )
    assignments = assignments_result.scalars().all()

    members_result = await db.execute(
        select(Member)
        .where(active_member_filter())
        .order_by(Member.last_name, Member.first_name)
    )
    all_members = members_result.scalars().all()

    return templates.TemplateResponse(
        "work_hours/club-roles.html",
        {
            "request": request,
            "user": user,
            "roles": roles,
            "assignments": assignments,
            "all_members": all_members,
            "year": year,
            "ExemptionReason": ExemptionReason,
            "current_year": date.today().year,
        },
    )


@router.post("/club-roles/assign-member")
async def member_club_role_assign(
    request: Request,
    member_id: str = Form(...),
    club_role_id: str = Form(...),
    year: int = Form(...),
    valid_from: str = Form(""),
    valid_until: str = Form(""),
    note: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    await require_user(request, db)

    existing = await db.execute(
        select(MemberClubRole).where(
            MemberClubRole.member_id == member_id,
            MemberClubRole.club_role_id == club_role_id,
            MemberClubRole.year == year,
        )
    )
    if not existing.scalar_one_or_none():
        assignment = MemberClubRole(
            member_id=member_id,
            club_role_id=club_role_id,
            year=year,
            valid_from=date.fromisoformat(valid_from) if valid_from.strip() else None,
            valid_until=date.fromisoformat(valid_until) if valid_until.strip() else None,
            note=note.strip() or None,
        )
        db.add(assignment)
        await db.commit()

    return RedirectResponse(f"/work-hours/club-roles?year={year}", status_code=302)


@router.get("/club-roles/assignment/{assignment_id}/edit", response_class=HTMLResponse)
async def member_club_role_edit_page(
    assignment_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await require_user(request, db)

    result = await db.execute(
        select(MemberClubRole)
        .options(
            selectinload(MemberClubRole.member),
            selectinload(MemberClubRole.club_role),
        )
        .where(MemberClubRole.id == assignment_id)
    )
    assignment = result.scalar_one_or_none()
    if not assignment:
        raise HTTPException(status_code=404, detail=t_for(request, "work_hours.errors.assignment_not_found"))

    members_result = await db.execute(
        select(Member)
        .where(active_member_filter())
        .order_by(Member.last_name, Member.first_name)
    )
    all_members = members_result.scalars().all()

    roles_result = await db.execute(select(ClubRole).order_by(ClubRole.name))
    all_roles = roles_result.scalars().all()

    return templates.TemplateResponse(
        "work_hours/member_club_role_form.html",
        {
            "request": request,
            "user": user,
            "assignment": assignment,
            "all_members": all_members,
            "all_roles": all_roles,
        },
    )


@router.post("/club-roles/assignment/{assignment_id}/edit")
async def member_club_role_update(
    assignment_id: str,
    request: Request,
    member_id: str = Form(...),
    club_role_id: str = Form(...),
    year: int = Form(...),
    valid_from: str = Form(""),
    valid_until: str = Form(""),
    note: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    await require_user(request, db)

    result = await db.execute(
        select(MemberClubRole).where(MemberClubRole.id == assignment_id)
    )
    assignment = result.scalar_one_or_none()
    if not assignment:
        raise HTTPException(status_code=404, detail=t_for(request, "work_hours.errors.assignment_not_found"))

    assignment.member_id = member_id
    assignment.club_role_id = club_role_id
    assignment.year = year
    assignment.valid_from = date.fromisoformat(valid_from) if valid_from.strip() else None
    assignment.valid_until = date.fromisoformat(valid_until) if valid_until.strip() else None
    assignment.note = note.strip() or None

    await db.commit()
    return RedirectResponse(f"/work-hours/club-roles?year={year}", status_code=302)


@router.post("/club-roles/assignment/{assignment_id}/remove")
async def member_club_role_remove(
    assignment_id: str,
    request: Request,
    year: int = Form(0),
    db: AsyncSession = Depends(get_db),
):
    await require_user(request, db)

    result = await db.execute(
        select(MemberClubRole).where(MemberClubRole.id == assignment_id)
    )
    assignment = result.scalar_one_or_none()
    rueck_jahr = assignment.year if assignment else date.today().year
    if assignment:
        await db.delete(assignment)
        await db.commit()

    return RedirectResponse(f"/work-hours/club-roles?year={rueck_jahr}", status_code=302)


@router.post("/club-roles/new")
async def club_role_create(
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    hours_exempt: bool = Form(False),
    exemption_reason: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    await require_user(request, db)

    role = ClubRole(
        name=name.strip(),
        description=description.strip() or None,
        hours_exempt=hours_exempt,
        exemption_reason=ExemptionReason(exemption_reason) if exemption_reason else None,
    )
    db.add(role)
    await db.commit()
    return RedirectResponse("/work-hours/club-roles", status_code=302)


@router.get("/club-roles/{role_id}/edit", response_class=HTMLResponse)
async def club_role_edit_page(
    role_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await require_user(request, db)

    result = await db.execute(select(ClubRole).where(ClubRole.id == role_id))
    role = result.scalar_one_or_none()
    if not role:
        raise HTTPException(status_code=404, detail=t_for(request, "work_hours.errors.club_role_not_found"))

    return templates.TemplateResponse(
        "work_hours/club_role_form.html",
        {
            "request": request,
            "user": user,
            "role": role,
        },
    )


@router.post("/club-roles/{role_id}/edit")
async def club_role_update(
    role_id: str,
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    hours_exempt: bool = Form(False),
    exemption_reason: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    await require_user(request, db)

    result = await db.execute(select(ClubRole).where(ClubRole.id == role_id))
    role = result.scalar_one_or_none()
    if role:
        role.name = name.strip()
        role.description = description.strip() or None
        role.hours_exempt = hours_exempt
        role.exemption_reason = ExemptionReason(exemption_reason) if exemption_reason else None
        await db.commit()

    return RedirectResponse("/work-hours/club-roles", status_code=302)


@router.post("/club-roles/{role_id}/delete")
async def club_role_delete(
    role_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    await require_user(request, db)
    result = await db.execute(select(ClubRole).where(ClubRole.id == role_id))
    role = result.scalar_one_or_none()
    if role:
        await db.delete(role)
        await db.commit()
    return RedirectResponse("/work-hours/club-roles", status_code=302)


# ---------------------------------------------------------------------------
# Sponsorships
# ---------------------------------------------------------------------------

@router.get("/sponsorships", response_class=HTMLResponse)
async def sponsorships_page(
    request: Request,
    year: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
):
    user = await require_user(request, db)

    if not year:
        year = date.today().year

    query = (
        select(Sponsorship)
        .options(selectinload(Sponsorship.member))
        .where(
            Sponsorship.valid_from <= date(year, 12, 31),
            (Sponsorship.valid_until.is_(None)) | (Sponsorship.valid_until >= date(year, 1, 1)),
        )
        .order_by(Sponsorship.area)
    )
    result = await db.execute(query)
    sponsorships = result.scalars().all()

    # Group by area, so multiple members per area are shown together
    grouped_areas = {}
    for p in sponsorships:
        grouped_areas.setdefault(p.area, []).append(p)

    # All known area names (for autocomplete, including past years, to
    # avoid typos when reusing one)
    alle_bereiche_result = await db.execute(
        select(Sponsorship.area).distinct().order_by(Sponsorship.area)
    )
    all_areas = [r[0] for r in alle_bereiche_result.all()]

    # Current work-hours configuration, for pre-filling
    config = await _get_config_for_year(db, year)

    members_result = await db.execute(
        select(Member)
        .where(active_member_filter())
        .order_by(Member.last_name, Member.first_name)
    )
    all_members = members_result.scalars().all()

    return templates.TemplateResponse(
        "work_hours/sponsorships.html",
        {
            "request": request,
            "user": user,
            "sponsorships": sponsorships,
            "grouped_areas": grouped_areas,
            "all_areas": all_areas,
            "config": config,
            "all_members": all_members,
            "year": year,
        },
    )


@router.post("/sponsorships/new")
async def sponsorship_create(
    request: Request,
    member_id: str = Form(""),
    area: str = Form(...),
    description: str = Form(""),
    credited_hours: str = Form(...),
    valid_from: str = Form(...),
    valid_until: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    await require_user(request, db)

    sponsorship = Sponsorship(
        member_id=member_id.strip() or None,
        area=area.strip(),
        description=description.strip() or None,
        credited_hours=float(credited_hours.replace(",", ".")),
        valid_from=date.fromisoformat(valid_from),
        valid_until=date.fromisoformat(valid_until) if valid_until.strip() else None,
    )
    db.add(sponsorship)
    await db.commit()
    return RedirectResponse("/work-hours/sponsorships", status_code=302)


@router.get("/sponsorships/{sponsorship_id}/edit", response_class=HTMLResponse)
async def sponsorship_edit_page(
    sponsorship_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await require_user(request, db)

    result = await db.execute(
        select(Sponsorship)
        .options(selectinload(Sponsorship.member))
        .where(Sponsorship.id == sponsorship_id)
    )
    sponsorship = result.scalar_one_or_none()
    if not sponsorship:
        raise HTTPException(status_code=404, detail=t_for(request, "work_hours.errors.sponsorship_not_found"))

    members_result = await db.execute(
        select(Member)
        .where(active_member_filter())
        .order_by(Member.last_name, Member.first_name)
    )
    all_members = members_result.scalars().all()

    alle_bereiche_result = await db.execute(
        select(Sponsorship.area).distinct().order_by(Sponsorship.area)
    )
    all_areas = [r[0] for r in alle_bereiche_result.all()]

    return templates.TemplateResponse(
        "work_hours/sponsorship_form.html",
        {
            "request": request,
            "user": user,
            "sponsorship": sponsorship,
            "all_members": all_members,
            "all_areas": all_areas,
        },
    )


@router.post("/sponsorships/{sponsorship_id}/edit")
async def sponsorship_update(
    sponsorship_id: str,
    request: Request,
    member_id: str = Form(""),
    area: str = Form(...),
    description: str = Form(""),
    credited_hours: str = Form(...),
    valid_from: str = Form(...),
    valid_until: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    await require_user(request, db)

    result = await db.execute(select(Sponsorship).where(Sponsorship.id == sponsorship_id))
    sponsorship = result.scalar_one_or_none()
    if not sponsorship:
        raise HTTPException(status_code=404, detail=t_for(request, "work_hours.errors.sponsorship_not_found"))

    sponsorship.member_id = member_id.strip() or None
    sponsorship.area = area.strip()
    sponsorship.description = description.strip() or None
    sponsorship.credited_hours = float(credited_hours.replace(",", "."))
    sponsorship.valid_from = date.fromisoformat(valid_from)
    sponsorship.valid_until = date.fromisoformat(valid_until) if valid_until.strip() else None

    await db.commit()

    year = sponsorship.valid_from.year
    return RedirectResponse(f"/work-hours/sponsorships?year={year}", status_code=302)


@router.post("/sponsorships/{sponsorship_id}/delete")
async def sponsorship_delete(
    sponsorship_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    await require_user(request, db)
    result = await db.execute(select(Sponsorship).where(Sponsorship.id == sponsorship_id))
    sponsorship = result.scalar_one_or_none()
    if sponsorship:
        await db.delete(sponsorship)
        await db.commit()
    return RedirectResponse("/work-hours/sponsorships", status_code=302)


# ---------------------------------------------------------------------------
# Evaluation: annual standing per member/parcel
# ---------------------------------------------------------------------------

@router.get("/evaluation", response_class=HTMLResponse)
async def evaluation(
    request: Request,
    year: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
):
    user = await require_user(request, db)

    if not year:
        year = date.today().year

    config = await _get_config_for_year(db, year)

    years_result = await db.execute(
        select(WorkHoursConfiguration.year).order_by(WorkHoursConfiguration.year.desc())
    )
    available_years = [r[0] for r in years_result.all()]

    if not config:
        return templates.TemplateResponse(
            "work_hours/evaluation.html",
            {
                "request": request,
                "user": user,
                "year": year,
                "config": None,
                "rows": [],
                "available_years": available_years,
            },
        )

    rows = []

    if config.mode == WorkHoursMode.PER_PARCEL:
        # Evaluate per parcel -- all active parcels with tenants
        parcels_result = await db.execute(
            select(Parcel)
            .options(
                selectinload(Parcel.member_assignments).selectinload(MemberParcel.member)
            )
            .where(Parcel.status == ParcelStatus.ACTIVE)
            .order_by(Parcel.plot_number)
        )
        parcels = parcels_result.scalars().all()

        for parcel in parcels:
            tenants = [
                z.member for z in parcel.member_assignments
                if z.member.deleted_at is None
                and (z.member.member_until is None or z.member.member_until >= date.today())
            ]
            if not tenants:
                continue  # skip vacant parcels or those with only inactive tenants

            # Sum hours across all tenants
            total_hours = 0.0
            tenant_details = []
            for m in tenants:
                hours = await _calculate_hours_for_member(db, m.id, year)
                exempt = await _is_exempt(db, m.id, year)
                total_hours += hours["total"]
                tenant_details.append({
                    "member": m,
                    "hours": hours,
                    "exempt": exempt,
                })

            required = float(config.hours_required)
            outstanding = max(0.0, required - total_hours)
            amount_due = outstanding * float(config.rate_per_hour_eur)

            # Exempt if AT LEAST ONE tenant is exempt (any(), not all() --
            # see docs/architecture-decisions.md). Deliberately NOT called
            # "all_exempt" -- that name once led to an inverted all()-copy
            # bug in the CSV export and the API.
            is_exempt = any(p["exempt"] for p in tenant_details)

            rows.append({
                "parcel": parcel,
                "tenant_details": tenant_details,
                "total_hours": total_hours,
                "required_hours": required,
                "outstanding_hours": outstanding if not is_exempt else 0.0,
                "amount_due": amount_due if not is_exempt else 0.0,
                "fulfilled": is_exempt or total_hours >= required,
                "all_exempt": is_exempt,
                "exempt": is_exempt,  # unified key for the template
            })

    else:
        # PER_MEMBER: evaluate each member with a parcel individually
        members_result = await db.execute(
            select(Member)
            .options(selectinload(Member.parcel_assignments))
            .where(
                Member.deleted_at.is_(None),
                Member.parcel_assignments.any(),
            )
            .order_by(Member.last_name, Member.first_name)
        )
        members = members_result.scalars().all()

        for m in members:
            hours = await _calculate_hours_for_member(db, m.id, year)
            exempt = await _is_exempt(db, m.id, year)
            required = float(config.hours_required)
            outstanding = max(0.0, required - hours["total"])
            amount_due = outstanding * float(config.rate_per_hour_eur)

            rows.append({
                "member": m,
                "hours": hours,
                "exempt": exempt,
                "required_hours": required,
                "outstanding_hours": outstanding if not exempt else 0.0,
                "amount_due": amount_due if not exempt else 0.0,
                "fulfilled": exempt or hours["total"] >= required,
            })

    return templates.TemplateResponse(
        "work_hours/evaluation.html",
        {
            "request": request,
            "user": user,
            "year": year,
            "config": config,
            "rows": rows,
            "available_years": available_years,
            "WorkHoursMode": WorkHoursMode,
        },
    )


@router.get("/evaluation/csv")
async def evaluation_export_csv(
    request: Request,
    year: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
):
    await require_user(request, db)

    if not year:
        year = date.today().year

    config = await _get_config_for_year(db, year)
    if not config:
        raise HTTPException(status_code=404, detail=t_for(request, "work_hours.errors.no_configuration_for_year", year=year))

    output = io.StringIO()
    writer = csv.writer(output, delimiter=";")
    writer.writerow([
        "Parcel", "Pächter", "Pflicht (h)", "Geleistet (h)",
        "Sponsorship (h)", "Gesamt (h)", "Offen (h)",
        "Schuldbetrag (EUR)", "Befreit", "Erfüllt"
    ])

    if config.mode == WorkHoursMode.PER_PARCEL:
        parzellen_result = await db.execute(
            select(Parcel)
            .options(selectinload(Parcel.member_assignments).selectinload(MemberParcel.member))
            .where(Parcel.status == ParcelStatus.ACTIVE)
            .order_by(Parcel.plot_number)
        )
        for parzelle in parzellen_result.scalars().all():
            paechter = [
                z.member for z in parzelle.member_assignments
                if z.member.deleted_at is None
                and (z.member.member_until is None or z.member.member_until >= date.today())
            ]
            if not paechter:
                continue
            gesamt = 0.0
            einsatz_h = 0.0
            paten_h = 0.0
            # Same rule as the evaluation page: ONE exempt tenant is enough
            # to exempt the whole parcel (any(), not all() -- see
            # docs/architecture-decisions.md).
            ist_befreit = False
            namen = []
            for m in paechter:
                stand = await _calculate_hours_for_member(db, m.id, year)
                befreit = await _is_exempt(db, m.id, year)
                gesamt += stand["total"]
                einsatz_h += stand["session_hours"]
                paten_h += stand["sponsorship_hours"]
                if befreit:
                    ist_befreit = True
                namen.append(m.full_name)
            pflicht = float(config.hours_required)
            offen = max(0.0, pflicht - gesamt) if not ist_befreit else 0.0
            schuld = offen * float(config.rate_per_hour_eur)
            writer.writerow([
                parzelle.plot_number,
                "; ".join(namen),
                f"{pflicht:.1f}",
                f"{einsatz_h:.1f}",
                f"{paten_h:.1f}",
                f"{gesamt:.1f}",
                f"{offen:.1f}",
                f"{schuld:.2f}".replace(".", ","),
                "Ja" if ist_befreit else "Nein",
                "Ja" if (ist_befreit or gesamt >= pflicht) else "Nein",
            ])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=pflichtstunden_{year}.csv"},
    )


# ---------------------------------------------------------------------------
# Tasks: a backlog of upcoming work, optionally scheduled to a session and
# assigned to one of that session's signed-up participants. Lets whoever
# coordinates a session match tasks to people appropriately (e.g. lighter
# tasks for someone who can't do heavy physical work) -- the app only
# stores a workload label per task; the actual matching judgment stays
# entirely with the human coordinator.
# ---------------------------------------------------------------------------

async def _load_task(db: AsyncSession, task_id: str) -> Optional[WorkTask]:
    result = await db.execute(
        select(WorkTask)
        .options(
            selectinload(WorkTask.session),
            selectinload(WorkTask.assigned_participation).selectinload(SessionParticipation.member),
        )
        .where(WorkTask.id == task_id)
    )
    return result.scalar_one_or_none()


@router.get("/tasks", response_class=HTMLResponse)
async def tasks_overview(request: Request, db: AsyncSession = Depends(get_db)):
    user = await require_user(request, db)

    backlog_result = await db.execute(
        select(WorkTask)
        .where(WorkTask.session_id.is_(None))
        .order_by(WorkTask.created_at.desc())
    )
    backlog = backlog_result.scalars().all()

    scheduled_result = await db.execute(
        select(WorkTask)
        .options(
            selectinload(WorkTask.session),
            selectinload(WorkTask.assigned_participation).selectinload(SessionParticipation.member),
        )
        .where(WorkTask.session_id.is_not(None))
        .order_by(WorkTask.is_done, WorkTask.created_at.desc())
    )
    scheduled_tasks = scheduled_result.scalars().all()

    # Group scheduled tasks by session for display, most recent session first.
    by_session: dict = {}
    for task in scheduled_tasks:
        by_session.setdefault(task.session, []).append(task)
    sessions_with_tasks = sorted(by_session.items(), key=lambda pair: pair[0].date, reverse=True)

    upcoming_sessions_result = await db.execute(
        select(WorkSession).where(WorkSession.date >= date.today()).order_by(WorkSession.date)
    )
    upcoming_sessions = upcoming_sessions_result.scalars().all()

    return templates.TemplateResponse("work_hours/tasks.html", {
        "request": request, "user": user,
        "backlog": backlog,
        "sessions_with_tasks": sessions_with_tasks,
        "upcoming_sessions": upcoming_sessions,
        "TaskWorkload": TaskWorkload,
    })


@router.post("/tasks/new")
async def task_create(
    request: Request,
    title: str = Form(...),
    description: str = Form(""),
    workload: str = Form("MODERATE"),
    session_id: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    user = await require_user(request, db)

    task = WorkTask(
        title=title.strip(),
        description=description.strip() or None,
        workload=TaskWorkload(workload),
        session_id=session_id or None,
        created_by_id=user.id,
    )
    db.add(task)
    await db.commit()
    return RedirectResponse("/work-hours/tasks", status_code=302)


@router.post("/tasks/{task_id}/schedule")
async def task_assign_to_session(
    task_id: str,
    request: Request,
    session_id: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    """Schedules a task to a session, or sends it back to the backlog if
    session_id is empty. Clears any participant assignment when the
    session changes (an assignment to a specific person only makes sense
    for the session they actually signed up for)."""
    await require_user(request, db)
    task = await _load_task(db, task_id)
    if not task:
        raise HTTPException(status_code=404, detail=t_for(request, "work_hours.errors.task_not_found"))

    new_session_id = session_id or None
    if new_session_id != task.session_id:
        task.assigned_participation_id = None
    task.session_id = new_session_id

    await db.commit()
    return RedirectResponse("/work-hours/tasks", status_code=302)


@router.post("/tasks/{task_id}/assign")
async def task_participant_assign(
    task_id: str,
    request: Request,
    participation_id: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    """Assigns a task to one specific signed-up participant of its
    session, or clears the assignment if participation_id is empty."""
    await require_user(request, db)
    task = await _load_task(db, task_id)
    if not task:
        raise HTTPException(status_code=404, detail=t_for(request, "work_hours.errors.task_not_found"))
    if not task.session_id:
        raise HTTPException(status_code=400, detail=t_for(request, "work_hours.errors.task_not_scheduled"))

    if participation_id:
        result = await db.execute(
            select(SessionParticipation).where(
                SessionParticipation.id == participation_id,
                SessionParticipation.session_id == task.session_id,
            )
        )
        participation = result.scalar_one_or_none()
        if not participation:
            raise HTTPException(status_code=400, detail=t_for(request, "work_hours.errors.participant_not_in_session"))
        task.assigned_participation_id = participation.id
    else:
        task.assigned_participation_id = None

    await db.commit()
    referer = request.headers.get("referer", "/work-hours/tasks")
    return RedirectResponse(referer, status_code=302)


@router.post("/tasks/{task_id}/toggle-done")
async def task_toggle_done(
    task_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    await require_user(request, db)
    task = await _load_task(db, task_id)
    if not task:
        raise HTTPException(status_code=404, detail=t_for(request, "work_hours.errors.task_not_found"))

    task.is_done = not task.is_done
    await db.commit()
    referer = request.headers.get("referer", "/work-hours/tasks")
    return RedirectResponse(referer, status_code=302)


@router.post("/tasks/{task_id}/delete")
async def task_delete(
    task_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    await require_user(request, db)
    task = await _load_task(db, task_id)
    if not task:
        raise HTTPException(status_code=404, detail=t_for(request, "work_hours.errors.task_not_found"))

    await db.delete(task)
    await db.commit()
    referer = request.headers.get("referer", "/work-hours/tasks")
    return RedirectResponse(referer, status_code=302)
