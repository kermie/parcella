"""
API router: Parcels -- full CRUD via REST, including member assignment.
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db
from app.models import Parcel, ParcelStatus, MemberParcel, Member
from app.api_auth import get_current_api_user, require_write_access
from app.schemas import (
    ParcelOut, ParcelDetailOut, ParcelCreate, ParcelUpdate, ParcelAssignmentBrief,
    AssignmentCreate, AssignmentOut,
)
from app.models import User
from sqlalchemy.orm import selectinload

router = APIRouter(prefix="/api/v1/parcels", tags=["API: Parcels"])


async def _get_parcel_or_404(db: AsyncSession, parcel_id: str, with_details: bool = False) -> Parcel:
    query = select(Parcel).where(Parcel.id == parcel_id)
    if with_details:
        query = query.options(
            selectinload(Parcel.member_assignments).selectinload(MemberParcel.member)
        )
    result = await db.execute(query)
    parcel = result.scalar_one_or_none()
    if not parcel:
        raise HTTPException(status_code=404, detail="Parcel not found")
    return parcel


def _to_detail_schema(parcel: Parcel) -> ParcelDetailOut:
    out = ParcelDetailOut.model_validate(parcel)
    out.members = [
        ParcelAssignmentBrief(
            member_id=z.member.id,
            name=z.member.full_name,
            is_invoice_address=z.is_invoice_address,
        )
        for z in parcel.member_assignments
    ]
    return out


@router.get(
    "",
    response_model=List[ParcelOut],
    summary="List parcels",
)
async def parcels_list(
    search: Optional[str] = Query(None, description="Search in plot number"),
    status_filter: Optional[ParcelStatus] = Query(None, alias="status"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_api_user),
):
    query = select(Parcel).order_by(Parcel.plot_number).limit(limit).offset(offset)

    if search:
        query = query.where(Parcel.plot_number.ilike(f"%{search}%"))
    if status_filter:
        query = query.where(Parcel.status == status_filter)

    result = await db.execute(query)
    return result.scalars().all()


@router.get(
    "/{parcel_id}",
    response_model=ParcelDetailOut,
    summary="Retrieve a single parcel",
    description="Returns a parcel including assigned members.",
)
async def parcel_get(
    parcel_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_api_user),
):
    parcel = await _get_parcel_or_404(db, parcel_id, with_details=True)
    return _to_detail_schema(parcel)


@router.post(
    "",
    response_model=ParcelOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create new parcel",
)
async def parcel_create(
    data: ParcelCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_write_access),
):
    plot_number = data.plot_number.strip().upper()

    existing = await db.execute(select(Parcel).where(Parcel.plot_number == plot_number))
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Plot number '{plot_number}' already exists.",
        )

    parcel = Parcel(
        plot_number=plot_number,
        area_sqm=data.area_sqm,
        notes=data.notes,
    )
    db.add(parcel)
    await db.commit()
    await db.refresh(parcel)
    return parcel


@router.put(
    "/{parcel_id}",
    response_model=ParcelOut,
    summary="Update parcel",
    description="Partial update: only the fields provided are changed. Also covers status changes (active/terminated/deleted) and termination data.",
)
async def parcel_update(
    parcel_id: str,
    data: ParcelUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_write_access),
):
    parcel = await _get_parcel_or_404(db, parcel_id)

    update_data = data.model_dump(exclude_unset=True)

    if "plot_number" in update_data and update_data["plot_number"]:
        new_number = update_data["plot_number"].strip().upper()
        if new_number != parcel.plot_number:
            existing = await db.execute(
                select(Parcel).where(Parcel.plot_number == new_number, Parcel.id != parcel_id)
            )
            if existing.scalar_one_or_none():
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Plot number '{new_number}' already exists.",
                )
        update_data["plot_number"] = new_number

    for field, value in update_data.items():
        setattr(parcel, field, value)

    await db.commit()
    await db.refresh(parcel)
    return parcel


@router.delete(
    "/{parcel_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Mark parcel as deleted",
    description="Sets the status to 'deleted' (no actual DB deletion, history is preserved).",
)
async def parcel_delete(
    parcel_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_write_access),
):
    parcel = await _get_parcel_or_404(db, parcel_id)
    parcel.status = ParcelStatus.DELETED
    await db.commit()


# ---------------------------------------------------------------------------
# Member assignment (sub-resource)
# ---------------------------------------------------------------------------

@router.post(
    "/{parcel_id}/assignments",
    response_model=AssignmentOut,
    status_code=status.HTTP_201_CREATED,
    summary="Assign member to a parcel",
    description="Enables multiple parcels per member and multiple members sharing a parcel.",
)
async def member_assign(
    parcel_id: str,
    data: AssignmentCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_write_access),
):
    if data.parcel_id != parcel_id:
        raise HTTPException(status_code=400, detail="parcel_id in body must match the URL")

    await _get_parcel_or_404(db, parcel_id)

    member_result = await db.execute(
        select(Member).where(Member.id == data.member_id, Member.deleted_at.is_(None))
    )
    if not member_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Member not found")

    existing = await db.execute(
        select(MemberParcel).where(
            MemberParcel.parcel_id == parcel_id,
            MemberParcel.member_id == data.member_id,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Assignment already exists")

    assignment = MemberParcel(
        parcel_id=parcel_id,
        member_id=data.member_id,
        # A former tenant (assigned_until set) can never be the invoice address.
        is_invoice_address=data.is_invoice_address if data.assigned_until is None else False,
        assigned_from=data.assigned_from,
        assigned_until=data.assigned_until,
    )
    db.add(assignment)
    await db.commit()
    await db.refresh(assignment)
    return assignment


@router.delete(
    "/{parcel_id}/assignments/{assignment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove member assignment",
)
async def assignment_remove(
    parcel_id: str,
    assignment_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_write_access),
):
    result = await db.execute(
        select(MemberParcel).where(
            MemberParcel.id == assignment_id, MemberParcel.parcel_id == parcel_id
        )
    )
    assignment = result.scalar_one_or_none()
    if not assignment:
        raise HTTPException(status_code=404, detail="Assignment not found")
    await db.delete(assignment)
    await db.commit()
