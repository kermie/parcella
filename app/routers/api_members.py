"""
API router: Members -- full CRUD via REST.
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_, func
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models import Member, MemberPhone, MemberEmail, MemberParcel
from app.api_auth import get_current_api_user, require_write_access
from app.schemas import (
    MemberOut, MemberDetailOut, MemberCreate, MemberUpdate,
    PhoneOut, PhoneCreate, EmailAddressOut, EmailAddressCreate,
    PaginatedResponse, MemberAssignmentBrief,
)
from app.models import User

router = APIRouter(prefix="/api/v1/members", tags=["API: Members"])


async def _get_member_or_404(db: AsyncSession, member_id: str, with_details: bool = False) -> Member:
    query = select(Member).where(Member.id == member_id, Member.deleted_at.is_(None))
    if with_details:
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
        raise HTTPException(status_code=404, detail="Member not found")
    return member


def _to_detail_schema(member: Member) -> MemberDetailOut:
    out = MemberDetailOut.model_validate(member)
    out.parcels = [
        MemberAssignmentBrief(
            parcel_id=z.parcel.id,
            plot_number=z.parcel.plot_number,
            is_invoice_address=z.is_invoice_address,
        )
        for z in member.parcel_assignments
    ]
    return out


@router.get(
    "",
    response_model=List[MemberOut],
    summary="List members",
    description="Returns all (non-deleted) members. Supports full-text search and pagination.",
)
async def members_list(
    search: Optional[str] = Query(None, description="Search in first/last name and city"),
    active_only: bool = Query(False, description="Only active memberships (member_until in the future or empty)"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_api_user),
):
    query = (
        select(Member)
        .options(selectinload(Member.phone_numbers), selectinload(Member.email_addresses))
        .where(Member.deleted_at.is_(None))
        .order_by(Member.last_name, Member.first_name)
        .limit(limit)
        .offset(offset)
    )
    if search:
        query = query.where(
            or_(
                Member.first_name.ilike(f"%{search}%"),
                Member.last_name.ilike(f"%{search}%"),
                Member.city.ilike(f"%{search}%"),
            )
        )

    result = await db.execute(query)
    members = result.scalars().all()

    if active_only:
        members = [m for m in members if m.is_active]

    return members


@router.get(
    "/{member_id}",
    response_model=MemberDetailOut,
    summary="Retrieve a single member",
    description="Returns a member including assigned parcels, phone numbers, and email addresses.",
)
async def member_get(
    member_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_api_user),
):
    member = await _get_member_or_404(db, member_id, with_details=True)
    return _to_detail_schema(member)


@router.post(
    "",
    response_model=MemberOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create new member",
)
async def member_create(
    data: MemberCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_write_access),
):
    member = Member(**data.model_dump())
    db.add(member)
    await db.commit()
    await db.refresh(member, attribute_names=["phone_numbers", "email_addresses"])
    return member


@router.put(
    "/{member_id}",
    response_model=MemberOut,
    summary="Update member",
    description="Partial update: only the fields provided are changed.",
)
async def member_update(
    member_id: str,
    data: MemberUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_write_access),
):
    member = await _get_member_or_404(db, member_id)

    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(member, field, value)

    await db.commit()
    await db.refresh(member, attribute_names=["phone_numbers", "email_addresses"])
    return member


@router.delete(
    "/{member_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete member (soft delete)",
    description="Marks the member as deleted (deleted_at set). Data remains in the database.",
)
async def member_delete(
    member_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_write_access),
):
    from datetime import datetime, timezone

    member = await _get_member_or_404(db, member_id)
    member.deleted_at = datetime.now(timezone.utc)
    await db.commit()


# ---------------------------------------------------------------------------
# Phone numbers (sub-resource)
# ---------------------------------------------------------------------------

@router.post(
    "/{member_id}/phone_numbers",
    response_model=PhoneOut,
    status_code=status.HTTP_201_CREATED,
    summary="Add phone number",
)
async def phone_add(
    member_id: str,
    data: PhoneCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_write_access),
):
    await _get_member_or_404(db, member_id)
    phone = MemberPhone(member_id=member_id, **data.model_dump())
    db.add(phone)
    await db.commit()
    await db.refresh(phone)
    return phone


@router.delete(
    "/{member_id}/phone_numbers/{phone_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove phone number",
)
async def phone_remove(
    member_id: str,
    phone_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_write_access),
):
    result = await db.execute(
        select(MemberPhone).where(
            MemberPhone.id == phone_id, MemberPhone.member_id == member_id
        )
    )
    phone = result.scalar_one_or_none()
    if not phone:
        raise HTTPException(status_code=404, detail="Phone number not found")
    await db.delete(phone)
    await db.commit()


# ---------------------------------------------------------------------------
# Email addresses (sub-resource)
# ---------------------------------------------------------------------------

@router.post(
    "/{member_id}/email-addresses",
    response_model=EmailAddressOut,
    status_code=status.HTTP_201_CREATED,
    summary="Add email address",
)
async def email_add(
    member_id: str,
    data: EmailAddressCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_write_access),
):
    await _get_member_or_404(db, member_id)
    email_obj = MemberEmail(
        member_id=member_id,
        address=str(data.address).lower(),
        label=data.label,
        is_primary=data.is_primary,
    )
    db.add(email_obj)
    await db.commit()
    await db.refresh(email_obj)
    return email_obj


@router.delete(
    "/{member_id}/email-addresses/{email_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove email address",
)
async def email_remove(
    member_id: str,
    email_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_write_access),
):
    result = await db.execute(
        select(MemberEmail).where(
            MemberEmail.id == email_id, MemberEmail.member_id == member_id
        )
    )
    email_obj = result.scalar_one_or_none()
    if not email_obj:
        raise HTTPException(status_code=404, detail="Email address not found")
    await db.delete(email_obj)
    await db.commit()
