"""
API-Router: Pflichtstunden – Konfiguration, ClubRolen, Arbeitseinsätze,
Sponsorshipen, Auswertung.
"""
from datetime import date
from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models import (
    WorkHoursConfiguration, WorkHoursMode,
    ClubRole, MemberClubRole, ExemptionReason,
    WorkSession, SessionParticipation, SessionType, ParticipationStatus,
    Sponsorship, Member, Parcel, ParcelStatus, MemberParcel, Benutzer,
)
from app.api_auth import get_current_api_user, require_schreibzugriff
from app.module_flags import require_modul
from app.schemas import (
    WorkHoursConfigurationOut, WorkHoursConfigurationCreate,
    ClubRoleOut, ClubRoleCreate,
    MemberClubRoleOut, MemberClubRoleCreate,
    WorkSessionOut, WorkSessionCreate, WorkSessionUpdate,
    SessionParticipationOut, SessionParticipationCreate, SessionParticipationUpdate,
    SponsorshipOut, SponsorshipCreate, SponsorshipUpdate,
    EvaluationRowOut,
)

router = APIRouter(
    prefix="/api/v1/work-hours",
    tags=["API: Work Hours"],
    dependencies=[Depends(require_modul("work_hours"))],
)


# ---------------------------------------------------------------------------
# Konfiguration
# ---------------------------------------------------------------------------

@router.get("/configuration", response_model=List[WorkHoursConfigurationOut], summary="Konfigurationen auflisten")
async def konfigurationen_auflisten(
    db: AsyncSession = Depends(get_db),
    benutzer: Benutzer = Depends(get_current_api_user),
):
    result = await db.execute(
        select(WorkHoursConfiguration).order_by(WorkHoursConfiguration.year.desc())
    )
    return result.scalars().all()


@router.get("/configuration/{year}", response_model=WorkHoursConfigurationOut, summary="Konfiguration für ein Jahr abrufen")
async def konfiguration_abrufen(
    year: int,
    db: AsyncSession = Depends(get_db),
    benutzer: Benutzer = Depends(get_current_api_user),
):
    result = await db.execute(
        select(WorkHoursConfiguration).where(WorkHoursConfiguration.year == year)
    )
    konfig = result.scalar_one_or_none()
    if not konfig:
        raise HTTPException(status_code=404, detail=f"Keine Konfiguration für {year}")
    return konfig


@router.put(
    "/configuration/{year}", response_model=WorkHoursConfigurationOut,
    summary="Konfiguration setzen (Upsert)",
    description="Legt die Konfiguration für ein Jahr an oder aktualisiert sie, falls bereits vorhanden.",
)
async def konfiguration_setzen(
    year: int,
    daten: WorkHoursConfigurationCreate,
    db: AsyncSession = Depends(get_db),
    benutzer: Benutzer = Depends(require_schreibzugriff),
):
    result = await db.execute(
        select(WorkHoursConfiguration).where(WorkHoursConfiguration.year == year)
    )
    konfig = result.scalar_one_or_none()

    if konfig:
        konfig.hours_required = daten.hours_required
        konfig.rate_per_hour_eur = daten.rate_per_hour_eur
        konfig.mode = WorkHoursMode(daten.mode)
        konfig.note = daten.note
    else:
        konfig = WorkHoursConfiguration(
            year=year,
            hours_required=daten.hours_required,
            rate_per_hour_eur=daten.rate_per_hour_eur,
            mode=WorkHoursMode(daten.mode),
            note=daten.note,
        )
        db.add(konfig)

    await db.commit()
    await db.refresh(konfig)
    return konfig


# ---------------------------------------------------------------------------
# ClubRolen
# ---------------------------------------------------------------------------

@router.get("/club-roles", response_model=List[ClubRoleOut], summary="ClubRolen auflisten")
async def vereinsrollen_auflisten(
    db: AsyncSession = Depends(get_db),
    benutzer: Benutzer = Depends(get_current_api_user),
):
    result = await db.execute(select(ClubRole).order_by(ClubRole.name))
    return result.scalars().all()


@router.post(
    "/club-roles", response_model=ClubRoleOut, status_code=status.HTTP_201_CREATED,
    summary="ClubRole anlegen",
)
async def vereinsrolle_erstellen(
    daten: ClubRoleCreate,
    db: AsyncSession = Depends(get_db),
    benutzer: Benutzer = Depends(require_schreibzugriff),
):
    role = ClubRole(
        name=daten.name,
        description=daten.description,
        hours_exempt=daten.hours_exempt,
        exemption_reason=ExemptionReason(daten.exemption_reason) if daten.exemption_reason else None,
    )
    db.add(role)
    await db.commit()
    await db.refresh(role)
    return role


@router.put("/club-roles/{role_id}", response_model=ClubRoleOut, summary="ClubRole aktualisieren")
async def vereinsrolle_aktualisieren(
    role_id: str,
    daten: ClubRoleCreate,
    db: AsyncSession = Depends(get_db),
    benutzer: Benutzer = Depends(require_schreibzugriff),
):
    result = await db.execute(select(ClubRole).where(ClubRole.id == role_id))
    role = result.scalar_one_or_none()
    if not role:
        raise HTTPException(status_code=404, detail="ClubRole nicht gefunden")

    role.name = daten.name
    role.description = daten.description
    role.hours_exempt = daten.hours_exempt
    role.exemption_reason = ExemptionReason(daten.exemption_reason) if daten.exemption_reason else None

    await db.commit()
    await db.refresh(role)
    return role


@router.delete(
    "/club-roles/{role_id}", status_code=status.HTTP_204_NO_CONTENT,
    summary="ClubRole löschen",
    description="Löscht die Rolle inkl. aller Member-Zuordnungen (Cascade).",
)
async def vereinsrolle_loeschen(
    role_id: str,
    db: AsyncSession = Depends(get_db),
    benutzer: Benutzer = Depends(require_schreibzugriff),
):
    result = await db.execute(select(ClubRole).where(ClubRole.id == role_id))
    role = result.scalar_one_or_none()
    if role:
        await db.delete(role)
        await db.commit()


@router.get(
    "/club-roles/assignments", response_model=List[MemberClubRoleOut],
    summary="Member-ClubRole-Zuordnungen auflisten",
)
async def zuordnungen_auflisten(
    year: Optional[int] = Query(None),
    member_id: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    benutzer: Benutzer = Depends(get_current_api_user),
):
    query = select(MemberClubRole)
    if year:
        query = query.where(MemberClubRole.year == year)
    if member_id:
        query = query.where(MemberClubRole.member_id == member_id)
    result = await db.execute(query)
    return result.scalars().all()


@router.post(
    "/club-roles/assignments", response_model=MemberClubRoleOut,
    status_code=status.HTTP_201_CREATED, summary="Member einer ClubRole zuordnen",
)
async def zuordnung_erstellen(
    daten: MemberClubRoleCreate,
    db: AsyncSession = Depends(get_db),
    benutzer: Benutzer = Depends(require_schreibzugriff),
):
    assignment = MemberClubRole(**daten.model_dump())
    db.add(assignment)
    await db.commit()
    await db.refresh(assignment)
    return assignment


@router.delete(
    "/club-roles/assignments/{assignment_id}", status_code=status.HTTP_204_NO_CONTENT,
    summary="Zuordnung entfernen",
)
async def zuordnung_loeschen(
    assignment_id: str,
    db: AsyncSession = Depends(get_db),
    benutzer: Benutzer = Depends(require_schreibzugriff),
):
    result = await db.execute(select(MemberClubRole).where(MemberClubRole.id == assignment_id))
    assignment = result.scalar_one_or_none()
    if assignment:
        await db.delete(assignment)
        await db.commit()


# ---------------------------------------------------------------------------
# Arbeitseinsätze
# ---------------------------------------------------------------------------

@router.get("/sessions", response_model=List[WorkSessionOut], summary="Arbeitseinsätze auflisten")
async def einsaetze_auflisten(
    year: Optional[int] = Query(None, description="Nach Jahr filtern"),
    type: Optional[str] = Query(None, description="STANDARD oder BESONDERS"),
    db: AsyncSession = Depends(get_db),
    benutzer: Benutzer = Depends(get_current_api_user),
):
    query = select(WorkSession).order_by(WorkSession.date.desc())
    if year:
        from sqlalchemy import extract
        query = query.where(extract("year", WorkSession.date) == year)
    if type:
        query = query.where(WorkSession.type == SessionType(type))
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/sessions/{session_id}", response_model=WorkSessionOut, summary="Einsatz abrufen")
async def einsatz_abrufen(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    benutzer: Benutzer = Depends(get_current_api_user),
):
    result = await db.execute(select(WorkSession).where(WorkSession.id == session_id))
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Einsatz nicht gefunden")
    return session


@router.post(
    "/sessions", response_model=WorkSessionOut, status_code=status.HTTP_201_CREATED,
    summary="WorkSession anlegen",
)
async def einsatz_erstellen(
    daten: WorkSessionCreate,
    db: AsyncSession = Depends(get_db),
    benutzer: Benutzer = Depends(require_schreibzugriff),
):
    session = WorkSession(
        title=daten.title, description=daten.description, type=SessionType(daten.type),
        date=daten.date, time_from=daten.time_from, time_until=daten.time_until,
        max_participants=daten.max_participants, hours_per_participant=daten.hours_per_participant,
        created_by_id=benutzer.id,
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return session


@router.put("/sessions/{session_id}", response_model=WorkSessionOut, summary="Einsatz aktualisieren")
async def einsatz_aktualisieren(
    session_id: str,
    daten: WorkSessionUpdate,
    db: AsyncSession = Depends(get_db),
    benutzer: Benutzer = Depends(require_schreibzugriff),
):
    result = await db.execute(select(WorkSession).where(WorkSession.id == session_id))
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Einsatz nicht gefunden")

    update_daten = daten.model_dump(exclude_unset=True)
    if "type" in update_daten:
        update_daten["type"] = SessionType(update_daten["type"])
    for feld, wert in update_daten.items():
        setattr(session, feld, wert)

    await db.commit()
    await db.refresh(session)
    return session


@router.delete(
    "/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT,
    summary="Einsatz löschen", description="Löscht auch alle Teilnahmen (Cascade).",
)
async def einsatz_loeschen(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    benutzer: Benutzer = Depends(require_schreibzugriff),
):
    result = await db.execute(select(WorkSession).where(WorkSession.id == session_id))
    session = result.scalar_one_or_none()
    if session:
        await db.delete(session)
        await db.commit()


# ---------------------------------------------------------------------------
# Teilnahmen (Unterressource von Einsätzen)
# ---------------------------------------------------------------------------

@router.get(
    "/sessions/{session_id}/participations", response_model=List[SessionParticipationOut],
    summary="Teilnahmen eines Einsatzes auflisten",
)
async def teilnahmen_auflisten(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    benutzer: Benutzer = Depends(get_current_api_user),
):
    result = await db.execute(
        select(SessionParticipation).where(SessionParticipation.session_id == session_id)
    )
    return result.scalars().all()


@router.post(
    "/sessions/{session_id}/participations", response_model=SessionParticipationOut,
    status_code=status.HTTP_201_CREATED, summary="Teilnahme eintragen",
)
async def teilnahme_erstellen(
    session_id: str,
    daten: SessionParticipationCreate,
    db: AsyncSession = Depends(get_db),
    benutzer: Benutzer = Depends(require_schreibzugriff),
):
    existing = await db.execute(
        select(SessionParticipation).where(
            SessionParticipation.session_id == session_id, SessionParticipation.member_id == daten.member_id
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Member ist bereits eingetragen")

    participation = SessionParticipation(
        session_id=session_id, member_id=daten.member_id,
        status=ParticipationStatus(daten.status), hours_completed=daten.hours_completed,
        note=daten.note,
    )
    db.add(participation)
    await db.commit()
    await db.refresh(participation)
    return participation


@router.put(
    "/sessions/{session_id}/participations/{participation_id}", response_model=SessionParticipationOut,
    summary="Teilnahme aktualisieren",
)
async def teilnahme_aktualisieren(
    session_id: str,
    participation_id: str,
    daten: SessionParticipationUpdate,
    db: AsyncSession = Depends(get_db),
    benutzer: Benutzer = Depends(require_schreibzugriff),
):
    result = await db.execute(
        select(SessionParticipation).where(
            SessionParticipation.id == participation_id, SessionParticipation.session_id == session_id
        )
    )
    participation = result.scalar_one_or_none()
    if not participation:
        raise HTTPException(status_code=404, detail="Teilnahme nicht gefunden")

    update_daten = daten.model_dump(exclude_unset=True)
    if "status" in update_daten:
        update_daten["status"] = ParticipationStatus(update_daten["status"])
    for feld, wert in update_daten.items():
        setattr(participation, feld, wert)

    await db.commit()
    await db.refresh(participation)
    return participation


@router.delete(
    "/sessions/{session_id}/participations/{participation_id}", status_code=status.HTTP_204_NO_CONTENT,
    summary="Teilnahme entfernen",
)
async def teilnahme_loeschen(
    session_id: str,
    participation_id: str,
    db: AsyncSession = Depends(get_db),
    benutzer: Benutzer = Depends(require_schreibzugriff),
):
    result = await db.execute(
        select(SessionParticipation).where(
            SessionParticipation.id == participation_id, SessionParticipation.session_id == session_id
        )
    )
    participation = result.scalar_one_or_none()
    if participation:
        await db.delete(participation)
        await db.commit()


# ---------------------------------------------------------------------------
# Sponsorshipen
# ---------------------------------------------------------------------------

@router.get("/sponsorships", response_model=List[SponsorshipOut], summary="Sponsorshipen auflisten")
async def patenschaften_auflisten(
    year: Optional[int] = Query(None, description="Nur Sponsorshipen, die in diesem Jahr aktiv waren"),
    db: AsyncSession = Depends(get_db),
    benutzer: Benutzer = Depends(get_current_api_user),
):
    query = select(Sponsorship).order_by(Sponsorship.area)
    if year:
        query = query.where(
            Sponsorship.valid_from <= date(year, 12, 31),
            (Sponsorship.valid_until.is_(None)) | (Sponsorship.valid_until >= date(year, 1, 1)),
        )
    result = await db.execute(query)
    return result.scalars().all()


@router.post(
    "/sponsorships", response_model=SponsorshipOut, status_code=status.HTTP_201_CREATED,
    summary="Sponsorship anlegen",
    description="member_id ist optional – eine Sponsorship kann angelegt werden, bevor sie vergeben ist.",
)
async def patenschaft_erstellen(
    daten: SponsorshipCreate,
    db: AsyncSession = Depends(get_db),
    benutzer: Benutzer = Depends(require_schreibzugriff),
):
    sponsorship = Sponsorship(**daten.model_dump())
    db.add(sponsorship)
    await db.commit()
    await db.refresh(sponsorship)
    return sponsorship


@router.put("/sponsorships/{sponsorship_id}", response_model=SponsorshipOut, summary="Sponsorship aktualisieren")
async def patenschaft_aktualisieren(
    sponsorship_id: str,
    daten: SponsorshipUpdate,
    db: AsyncSession = Depends(get_db),
    benutzer: Benutzer = Depends(require_schreibzugriff),
):
    result = await db.execute(select(Sponsorship).where(Sponsorship.id == sponsorship_id))
    sponsorship = result.scalar_one_or_none()
    if not sponsorship:
        raise HTTPException(status_code=404, detail="Sponsorship nicht gefunden")

    for feld, wert in daten.model_dump(exclude_unset=True).items():
        setattr(sponsorship, feld, wert)

    await db.commit()
    await db.refresh(sponsorship)
    return sponsorship


@router.delete(
    "/sponsorships/{sponsorship_id}", status_code=status.HTTP_204_NO_CONTENT,
    summary="Sponsorship löschen",
)
async def patenschaft_loeschen(
    sponsorship_id: str,
    db: AsyncSession = Depends(get_db),
    benutzer: Benutzer = Depends(require_schreibzugriff),
):
    result = await db.execute(select(Sponsorship).where(Sponsorship.id == sponsorship_id))
    sponsorship = result.scalar_one_or_none()
    if sponsorship:
        await db.delete(sponsorship)
        await db.commit()


# ---------------------------------------------------------------------------
# Auswertung
# ---------------------------------------------------------------------------

@router.get(
    "/evaluation/{year}", response_model=List[EvaluationRowOut],
    summary="Jahresauswertung abrufen",
    description=(
        "Berechnet je nach konfiguriertem Modus (PER_PARCEL oder PER_MEMBER) "
        "den Pflichtstunden-Stand: geleistete Stunden, offene Stunden, Schuldbetrag, "
        "Befreiungsstatus."
    ),
)
async def auswertung_abrufen(
    year: int,
    db: AsyncSession = Depends(get_db),
    benutzer: Benutzer = Depends(get_current_api_user),
):
    from app.routers.work_hours import (
        _get_config_for_year, _calculate_hours_for_member, _is_exempt
    )

    config = await _get_config_for_year(db, year)
    if not config:
        raise HTTPException(status_code=404, detail=f"Keine Konfiguration für {year}")

    zeilen: List[EvaluationRowOut] = []
    pflicht = Decimal(str(config.hours_required))

    if config.mode == WorkHoursMode.PER_PARCEL:
        result = await db.execute(
            select(Parcel)
            .options(selectinload(Parcel.member_assignments).selectinload(MemberParcel.member))
            .where(Parcel.status == ParcelStatus.ACTIVE)
            .order_by(Parcel.plot_number)
        )
        for parzelle in result.scalars().all():
            paechter = [z.member for z in parzelle.member_assignments]
            if not paechter:
                continue
            gesamt = Decimal("0")
            # Vier-Augen-freundliche Regel: EIN befreiter Pächter genügt, um
            # die gesamte Parcel zu befreien (any(), nicht all() – siehe
            # docs/architektur-entscheidungen.md).
            ist_befreit = False
            for m in paechter:
                stand = await _calculate_hours_for_member(db, m.id, year)
                gesamt += Decimal(str(stand["gesamt"]))
                if await _is_exempt(db, m.id, year):
                    ist_befreit = True
            offen = max(Decimal("0"), pflicht - gesamt) if not ist_befreit else Decimal("0")
            zeilen.append(EvaluationRowOut(
                label=parzelle.plot_number,
                hours_required=pflicht, hours_completed=gesamt, hours_open=offen,
                amount_due_eur=offen * Decimal(str(config.rate_per_hour_eur)),
                exempt=ist_befreit, fulfilled=ist_befreit or gesamt >= pflicht,
            ))
    else:
        result = await db.execute(
            select(Member)
            .options(selectinload(Member.parcel_assignments))
            .where(Member.deleted_at.is_(None), Member.parcel_assignments.any())
            .order_by(Member.last_name, Member.first_name)
        )
        for m in result.scalars().all():
            stand = await _calculate_hours_for_member(db, m.id, year)
            befreit = await _is_exempt(db, m.id, year)
            gesamt = Decimal(str(stand["gesamt"]))
            offen = max(Decimal("0"), pflicht - gesamt) if not befreit else Decimal("0")
            zeilen.append(EvaluationRowOut(
                label=m.full_name,
                hours_required=pflicht, hours_completed=gesamt, hours_open=offen,
                amount_due_eur=offen * Decimal(str(config.rate_per_hour_eur)),
                exempt=befreit, fulfilled=befreit or gesamt >= pflicht,
            ))

    return zeilen
