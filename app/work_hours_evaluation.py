"""
Work-hours shortfall amounts for invoicing (issue #83): "charge those
who not completely or never used their work sessions, according to
/work-hours/evaluation. Exclude people and garden plots which
exempted or fulfilled their duty."

Reuses app.services.work_hours' evaluation engine (ADR 0070 -- the same
one /work-hours/evaluation and its API equivalent use), but returns
just what invoice generation needs: how much each parcel or member
currently owes for the year, already excluding anyone exempt or who
already fulfilled their hours (both computed to 0, then dropped from
the result entirely -- see app/invoice_generation.py's
WORK_HOURS_SHORTFALL pricing mode, which bills exactly this,
automatically, with no manual scoping).
"""
from datetime import date
from typing import Dict, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Parcel, ParcelStatus, Member, MemberParcel, WorkHoursMode
from app.services.work_hours import get_config_for_year, evaluate_parcel, evaluate_member


async def compute_work_hours_shortfalls(
    db: AsyncSession, year: int
) -> Tuple[Optional[WorkHoursMode], Dict[str, float], Dict[str, float]]:
    """Returns (mode, amounts_by_parcel_id, amounts_by_member_id).

    `mode` is None (both dicts empty) if no WorkHoursConfiguration
    exists for `year`. Otherwise exactly one of the two dicts is
    populated, matching whichever mode that year is configured for --
    the other pricing family gets nothing to bill, same as
    /work-hours/evaluation itself only ever shows one table shape per
    year."""
    config = await get_config_for_year(db, year)
    if not config:
        return None, {}, {}

    if config.mode == WorkHoursMode.PER_PARCEL:
        result = await db.execute(
            select(Parcel)
            .options(selectinload(Parcel.member_assignments).selectinload(MemberParcel.member))
            .where(Parcel.status == ParcelStatus.ACTIVE)
        )
        amounts_by_parcel: Dict[str, float] = {}
        for parcel in result.scalars().all():
            row = await evaluate_parcel(db, parcel, year, config=config)
            # row is None for a vacant parcel (no active tenants); exempt
            # or fully-fulfilled parcels are dropped, not billed at 0 --
            # same rule /work-hours/evaluation itself applies.
            if row is None or row["exempt"] or row["amount_due"] <= 0:
                continue
            amounts_by_parcel[parcel.id] = row["amount_due"]
        return WorkHoursMode.PER_PARCEL, amounts_by_parcel, {}

    result = await db.execute(
        select(Member).where(Member.deleted_at.is_(None), Member.parcel_assignments.any())
    )
    amounts_by_member: Dict[str, float] = {}
    for member in result.scalars().all():
        row = await evaluate_member(db, member, year, config=config)
        if row["exempt"] or row["amount_due"] <= 0:
            continue
        amounts_by_member[member.id] = row["amount_due"]
    return WorkHoursMode.PER_MEMBER, {}, amounts_by_member
