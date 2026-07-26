"""
Club-wide area figures (issues #80, #81, #82).

Area A (leased parcels) is always the live sum of every parcel's area
regardless of lease status -- not a manually-entered ClubSetting -- so
it can't drift out of sync with the parcel data it's actually built
from (see ADR-worthy history in issue #81: it used to be a free-typed
number, mislabeled "municipal" to boot). Area B (communal) is derived
from it (Total area - Area A - Area C) rather than entered either.

Shared between the admin settings page (display only) and invoice
generation's "communal area share" pricing mode (issue #82, see
app/invoice_generation.py), so both agree on exactly the same numbers.
"""
from typing import Optional

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Parcel, ParcelStatus, ClubSetting


async def compute_area_a_sqm(db: AsyncSession) -> float:
    result = await db.scalar(
        select(func.coalesce(func.sum(Parcel.area_sqm), 0)).where(Parcel.status != ParcelStatus.DELETED)
    )
    return float(result or 0)


async def _setting_float(db: AsyncSession, key: str) -> float:
    result = await db.execute(select(ClubSetting).where(ClubSetting.key == key))
    entry = result.scalar_one_or_none()
    if entry and entry.value:
        try:
            return float(entry.value)
        except ValueError:
            return 0.0
    return 0.0


async def compute_area_b_sqm(db: AsyncSession, area_a_sqm: Optional[float] = None) -> float:
    """Area B (communal) = Total area - Area A - Area C."""
    if area_a_sqm is None:
        area_a_sqm = await compute_area_a_sqm(db)
    total_sqm = await _setting_float(db, "flaeche_gesamt_qm")
    area_c_sqm = await _setting_float(db, "flaeche_c_qm")
    return total_sqm - area_a_sqm - area_c_sqm
