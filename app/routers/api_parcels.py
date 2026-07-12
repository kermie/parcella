"""
API-Router: Parzellen – vollständiges CRUD über REST, inkl. Member-Zuordnung.
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db
from app.models import Parcel, ParcelStatus, MemberParcel, Member
from app.api_auth import get_current_api_user, require_schreibzugriff
from app.schemas import (
    ParcelOut, ParcelDetailOut, ParcelCreate, ParcelUpdate, ParcelAssignmentBrief,
    AssignmentCreate, AssignmentOut,
)
from app.models import Benutzer
from sqlalchemy.orm import selectinload

router = APIRouter(prefix="/api/v1/parcels", tags=["API: Parcels"])


async def _hole_parcel_oder_404(db: AsyncSession, parcel_id: str, mit_details: bool = False) -> Parcel:
    query = select(Parcel).where(Parcel.id == parcel_id)
    if mit_details:
        query = query.options(
            selectinload(Parcel.member_assignments).selectinload(MemberParcel.member)
        )
    result = await db.execute(query)
    parzelle = result.scalar_one_or_none()
    if not parzelle:
        raise HTTPException(status_code=404, detail="Parcel nicht gefunden")
    return parzelle


def _zu_detail_schema(parzelle: Parcel) -> ParcelDetailOut:
    out = ParcelDetailOut.model_validate(parzelle)
    out.members = [
        ParcelAssignmentBrief(
            member_id=z.member.id,
            name=z.member.full_name,
            is_primary_tenant=z.is_primary_tenant,
        )
        for z in parzelle.member_assignments
    ]
    return out


@router.get(
    "",
    response_model=List[ParcelOut],
    summary="Parzellen auflisten",
)
async def parzellen_auflisten(
    suche: Optional[str] = Query(None, description="Suche in Gartennummer"),
    status_filter: Optional[ParcelStatus] = Query(None, alias="status"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    benutzer: Benutzer = Depends(get_current_api_user),
):
    query = select(Parcel).order_by(Parcel.plot_number).limit(limit).offset(offset)

    if suche:
        query = query.where(Parcel.plot_number.ilike(f"%{suche}%"))
    if status_filter:
        query = query.where(Parcel.status == status_filter)

    result = await db.execute(query)
    return result.scalars().all()


@router.get(
    "/{parcel_id}",
    response_model=ParcelDetailOut,
    summary="Einzelne Parcel abrufen",
    description="Gibt eine Parcel inkl. zugeordneter Mitglieder zurück.",
)
async def parzelle_abrufen(
    parcel_id: str,
    db: AsyncSession = Depends(get_db),
    benutzer: Benutzer = Depends(get_current_api_user),
):
    parzelle = await _hole_parcel_oder_404(db, parcel_id, mit_details=True)
    return _zu_detail_schema(parzelle)


@router.post(
    "",
    response_model=ParcelOut,
    status_code=status.HTTP_201_CREATED,
    summary="Neue Parcel anlegen",
)
async def parzelle_erstellen(
    daten: ParcelCreate,
    db: AsyncSession = Depends(get_db),
    benutzer: Benutzer = Depends(require_schreibzugriff),
):
    plot_number = daten.plot_number.strip().upper()

    existing = await db.execute(select(Parcel).where(Parcel.plot_number == plot_number))
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Gartennummer '{plot_number}' existiert bereits.",
        )

    parzelle = Parcel(
        plot_number=plot_number,
        area_sqm=daten.area_sqm,
        notes=daten.notes,
    )
    db.add(parzelle)
    await db.commit()
    await db.refresh(parzelle)
    return parzelle


@router.put(
    "/{parcel_id}",
    response_model=ParcelOut,
    summary="Parcel aktualisieren",
    description="Teilupdate: nur übergebene Felder werden geändert. Hier auch Statuswechsel (aktiv/gekündigt/gelöscht) und Kündigungsdaten.",
)
async def parzelle_aktualisieren(
    parcel_id: str,
    daten: ParcelUpdate,
    db: AsyncSession = Depends(get_db),
    benutzer: Benutzer = Depends(require_schreibzugriff),
):
    parzelle = await _hole_parcel_oder_404(db, parcel_id)

    update_daten = daten.model_dump(exclude_unset=True)

    if "plot_number" in update_daten and update_daten["plot_number"]:
        neue_nummer = update_daten["plot_number"].strip().upper()
        if neue_nummer != parzelle.plot_number:
            existing = await db.execute(
                select(Parcel).where(Parcel.plot_number == neue_nummer, Parcel.id != parcel_id)
            )
            if existing.scalar_one_or_none():
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Gartennummer '{neue_nummer}' existiert bereits.",
                )
        update_daten["plot_number"] = neue_nummer

    for feld, wert in update_daten.items():
        setattr(parzelle, feld, wert)

    await db.commit()
    await db.refresh(parzelle)
    return parzelle


@router.delete(
    "/{parcel_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Parcel als gelöscht markieren",
    description="Setzt den Status auf 'geloescht' (kein echtes DB-Löschen, Historie bleibt erhalten).",
)
async def parzelle_loeschen(
    parcel_id: str,
    db: AsyncSession = Depends(get_db),
    benutzer: Benutzer = Depends(require_schreibzugriff),
):
    parzelle = await _hole_parcel_oder_404(db, parcel_id)
    parzelle.status = ParcelStatus.DELETED
    await db.commit()


# ---------------------------------------------------------------------------
# Member-Zuordnung (Unterressource)
# ---------------------------------------------------------------------------

@router.post(
    "/{parcel_id}/assignments",
    response_model=AssignmentOut,
    status_code=status.HTTP_201_CREATED,
    summary="Member einer Parcel zuordnen",
    description="Ermöglicht Doppelgärten (mehrere Parzellen pro Member) und Gemeinschaftsgärten (mehrere Mitglieder pro Parcel).",
)
async def member_zuordnen(
    parcel_id: str,
    daten: AssignmentCreate,
    db: AsyncSession = Depends(get_db),
    benutzer: Benutzer = Depends(require_schreibzugriff),
):
    if daten.parcel_id != parcel_id:
        raise HTTPException(status_code=400, detail="parcel_id im Body muss mit URL übereinstimmen")

    await _hole_parcel_oder_404(db, parcel_id)

    member_result = await db.execute(
        select(Member).where(Member.id == daten.member_id, Member.deleted_at.is_(None))
    )
    if not member_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Member nicht gefunden")

    existing = await db.execute(
        select(MemberParcel).where(
            MemberParcel.parcel_id == parcel_id,
            MemberParcel.member_id == daten.member_id,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Zuordnung existiert bereits")

    assignment = MemberParcel(
        parcel_id=parcel_id,
        member_id=daten.member_id,
        is_primary_tenant=daten.is_primary_tenant,
        assigned_from=daten.assigned_from,
        assigned_until=daten.assigned_until,
    )
    db.add(assignment)
    await db.commit()
    await db.refresh(assignment)
    return assignment


@router.delete(
    "/{parcel_id}/assignments/{assignment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Member-Zuordnung entfernen",
)
async def assignment_entfernen(
    parcel_id: str,
    assignment_id: str,
    db: AsyncSession = Depends(get_db),
    benutzer: Benutzer = Depends(require_schreibzugriff),
):
    result = await db.execute(
        select(MemberParcel).where(
            MemberParcel.id == assignment_id, MemberParcel.parcel_id == parcel_id
        )
    )
    assignment = result.scalar_one_or_none()
    if not assignment:
        raise HTTPException(status_code=404, detail="Zuordnung nicht gefunden")
    await db.delete(assignment)
    await db.commit()
