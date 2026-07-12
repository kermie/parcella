"""
API-Router: Mitglieder – vollständiges CRUD über REST.
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_, func
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models import Member, MemberPhone, MemberEmail, MemberParcel
from app.api_auth import get_current_api_user, require_schreibzugriff
from app.schemas import (
    MemberOut, MemberDetailOut, MemberCreate, MemberUpdate,
    PhoneOut, PhoneCreate, EmailAddressOut, EmailAddressCreate,
    PaginierteAntwort, MemberAssignmentBrief,
)
from app.models import Benutzer

router = APIRouter(prefix="/api/v1/members", tags=["API: Members"])


async def _hole_member_oder_404(db: AsyncSession, member_id: str, mit_details: bool = False) -> Member:
    query = select(Member).where(Member.id == member_id, Member.deleted_at.is_(None))
    if mit_details:
        query = query.options(
            selectinload(Member.phone_numbers),
            selectinload(Member.email_addresses),
            selectinload(Member.parcel_assignments).selectinload(MemberParcel.parcel),
        )
    else:
        query = query.options(
            selectinload(Member.phone_numbers),
            selectinload(Member.email_addresses),
        )
    result = await db.execute(query)
    member = result.scalar_one_or_none()
    if not member:
        raise HTTPException(status_code=404, detail="Member nicht gefunden")
    return member


def _zu_detail_schema(member: Member) -> MemberDetailOut:
    out = MemberDetailOut.model_validate(member)
    out.parcels = [
        MemberAssignmentBrief(
            parcel_id=z.parcel.id,
            plot_number=z.parcel.plot_number,
            is_primary_tenant=z.is_primary_tenant,
        )
        for z in member.parcel_assignments
    ]
    return out


@router.get(
    "",
    response_model=List[MemberOut],
    summary="Mitglieder auflisten",
    description="Gibt alle (nicht gelöschten) Mitglieder zurück. Unterstützt Volltextsuche und Paginierung.",
)
async def mitglieder_auflisten(
    suche: Optional[str] = Query(None, description="Suche in Vor-/Nachname und Ort"),
    nur_aktive: bool = Query(False, description="Nur aktive Mitgliedschaften (member_until in der Zukunft oder leer)"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    benutzer: Benutzer = Depends(get_current_api_user),
):
    query = (
        select(Member)
        .options(selectinload(Member.phone_numbers), selectinload(Member.email_addresses))
        .where(Member.deleted_at.is_(None))
        .order_by(Member.last_name, Member.first_name)
        .limit(limit)
        .offset(offset)
    )
    if suche:
        query = query.where(
            or_(
                Member.first_name.ilike(f"%{suche}%"),
                Member.last_name.ilike(f"%{suche}%"),
                Member.city.ilike(f"%{suche}%"),
            )
        )

    result = await db.execute(query)
    members = result.scalars().all()

    if nur_aktive:
        members = [m for m in members if m.is_active]

    return members


@router.get(
    "/{member_id}",
    response_model=MemberDetailOut,
    summary="Einzelnes Member abrufen",
    description="Gibt ein Member inkl. zugeordneter Parzellen, Telefonnummern und E-Mail-Adressen zurück.",
)
async def mitglied_abrufen(
    member_id: str,
    db: AsyncSession = Depends(get_db),
    benutzer: Benutzer = Depends(get_current_api_user),
):
    member = await _hole_member_oder_404(db, member_id, mit_details=True)
    return _zu_detail_schema(member)


@router.post(
    "",
    response_model=MemberOut,
    status_code=status.HTTP_201_CREATED,
    summary="Neues Member anlegen",
)
async def mitglied_erstellen(
    daten: MemberCreate,
    db: AsyncSession = Depends(get_db),
    benutzer: Benutzer = Depends(require_schreibzugriff),
):
    member = Member(**daten.model_dump())
    db.add(member)
    await db.commit()
    await db.refresh(member, attribute_names=["phone_numbers", "email_addresses"])
    return member


@router.put(
    "/{member_id}",
    response_model=MemberOut,
    summary="Member aktualisieren",
    description="Teilupdate: nur übergebene Felder werden geändert.",
)
async def mitglied_aktualisieren(
    member_id: str,
    daten: MemberUpdate,
    db: AsyncSession = Depends(get_db),
    benutzer: Benutzer = Depends(require_schreibzugriff),
):
    member = await _hole_member_oder_404(db, member_id)

    for feld, wert in daten.model_dump(exclude_unset=True).items():
        setattr(member, feld, wert)

    await db.commit()
    await db.refresh(member, attribute_names=["phone_numbers", "email_addresses"])
    return member


@router.delete(
    "/{member_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Member löschen (Soft-Delete)",
    description="Markiert das Member als gelöscht (deleted_at gesetzt). Daten bleiben in der Datenbank erhalten.",
)
async def mitglied_loeschen(
    member_id: str,
    db: AsyncSession = Depends(get_db),
    benutzer: Benutzer = Depends(require_schreibzugriff),
):
    from datetime import datetime, timezone

    member = await _hole_member_oder_404(db, member_id)
    member.deleted_at = datetime.now(timezone.utc)
    await db.commit()


# ---------------------------------------------------------------------------
# Telefonnummern (Unterressource)
# ---------------------------------------------------------------------------

@router.post(
    "/{member_id}/phone_numbers",
    response_model=PhoneOut,
    status_code=status.HTTP_201_CREATED,
    summary="Telefonnummer hinzufügen",
)
async def telefon_hinzufuegen(
    member_id: str,
    daten: PhoneCreate,
    db: AsyncSession = Depends(get_db),
    benutzer: Benutzer = Depends(require_schreibzugriff),
):
    await _hole_member_oder_404(db, member_id)
    telefon = MemberPhone(member_id=member_id, **daten.model_dump())
    db.add(telefon)
    await db.commit()
    await db.refresh(telefon)
    return telefon


@router.delete(
    "/{member_id}/phone_numbers/{telefon_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Telefonnummer entfernen",
)
async def telefon_entfernen(
    member_id: str,
    telefon_id: str,
    db: AsyncSession = Depends(get_db),
    benutzer: Benutzer = Depends(require_schreibzugriff),
):
    result = await db.execute(
        select(MemberPhone).where(
            MemberPhone.id == telefon_id, MemberPhone.member_id == member_id
        )
    )
    telefon = result.scalar_one_or_none()
    if not telefon:
        raise HTTPException(status_code=404, detail="Telefonnummer nicht gefunden")
    await db.delete(telefon)
    await db.commit()


# ---------------------------------------------------------------------------
# E-Mail-Adressen (Unterressource)
# ---------------------------------------------------------------------------

@router.post(
    "/{member_id}/email-addresses",
    response_model=EmailAddressOut,
    status_code=status.HTTP_201_CREATED,
    summary="E-Mail-Adresse hinzufügen",
)
async def email_hinzufuegen(
    member_id: str,
    daten: EmailAddressCreate,
    db: AsyncSession = Depends(get_db),
    benutzer: Benutzer = Depends(require_schreibzugriff),
):
    await _hole_member_oder_404(db, member_id)
    email_obj = MemberEmail(
        member_id=member_id,
        address=str(daten.address).lower(),
        label=daten.label,
        is_primary=daten.is_primary,
    )
    db.add(email_obj)
    await db.commit()
    await db.refresh(email_obj)
    return email_obj


@router.delete(
    "/{member_id}/email-addresses/{email_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="E-Mail-Adresse entfernen",
)
async def email_entfernen(
    member_id: str,
    email_id: str,
    db: AsyncSession = Depends(get_db),
    benutzer: Benutzer = Depends(require_schreibzugriff),
):
    result = await db.execute(
        select(MemberEmail).where(
            MemberEmail.id == email_id, MemberEmail.member_id == member_id
        )
    )
    email_obj = result.scalar_one_or_none()
    if not email_obj:
        raise HTTPException(status_code=404, detail="E-Mail-Adresse nicht gefunden")
    await db.delete(email_obj)
    await db.commit()
