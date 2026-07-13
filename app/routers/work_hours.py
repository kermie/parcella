"""
Pflichtstunden-Router: Arbeitseinsätze, Sponsorshipen, ClubRolen, Konfiguration.
"""
import csv
import io
from datetime import date, datetime, timezone
from typing import Optional, List

from fastapi import APIRouter, Request, Form, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_
from sqlalchemy.orm import selectinload

from app.database import get_db, active_member_filter
from app.models import (
    WorkSession, SessionParticipation, SessionType, ParticipationStatus,
    Sponsorship, ClubRole, MemberClubRole, ExemptionReason,
    WorkHoursConfiguration, WorkHoursMode,
    Member, MemberParcel, Parcel, ParcelStatus,
)
from app.auth import require_user

from app.module_flags import require_modul

router = APIRouter(
    prefix="/work-hours",
    tags=["work-hours"],
    dependencies=[Depends(require_modul("work_hours"))],
)
from app.templating import templates


# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------

async def _get_config_for_year(db: AsyncSession, year: int) -> Optional[WorkHoursConfiguration]:
    result = await db.execute(
        select(WorkHoursConfiguration).where(WorkHoursConfiguration.year == year)
    )
    return result.scalar_one_or_none()


async def _calculate_hours_for_member(
    db: AsyncSession, member_id: str, year: int
) -> dict:
    """Berechnet den Pflichtstunden-Stand eines Mitglieds für ein Jahr."""

    # Einsatz-Teilnahmen (nur ERSCHIENEN zählen)
    einsatz_stunden = await db.scalar(
        select(func.coalesce(func.sum(SessionParticipation.hours_completed), 0))
        .join(WorkSession)
        .where(
            SessionParticipation.member_id == member_id,
            SessionParticipation.status == ParticipationStatus.ATTENDED,
            func.extract("year", WorkSession.date) == year,
        )
    ) or 0

    # Sponsorship (aktiv im gesuchten Jahr)
    patenschaft_stunden = await db.scalar(
        select(func.coalesce(func.sum(Sponsorship.credited_hours), 0))
        .where(
            Sponsorship.member_id == member_id,
            Sponsorship.valid_from <= date(year, 12, 31),
            (Sponsorship.valid_until.is_(None)) | (Sponsorship.valid_until >= date(year, 1, 1)),
        )
    ) or 0

    return {
        "einsatz_stunden": float(einsatz_stunden),
        "patenschaft_stunden": float(patenschaft_stunden),
        "gesamt": float(einsatz_stunden) + float(patenschaft_stunden),
    }


async def _is_exempt(db: AsyncSession, member_id: str, year: int) -> bool:
    """Prüft ob ein Member für ein Jahr von Pflichtstunden befreit ist."""
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
# Dashboard / Übersicht
# ---------------------------------------------------------------------------

@router.get("/", response_class=HTMLResponse)
async def pflichtstunden_uebersicht(
    request: Request,
    year: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
):
    user = await require_user(request, db)

    if not year:
        year = date.today().year

    config = await _get_config_for_year(db, year)

    # Alle verfügbaren Jahre für Dropdown
    jahre_result = await db.execute(
        select(WorkHoursConfiguration.year).order_by(WorkHoursConfiguration.year.desc())
    )
    verfuegbare_jahre = [r[0] for r in jahre_result.all()]

    # Einsätze des Jahres
    einsaetze_result = await db.execute(
        select(WorkSession)
        .options(selectinload(WorkSession.participations))
        .where(func.extract("year", WorkSession.date) == year)
        .order_by(WorkSession.date.desc())
    )
    einsaetze = einsaetze_result.scalars().all()

    return templates.TemplateResponse(
        "work_hours/uebersicht.html",
        {
            "request": request,
            "user": user,
            "year": year,
            "config": config,
            "einsaetze": einsaetze,
            "verfuegbare_jahre": verfuegbare_jahre,
            "SessionType": SessionType,
            "ParticipationStatus": ParticipationStatus,
        },
    )


# ---------------------------------------------------------------------------
# Pflichtstunden-Konfiguration
# ---------------------------------------------------------------------------

@router.get("/configuration", response_class=HTMLResponse)
async def konfiguration_seite(request: Request, db: AsyncSession = Depends(get_db)):
    user = await require_user(request, db)

    result = await db.execute(
        select(WorkHoursConfiguration).order_by(WorkHoursConfiguration.year.desc())
    )
    konfigurationen = result.scalars().all()

    return templates.TemplateResponse(
        "work_hours/configuration.html",
        {
            "request": request,
            "user": user,
            "konfigurationen": konfigurationen,
            "WorkHoursMode": WorkHoursMode,
            "aktuelles_jahr": date.today().year,
        },
    )


@router.get("/configuration/{configuration_id}/edit", response_class=HTMLResponse)
async def konfiguration_bearbeiten_seite(
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
        raise HTTPException(status_code=404, detail="Konfiguration nicht gefunden")

    return templates.TemplateResponse(
        "work_hours/configuration_formular.html",
        {
            "request": request,
            "user": user,
            "configuration": configuration,
            "WorkHoursMode": WorkHoursMode,
        },
    )


@router.post("/configuration/{configuration_id}/edit")
async def konfiguration_aktualisieren(
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
        raise HTTPException(status_code=404, detail="Konfiguration nicht gefunden")

    # Falls das Jahr geändert wird: prüfen ob es mit einem anderen Eintrag kollidiert
    if year != configuration.year:
        kollision = await _get_config_for_year(db, year)
        if kollision and kollision.id != configuration_id:
            raise HTTPException(
                status_code=400,
                detail=f"Für {year} existiert bereits eine andere Konfiguration."
            )

    configuration.year = year
    configuration.hours_required = float(hours_required.replace(",", "."))
    configuration.rate_per_hour_eur = float(rate_per_hour_eur.replace(",", "."))
    configuration.mode = WorkHoursMode(mode)
    configuration.note = note.strip() or None

    await db.commit()
    return RedirectResponse("/work-hours/configuration", status_code=302)


@router.post("/configuration/{configuration_id}/delete")
async def konfiguration_loeschen(
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
async def konfiguration_erstellen(
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
# Arbeitseinsätze
# ---------------------------------------------------------------------------

@router.get("/sessions/new", response_class=HTMLResponse)
async def einsatz_neu_seite(request: Request, db: AsyncSession = Depends(get_db)):
    user = await require_user(request, db)
    return templates.TemplateResponse(
        "work_hours/einsatz_formular.html",
        {
            "request": request,
            "user": user,
            "session": None,
            "SessionType": SessionType,
        },
    )


@router.post("/sessions/new")
async def einsatz_erstellen(
    request: Request,
    title: str = Form(...),
    description: str = Form(""),
    type: str = Form("STANDARD"),
    date: str = Form(...),
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
        date=date.fromisoformat(date),
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
async def einsatz_bearbeiten_seite(
    session_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await require_user(request, db)

    result = await db.execute(select(WorkSession).where(WorkSession.id == session_id))
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Einsatz nicht gefunden")

    return templates.TemplateResponse(
        "work_hours/einsatz_formular.html",
        {
            "request": request,
            "user": user,
            "session": session,
            "SessionType": SessionType,
        },
    )


@router.post("/sessions/{session_id}/edit")
async def einsatz_aktualisieren(
    session_id: str,
    request: Request,
    title: str = Form(...),
    description: str = Form(""),
    type: str = Form("STANDARD"),
    date: str = Form(...),
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
        raise HTTPException(status_code=404, detail="Einsatz nicht gefunden")

    session.title = title.strip()
    session.description = description.strip() or None
    session.type = SessionType(type)
    session.date = date.fromisoformat(date)
    session.time_from = time_from.strip() or None
    session.time_until = time_until.strip() or None
    session.max_participants = int(max_participants) if max_participants.strip() else None
    session.hours_per_participant = (
        float(hours_per_participant.replace(",", ".")) if hours_per_participant.strip() else None
    )

    await db.commit()
    return RedirectResponse(f"/work-hours/sessions/{session_id}", status_code=302)


@router.post("/sessions/{session_id}/delete")
async def einsatz_loeschen(
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
async def einsatz_detail(
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
        raise HTTPException(status_code=404, detail="Einsatz nicht gefunden")

    # Alle aktiven Mitglieder für Anmelde-Dropdown
    mitglieder_result = await db.execute(
        select(Member)
        .where(active_member_filter())
        .order_by(Member.last_name, Member.first_name)
    )
    alle_mitglieder = mitglieder_result.scalars().all()
    bereits_eingetragen = {t.member_id for t in session.participations}

    return templates.TemplateResponse(
        "work_hours/einsatz_detail.html",
        {
            "request": request,
            "user": user,
            "session": session,
            "alle_mitglieder": alle_mitglieder,
            "bereits_eingetragen": bereits_eingetragen,
            "ParticipationStatus": ParticipationStatus,
            "SessionType": SessionType,
        },
    )


@router.post("/sessions/{session_id}/participants/add")
async def teilnehmer_hinzufuegen(
    session_id: str,
    request: Request,
    member_id: str = Form(...),
    status: str = Form("ATTENDED"),
    hours_completed: str = Form(""),
    note: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    await require_user(request, db)

    # Bereits eingetragen?
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
async def teilnahme_status_aendern(
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
async def teilnahme_entfernen(
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
# ClubRolen
# ---------------------------------------------------------------------------

@router.get("/club-roles", response_class=HTMLResponse)
async def vereinsrollen_seite(
    request: Request,
    year: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
):
    user = await require_user(request, db)

    if not year:
        year = date.today().year

    rollen_result = await db.execute(
        select(ClubRole).order_by(ClubRole.name)
    )
    rollen = rollen_result.scalars().all()

    zuordnungen_result = await db.execute(
        select(MemberClubRole)
        .options(
            selectinload(MemberClubRole.member),
            selectinload(MemberClubRole.club_role),
        )
        .where(MemberClubRole.year == year)
        .order_by(MemberClubRole.club_role_id)
    )
    assignments = zuordnungen_result.scalars().all()

    mitglieder_result = await db.execute(
        select(Member)
        .where(active_member_filter())
        .order_by(Member.last_name, Member.first_name)
    )
    alle_mitglieder = mitglieder_result.scalars().all()

    return templates.TemplateResponse(
        "work_hours/club-roles.html",
        {
            "request": request,
            "user": user,
            "rollen": rollen,
            "assignments": assignments,
            "alle_mitglieder": alle_mitglieder,
            "year": year,
            "ExemptionReason": ExemptionReason,
            "aktuelles_jahr": date.today().year,
        },
    )


@router.post("/club-roles/assign-member")
async def mitglied_vereinsrolle_zuordnen(
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
async def mitglied_vereinsrolle_bearbeiten_seite(
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
        raise HTTPException(status_code=404, detail="Zuordnung nicht gefunden")

    mitglieder_result = await db.execute(
        select(Member)
        .where(active_member_filter())
        .order_by(Member.last_name, Member.first_name)
    )
    alle_mitglieder = mitglieder_result.scalars().all()

    rollen_result = await db.execute(select(ClubRole).order_by(ClubRole.name))
    alle_rollen = rollen_result.scalars().all()

    return templates.TemplateResponse(
        "work_hours/mitglied_vereinsrolle_formular.html",
        {
            "request": request,
            "user": user,
            "assignment": assignment,
            "alle_mitglieder": alle_mitglieder,
            "alle_rollen": alle_rollen,
        },
    )


@router.post("/club-roles/assignment/{assignment_id}/edit")
async def mitglied_vereinsrolle_aktualisieren(
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
        raise HTTPException(status_code=404, detail="Zuordnung nicht gefunden")

    assignment.member_id = member_id
    assignment.club_role_id = club_role_id
    assignment.year = year
    assignment.valid_from = date.fromisoformat(valid_from) if valid_from.strip() else None
    assignment.valid_until = date.fromisoformat(valid_until) if valid_until.strip() else None
    assignment.note = note.strip() or None

    await db.commit()
    return RedirectResponse(f"/work-hours/club-roles?year={year}", status_code=302)


@router.post("/club-roles/assignment/{assignment_id}/remove")
async def mitglied_vereinsrolle_entfernen(
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
async def vereinsrolle_erstellen(
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
async def vereinsrolle_bearbeiten_seite(
    role_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await require_user(request, db)

    result = await db.execute(select(ClubRole).where(ClubRole.id == role_id))
    role = result.scalar_one_or_none()
    if not role:
        raise HTTPException(status_code=404, detail="ClubRole nicht gefunden")

    return templates.TemplateResponse(
        "work_hours/vereinsrolle_formular.html",
        {
            "request": request,
            "user": user,
            "role": role,
        },
    )


@router.post("/club-roles/{role_id}/edit")
async def vereinsrolle_aktualisieren(
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
async def vereinsrolle_loeschen(
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
# Sponsorshipen
# ---------------------------------------------------------------------------

@router.get("/sponsorships", response_class=HTMLResponse)
async def patenschaften_seite(
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
    patenschaften = result.scalars().all()

    # Nach Bereich gruppieren, damit mehrere Mitglieder pro Bereich
    # gemeinsam dargestellt werden
    bereiche_gruppiert = {}
    for p in patenschaften:
        bereiche_gruppiert.setdefault(p.area, []).append(p)

    # Alle bekannten Bereichsnamen (für Autovervollständigung, auch aus
    # vergangenen Jahren, damit Tippfehler beim Wiederverwenden vermieden werden)
    alle_bereiche_result = await db.execute(
        select(Sponsorship.area).distinct().order_by(Sponsorship.area)
    )
    alle_bereiche = [r[0] for r in alle_bereiche_result.all()]

    # Aktuelle Pflichtstunden-Konfiguration für Vorbefüllung
    config = await _get_config_for_year(db, year)

    mitglieder_result = await db.execute(
        select(Member)
        .where(active_member_filter())
        .order_by(Member.last_name, Member.first_name)
    )
    alle_mitglieder = mitglieder_result.scalars().all()

    return templates.TemplateResponse(
        "work_hours/sponsorships.html",
        {
            "request": request,
            "user": user,
            "patenschaften": patenschaften,
            "bereiche_gruppiert": bereiche_gruppiert,
            "alle_bereiche": alle_bereiche,
            "config": config,
            "alle_mitglieder": alle_mitglieder,
            "year": year,
        },
    )


@router.post("/sponsorships/new")
async def patenschaft_erstellen(
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
async def patenschaft_bearbeiten_seite(
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
        raise HTTPException(status_code=404, detail="Sponsorship nicht gefunden")

    mitglieder_result = await db.execute(
        select(Member)
        .where(active_member_filter())
        .order_by(Member.last_name, Member.first_name)
    )
    alle_mitglieder = mitglieder_result.scalars().all()

    alle_bereiche_result = await db.execute(
        select(Sponsorship.area).distinct().order_by(Sponsorship.area)
    )
    alle_bereiche = [r[0] for r in alle_bereiche_result.all()]

    return templates.TemplateResponse(
        "work_hours/patenschaft_formular.html",
        {
            "request": request,
            "user": user,
            "sponsorship": sponsorship,
            "alle_mitglieder": alle_mitglieder,
            "alle_bereiche": alle_bereiche,
        },
    )


@router.post("/sponsorships/{sponsorship_id}/edit")
async def patenschaft_aktualisieren(
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
        raise HTTPException(status_code=404, detail="Sponsorship nicht gefunden")

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
async def patenschaft_loeschen(
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
# Auswertung: Jahresstand pro Member/Parcel
# ---------------------------------------------------------------------------

@router.get("/evaluation", response_class=HTMLResponse)
async def auswertung(
    request: Request,
    year: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
):
    user = await require_user(request, db)

    if not year:
        year = date.today().year

    config = await _get_config_for_year(db, year)

    jahre_result = await db.execute(
        select(WorkHoursConfiguration.year).order_by(WorkHoursConfiguration.year.desc())
    )
    verfuegbare_jahre = [r[0] for r in jahre_result.all()]

    if not config:
        return templates.TemplateResponse(
            "work_hours/evaluation.html",
            {
                "request": request,
                "user": user,
                "year": year,
                "config": None,
                "zeilen": [],
                "verfuegbare_jahre": verfuegbare_jahre,
            },
        )

    zeilen = []

    if config.mode == WorkHoursMode.PER_PARCEL:
        # Pro Parcel auswerten – alle aktiven Parzellen mit Pächtern
        parzellen_result = await db.execute(
            select(Parcel)
            .options(
                selectinload(Parcel.member_assignments).selectinload(MemberParcel.member)
            )
            .where(Parcel.status == ParcelStatus.ACTIVE)
            .order_by(Parcel.plot_number)
        )
        parcels = parzellen_result.scalars().all()

        for parzelle in parcels:
            paechter = [
                z.member for z in parzelle.member_assignments
                if z.member.deleted_at is None
                and (z.member.member_until is None or z.member.member_until >= date.today())
            ]
            if not paechter:
                continue  # Unbesetzte oder nur inaktive Pächter überspringen

            # Stunden aller Pächter summieren
            gesamt_stunden = 0.0
            paechter_details = []
            for m in paechter:
                stand = await _calculate_hours_for_member(db, m.id, year)
                befreit = await _is_exempt(db, m.id, year)
                gesamt_stunden += stand["gesamt"]
                paechter_details.append({
                    "member": m,
                    "stand": stand,
                    "befreit": befreit,
                })

            pflicht = float(config.hours_required)
            offen = max(0.0, pflicht - gesamt_stunden)
            schuldbetrag = offen * float(config.rate_per_hour_eur)

            # Befreit wenn MINDESTENS EIN Pächter befreit (any(), nicht all() –
            # siehe docs/architektur-entscheidungen.md). Bewusst NICHT
            # "alle_befreit" genannt, das hatte schon einmal zu einer
            # falsch herum kopierten all()-Logik im CSV-Export und in der
            # API geführt.
            ist_befreit = any(p["befreit"] for p in paechter_details)

            zeilen.append({
                "parzelle": parzelle,
                "paechter_details": paechter_details,
                "gesamt_stunden": gesamt_stunden,
                "pflicht_stunden": pflicht,
                "offen_stunden": offen if not ist_befreit else 0.0,
                "schuldbetrag": schuldbetrag if not ist_befreit else 0.0,
                "erfuellt": ist_befreit or gesamt_stunden >= pflicht,
                "alle_befreit": ist_befreit,
                "befreit": ist_befreit,  # einheitlicher Key für Template
            })

    else:
        # PRO_MITGLIED: jedes Member mit Parcel einzeln auswerten
        mitglieder_result = await db.execute(
            select(Member)
            .options(selectinload(Member.parcel_assignments))
            .where(
                Member.deleted_at.is_(None),
                Member.parcel_assignments.any(),
            )
            .order_by(Member.last_name, Member.first_name)
        )
        members = mitglieder_result.scalars().all()

        for m in members:
            stand = await _calculate_hours_for_member(db, m.id, year)
            befreit = await _is_exempt(db, m.id, year)
            pflicht = float(config.hours_required)
            offen = max(0.0, pflicht - stand["gesamt"])
            schuldbetrag = offen * float(config.rate_per_hour_eur)

            zeilen.append({
                "member": m,
                "stand": stand,
                "befreit": befreit,
                "pflicht_stunden": pflicht,
                "offen_stunden": offen if not befreit else 0.0,
                "schuldbetrag": schuldbetrag if not befreit else 0.0,
                "erfuellt": befreit or stand["gesamt"] >= pflicht,
            })

    return templates.TemplateResponse(
        "work_hours/evaluation.html",
        {
            "request": request,
            "user": user,
            "year": year,
            "config": config,
            "zeilen": zeilen,
            "verfuegbare_jahre": verfuegbare_jahre,
            "WorkHoursMode": WorkHoursMode,
        },
    )


@router.get("/evaluation/csv")
async def auswertung_export_csv(
    request: Request,
    year: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
):
    await require_user(request, db)

    if not year:
        year = date.today().year

    config = await _get_config_for_year(db, year)
    if not config:
        raise HTTPException(status_code=404, detail=f"Keine Konfiguration für {year}")

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
            # Vier-Augen-freundliche Regel: EIN befreiter Pächter genügt, um
            # die gesamte Parcel zu befreien (any(), nicht all() – siehe
            # docs/architektur-entscheidungen.md).
            ist_befreit = False
            namen = []
            for m in paechter:
                stand = await _calculate_hours_for_member(db, m.id, year)
                befreit = await _is_exempt(db, m.id, year)
                gesamt += stand["gesamt"]
                einsatz_h += stand["einsatz_stunden"]
                paten_h += stand["patenschaft_stunden"]
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
