"""
API router factory for metering (water & electricity) -- analogous to
the HTML router factory in app/routers/metering.py. One codebase for
both media, instantiated twice (see main.py).
"""
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models import MeteringPoint, MeteringPointType, MeteringMedium, Meter, MeterReading, User
from app.api_auth import get_current_api_user, require_write_access
from app.module_flags import require_module
from app.meter_utils import calculate_consumption, check_monotonicity, format_monotonicity_error_de, total_consumption_for_type
from app.schemas import (
    MeteringPointOut, MeteringPointDetailOut, MeteringPointCreate, MeteringPointUpdate,
    MeterOut, MeterSwapRequest, MeterReadingCreate, MeterReadingOut,
    ConsumptionRowOut,
)


def create_metering_api_router(
    medium: MeteringMedium, url_prefix: str, modul_name: str,
) -> APIRouter:
    router = APIRouter(
        prefix=f"/api/v1{url_prefix}",
        tags=[f"API: {modul_name.capitalize()}"],
        dependencies=[Depends(require_module(modul_name))],
    )

    async def _load_metering_point(db: AsyncSession, metering_point_id: str) -> Optional[MeteringPoint]:
        result = await db.execute(
            select(MeteringPoint)
            .options(selectinload(MeteringPoint.meters).selectinload(Meter.readings))
            .where(MeteringPoint.id == metering_point_id, MeteringPoint.medium == medium)
        )
        return result.scalar_one_or_none()

    @router.get("/metering-points", response_model=List[MeteringPointOut], summary="List metering points")
    async def list_metering_points(
        type: Optional[str] = Query(None, description="MAIN_METER, PARCEL, or CLUB"),
        db: AsyncSession = Depends(get_db),
        user: User = Depends(get_current_api_user),
    ):
        query = select(MeteringPoint).where(MeteringPoint.medium == medium)
        if type:
            query = query.where(MeteringPoint.type == MeteringPointType(type))
        result = await db.execute(query)
        return result.scalars().all()

    @router.get(
        "/metering-points/{metering_point_id}", response_model=MeteringPointDetailOut,
        summary="Retrieve metering point incl. meter history",
    )
    async def get_metering_point(
        metering_point_id: str,
        db: AsyncSession = Depends(get_db),
        user: User = Depends(get_current_api_user),
    ):
        zp = await _load_metering_point(db, metering_point_id)
        if not zp:
            raise HTTPException(status_code=404, detail="Metering point not found")
        out = MeteringPointDetailOut.model_validate(zp)
        out.current_meter = zp.current_meter
        out.former_meters = [z for z in zp.meters if not z.is_active]
        return out

    @router.post(
        "/metering-points", response_model=MeteringPointDetailOut, status_code=status.HTTP_201_CREATED,
        summary="Create metering point",
        description="Creates a metering point including its first meter in a single step.",
    )
    async def create_metering_point(
        daten: MeteringPointCreate,
        db: AsyncSession = Depends(get_db),
        user: User = Depends(require_write_access),
    ):
        zp = MeteringPoint(
            medium=medium, type=MeteringPointType(daten.type),
            parcel_id=daten.parcel_id, label=daten.label, notes=daten.notes,
        )
        db.add(zp)
        await db.flush()

        meter = Meter(
            metering_point_id=zp.id, number=daten.number, is_active=True,
            calibrated_until=daten.calibrated_until, installed_at=daten.installed_at,
            initial_reading=daten.initial_reading,
        )
        db.add(meter)
        await db.commit()

        zp = await _load_metering_point(db, zp.id)
        out = MeteringPointDetailOut.model_validate(zp)
        out.current_meter = zp.current_meter
        out.former_meters = []
        return out

    @router.put("/metering-points/{metering_point_id}", response_model=MeteringPointOut, summary="Update metering point")
    async def update_metering_point(
        metering_point_id: str,
        daten: MeteringPointUpdate,
        db: AsyncSession = Depends(get_db),
        user: User = Depends(require_write_access),
    ):
        result = await db.execute(
            select(MeteringPoint).where(MeteringPoint.id == metering_point_id, MeteringPoint.medium == medium)
        )
        zp = result.scalar_one_or_none()
        if not zp:
            raise HTTPException(status_code=404, detail="Metering point not found")

        for field, value in daten.model_dump(exclude_unset=True).items():
            setattr(zp, field, value)

        await db.commit()
        await db.refresh(zp)
        return zp

    @router.delete(
        "/metering-points/{metering_point_id}", status_code=status.HTTP_204_NO_CONTENT,
        summary="Delete metering point", description="Also deletes all meters and readings (cascade).",
    )
    async def delete_metering_point(
        metering_point_id: str,
        db: AsyncSession = Depends(get_db),
        user: User = Depends(require_write_access),
    ):
        result = await db.execute(
            select(MeteringPoint).where(MeteringPoint.id == metering_point_id, MeteringPoint.medium == medium)
        )
        zp = result.scalar_one_or_none()
        if zp:
            await db.delete(zp)
            await db.commit()

    @router.post(
        "/metering-points/{metering_point_id}/exchange", response_model=MeterOut,
        summary="Exchange meter",
        description="Deactivates the current meter (removal date) and creates a new one.",
    )
    async def exchange_meter(
        metering_point_id: str,
        daten: MeterSwapRequest,
        db: AsyncSession = Depends(get_db),
        user: User = Depends(require_write_access),
    ):
        zp = await _load_metering_point(db, metering_point_id)
        if not zp:
            raise HTTPException(status_code=404, detail="Metering point not found")

        old_meter = zp.current_meter
        if old_meter:
            old_meter.is_active = False
            old_meter.removed_at = daten.removed_at

        new_meter = Meter(
            metering_point_id=metering_point_id, number=daten.new_number, is_active=True,
            calibrated_until=daten.calibrated_until, installed_at=daten.installed_at,
            initial_reading=daten.initial_reading,
        )
        db.add(new_meter)
        await db.commit()
        await db.refresh(new_meter)
        return new_meter

    @router.get(
        "/metering-points/{metering_point_id}/readings", response_model=List[MeterReadingOut],
        summary="List meter readings",
    )
    async def list_meter_readings(
        metering_point_id: str,
        db: AsyncSession = Depends(get_db),
        user: User = Depends(get_current_api_user),
    ):
        zp = await _load_metering_point(db, metering_point_id)
        if not zp:
            raise HTTPException(status_code=404, detail="Metering point not found")
        meter = zp.current_meter
        if not meter:
            return []
        return sorted(meter.readings, key=lambda z: z.year, reverse=True)

    @router.post(
        "/metering-points/{metering_point_id}/readings", response_model=MeterReadingOut,
        status_code=status.HTTP_201_CREATED, summary="Record reading",
        description="Creates a new reading or updates the existing one for the same year. "
                    "Checks plausibility (the reading must not decrease).",
    )
    async def create_reading(
        metering_point_id: str,
        daten: MeterReadingCreate,
        db: AsyncSession = Depends(get_db),
        user: User = Depends(require_write_access),
    ):
        zp = await _load_metering_point(db, metering_point_id)
        if not zp:
            raise HTTPException(status_code=404, detail="Metering point not found")
        meter = zp.current_meter
        if not meter:
            raise HTTPException(status_code=400, detail="No active meter for this metering point")

        error = check_monotonicity(meter, daten.year, daten.reading)
        if error:
            raise HTTPException(status_code=422, detail=format_monotonicity_error_de(*error))

        existing = next((z for z in meter.readings if z.year == daten.year), None)
        if existing:
            existing.reading = daten.reading
            existing.date = daten.date
            existing.note = daten.note
            existing.recorded_by_id = user.id
            await db.commit()
            await db.refresh(existing)
            return existing

        new_reading = MeterReading(
            meter_id=meter.id, year=daten.year, date=daten.date,
            reading=daten.reading, note=daten.note, recorded_by_id=user.id,
        )
        db.add(new_reading)
        await db.commit()
        await db.refresh(new_reading)
        return new_reading

    @router.delete(
        "/readings/{reading_id}", status_code=status.HTTP_204_NO_CONTENT,
        summary="Delete reading",
    )
    async def delete_reading(
        reading_id: str,
        db: AsyncSession = Depends(get_db),
        user: User = Depends(require_write_access),
    ):
        result = await db.execute(select(MeterReading).where(MeterReading.id == reading_id))
        reading_entry = result.scalar_one_or_none()
        if reading_entry:
            await db.delete(reading_entry)
            await db.commit()

    @router.get(
        "/evaluation/{year}", response_model=List[ConsumptionRowOut],
        summary="Consumption report for a year",
    )
    async def evaluation(
        year: int,
        type: Optional[str] = Query(None, description="Filter by MAIN_METER, PARCEL, or CLUB"),
        db: AsyncSession = Depends(get_db),
        user: User = Depends(get_current_api_user),
    ):
        query = (
            select(MeteringPoint)
            .options(selectinload(MeteringPoint.meters).selectinload(Meter.readings))
            .where(MeteringPoint.medium == medium)
        )
        if type:
            query = query.where(MeteringPoint.type == MeteringPointType(type))
        result = await db.execute(query)
        metering_points = result.scalars().all()

        rows = []
        for zp in metering_points:
            meter = zp.current_meter
            consumption = calculate_consumption(meter, year) if meter else None
            rows.append(ConsumptionRowOut(
                metering_point_id=zp.id, label=zp.display_name,
                meter_number=meter.number if meter else None,
                consumption=consumption,
            ))
        return rows

    return router
