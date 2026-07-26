"""
Work-hours shortfall amounts for invoicing (issue #83): "charge those
who not completely or never used their work sessions, according to
/work-hours/evaluation. Exclude people and garden plots which
exempted or fulfilled their duty."

Reuses the same per-member hour calculation, exemption check, and
year-configuration lookup as /work-hours/evaluation
(app/routers/work_hours.py) and its public-API equivalent
(app/routers/api_work_hours.py), but returns just what invoice
generation needs: how much each parcel or member currently owes for
the year, already excluding anyone exempt or who already fulfilled
their hours (both computed to 0, then dropped from the result
entirely -- see app/invoice_generation.py's WORK_HOURS_SHORTFALL
pricing mode, which bills exactly this, automatically, with no manual
scoping).
"""
from datetime import date
from typing import Dict, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Parcel, ParcelStatus, Member, MemberParcel, WorkHoursMode


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
    from app.routers.work_hours import _get_config_for_year, _calculate_hours_for_member, _is_exempt

    config = await _get_config_for_year(db, year)
    if not config:
        return None, {}, {}

    required = float(config.hours_required)
    rate = float(config.rate_per_hour_eur)

    if config.mode == WorkHoursMode.PER_PARCEL:
        result = await db.execute(
            select(Parcel)
            .options(selectinload(Parcel.member_assignments).selectinload(MemberParcel.member))
            .where(Parcel.status == ParcelStatus.ACTIVE)
        )
        amounts_by_parcel: Dict[str, float] = {}
        for parcel in result.scalars().all():
            tenants = [
                a.member for a in parcel.member_assignments
                if a.member.deleted_at is None
                and (a.member.member_until is None or a.member.member_until >= date.today())
            ]
            if not tenants:
                continue
            total_hours = 0.0
            any_exempt = False
            for member in tenants:
                hours = await _calculate_hours_for_member(db, member.id, year)
                total_hours += hours["total"]
                if await _is_exempt(db, member.id, year):
                    any_exempt = True
            # ONE exempt tenant is enough to exempt the whole parcel --
            # same any()-not-all() rule as the web evaluation page (see
            # docs/ADR/README.md for the bug that motivated this rule).
            if any_exempt:
                continue
            outstanding = max(0.0, required - total_hours)
            amount = outstanding * rate
            if amount > 0:
                amounts_by_parcel[parcel.id] = amount
        return WorkHoursMode.PER_PARCEL, amounts_by_parcel, {}

    result = await db.execute(
        select(Member).where(Member.deleted_at.is_(None), Member.parcel_assignments.any())
    )
    amounts_by_member: Dict[str, float] = {}
    for member in result.scalars().all():
        if await _is_exempt(db, member.id, year):
            continue
        hours = await _calculate_hours_for_member(db, member.id, year)
        outstanding = max(0.0, required - hours["total"])
        amount = outstanding * rate
        if amount > 0:
            amounts_by_member[member.id] = amount
    return WorkHoursMode.PER_MEMBER, {}, amounts_by_member
