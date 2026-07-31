"""
Generic metering module: covers water AND electricity meters via the
same codebase. A MeteringPoint has a "medium" (WATER/ELECTRICITY); the
entire logic (consumption calculation, plausibility checking, readings,
evaluation) is identical regardless of medium.

create_metering_router() is a factory function: it produces a fully
configured router for ONE medium. main.py instantiates it twice (for
/water and /electricity) -- so the logic stays maintained in a single
place instead of being duplicated per medium.
"""
import csv
import io
import urllib.parse
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Optional, List

from fastapi import APIRouter, Request, Form, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models import (
    MeteringPoint, MeteringPointType, MeteringMedium, Meter, MeterReading,
    Parcel, ParcelStatus,
)
from app.permissions import require_permission
from app.i18n import t_for, translate, DEFAULT_LANGUAGE
from app.module_flags import require_module
from app.meter_utils import (
    calculate_consumption, check_monotonicity, total_consumption_for_type, reading_before_year
)

from app.templating import templates
templates.env.filters["fmt"] = lambda value, places: f"{float(value):.{places}f}"


def _parse_number(value: str, decimal_places: int) -> Optional[Decimal]:
    value = value.strip().replace(",", ".")
    if not value:
        return None
    try:
        parsed_value = Decimal(value)
    except InvalidOperation:
        return None
    quant = Decimal("1") if decimal_places == 0 else Decimal("1." + "0" * decimal_places)
    return parsed_value.quantize(quant)


def create_metering_router(
    medium: MeteringMedium,
    url_prefix: str,
    modul_name: str,
    medium_label_key: str,
    unit: str,
    icon: str,
    decimal_places: int,
) -> APIRouter:
    """
    Produces a complete router for a metering medium.

    Args:
        medium: MeteringMedium.WATER or MeteringMedium.ELECTRICITY
        url_prefix: e.g. "/water" or "/electricity"
        modul_name: key for the module flag, e.g. "water"/"electricity"
        medium_label_key: translation key for the display name, e.g.
            "metering.medium.water"/"metering.medium.electricity" -- a
            key instead of a ready-made string, because the router is
            instantiated once at startup, but the display language can
            change per request (see app/i18n.py). The (deliberately
            still German) CSV export nonetheless uses a fixed German
            text, see medium_label_de further below.
        unit: e.g. "m³"/"kWh"
        icon: Bootstrap icon class, e.g. "bi-droplet"/"bi-lightning-charge"
        decimal_places: number of decimal places for display/input
    """
    router = APIRouter(
        prefix=url_prefix,
        tags=[modul_name],
        dependencies=[Depends(require_module(modul_name))],
    )

    # German display name, exclusively for the (still German) CSV
    # export -- see medium_label_key above for why the translated
    # display name is NOT resolved here, but per request instead.
    medium_label_de = translate(medium_label_key, DEFAULT_LANGUAGE)

    def medium_label(request: Request) -> str:
        return t_for(request, medium_label_key)

    base_context_without_label = {
        "medium": medium.value,
        "modul_name": modul_name,
        "unit": unit,
        "icon": icon,
        "url_prefix": url_prefix,
        "decimal_places": decimal_places,
    }

    def base_context(request: Request) -> dict:
        return {**base_context_without_label, "medium_label": medium_label(request)}

    async def _load_metering_point_with_details(db: AsyncSession, metering_point_id: str) -> Optional[MeteringPoint]:
        result = await db.execute(
            select(MeteringPoint)
            .options(
                selectinload(MeteringPoint.parcel),
                selectinload(MeteringPoint.meters).selectinload(Meter.readings),
            )
            .where(MeteringPoint.id == metering_point_id, MeteringPoint.medium == medium)
        )
        return result.scalar_one_or_none()

    async def _load_all_metering_points(db: AsyncSession) -> List[MeteringPoint]:
        result = await db.execute(
            select(MeteringPoint)
            .options(
                selectinload(MeteringPoint.parcel),
                selectinload(MeteringPoint.meters).selectinload(Meter.readings),
            )
            .where(MeteringPoint.medium == medium)
        )
        return result.scalars().all()

    # -----------------------------------------------------------------
    # Overview
    # -----------------------------------------------------------------

    @router.get("/", response_class=HTMLResponse)
    async def overview(
        request: Request,
        year: Optional[int] = None,
        db: AsyncSession = Depends(get_db),
    ):
        user = await require_permission(request, db, modul_name, "read")
        if not year:
            year = date.today().year

        all_points = await _load_all_metering_points(db)
        main_meters = [a for a in all_points if a.type == MeteringPointType.MAIN_METER]
        parcels = [a for a in all_points if a.type == MeteringPointType.PARCEL]
        club_points = [a for a in all_points if a.type == MeteringPointType.CLUB]

        main_consumption = total_consumption_for_type(main_meters, year)
        parcel_consumption = total_consumption_for_type(parcels, year)
        club_consumption = total_consumption_for_type(club_points, year)

        warning = None
        if main_consumption > 0 and (parcel_consumption + club_consumption) > main_consumption:
            warning = t_for(
                request, "metering.errors.overall_plausibility_overview",
                parcels=parcel_consumption, club=club_consumption, main=main_consumption, unit=unit, year=year,
            )

        open_readings_count = 0
        for a in all_points:
            z = a.current_meter
            if z and not any(zs.year == year for zs in z.readings):
                open_readings_count += 1

        available_years = sorted({
            zs.year for a in all_points for z in a.meters for zs in z.readings
        }, reverse=True)
        if year not in available_years:
            available_years.insert(0, year)

        return templates.TemplateResponse("metering/overview.html", {
            **base_context(request),
            "request": request, "user": user, "year": year,
            "available_years": available_years,
            "main_meter_count": len(main_meters),
            "parcel_count": len(parcels),
            "club_count": len(club_points),
            "main_consumption": main_consumption,
            "parcel_consumption": parcel_consumption,
            "club_consumption": club_consumption,
            "warning": warning,
            "open_readings": open_readings_count,
        })

    # -----------------------------------------------------------------
    # MeteringPoints: list, create, detail, edit, delete
    # -----------------------------------------------------------------

    @router.get("/metering-points", response_class=HTMLResponse)
    async def metering_points_list(request: Request, db: AsyncSession = Depends(get_db)):
        user = await require_permission(request, db, modul_name, "read")
        all_points = await _load_all_metering_points(db)

        def sortkey(a):
            if a.type == MeteringPointType.MAIN_METER:
                return (0, "")
            if a.type == MeteringPointType.PARCEL:
                return (1, a.parcel.plot_number if a.parcel else "")
            return (2, a.label or "")

        all_points.sort(key=sortkey)

        return templates.TemplateResponse("metering/metering_points_list.html", {
            **base_context(request),
            "request": request, "user": user,
            "metering_points": all_points, "MeteringPointType": MeteringPointType,
            "year": date.today().year,
        })

    @router.get("/metering-points/new", response_class=HTMLResponse)
    async def metering_point_new_page(request: Request, db: AsyncSession = Depends(get_db)):
        user = await require_permission(request, db, modul_name, "write")
        result = await db.execute(
            select(Parcel).where(Parcel.status == ParcelStatus.ACTIVE).order_by(Parcel.plot_number)
        )
        all_parcels = result.scalars().all()

        return templates.TemplateResponse("metering/metering_point_form.html", {
            **base_context(request),
            "request": request, "user": user,
            "all_parcels": all_parcels, "today": date.today().isoformat(),
        })

    @router.post("/metering-points/new")
    async def metering_point_create(
        request: Request,
        type: str = Form(...),
        parcel_id: str = Form(""),
        label: str = Form(""),
        notes: str = Form(""),
        number: str = Form(...),
        calibrated_until: str = Form(""),
        installed_at: str = Form(""),
        initial_reading: str = Form("0"),
        db: AsyncSession = Depends(get_db),
    ):
        await require_permission(request, db, modul_name, "write")

        metering_point = MeteringPoint(
            medium=medium,
            type=MeteringPointType(type),
            parcel_id=parcel_id.strip() or None,
            label=label.strip() or None,
            notes=notes.strip() or None,
        )
        db.add(metering_point)
        await db.flush()

        reading = _parse_number(initial_reading, decimal_places) or Decimal("0")

        meter = Meter(
            metering_point_id=metering_point.id,
            number=number.strip(),
            is_active=True,
            calibrated_until=int(calibrated_until) if calibrated_until.strip() else None,
            installed_at=date.fromisoformat(installed_at) if installed_at.strip() else None,
            initial_reading=reading,
        )
        db.add(meter)

        await db.commit()
        return RedirectResponse(f"{url_prefix}/metering-points/{metering_point.id}", status_code=302)

    @router.get("/metering-points/{metering_point_id}", response_class=HTMLResponse)
    async def metering_point_detail(
        metering_point_id: str,
        request: Request,
        db: AsyncSession = Depends(get_db),
    ):
        user = await require_permission(request, db, modul_name, "read")
        metering_point = await _load_metering_point_with_details(db, metering_point_id)
        if not metering_point:
            raise HTTPException(status_code=404, detail=t_for(request, "metering.errors.point_not_found", medium=medium_label(request)))

        current_meter = metering_point.current_meter
        former_meters = sorted(
            [z for z in metering_point.meters if not z.is_active],
            key=lambda z: z.removed_at or date.min,
            reverse=True,
        )

        readings_with_consumption = []
        if current_meter:
            for z in sorted(current_meter.readings, key=lambda z: z.year, reverse=True):
                readings_with_consumption.append({
                    "reading": z,
                    "consumption": calculate_consumption(current_meter, z.year),
                })

        return templates.TemplateResponse("metering/metering_point_detail.html", {
            **base_context(request),
            "request": request, "user": user,
            "metering_point": metering_point,
            "current_meter": current_meter,
            "former_meters": former_meters,
            "readings_with_consumption": readings_with_consumption,
            "today": date.today().isoformat(),
            "current_year": date.today().year,
            "MeteringPointType": MeteringPointType,
        })

    @router.post("/metering-points/{metering_point_id}/edit")
    async def metering_point_update(
        metering_point_id: str,
        request: Request,
        label: str = Form(""),
        notes: str = Form(""),
        db: AsyncSession = Depends(get_db),
    ):
        await require_permission(request, db, modul_name, "write")
        result = await db.execute(
            select(MeteringPoint).where(MeteringPoint.id == metering_point_id, MeteringPoint.medium == medium)
        )
        metering_point = result.scalar_one_or_none()
        if not metering_point:
            raise HTTPException(status_code=404)

        metering_point.label = label.strip() or None
        metering_point.notes = notes.strip() or None
        await db.commit()
        return RedirectResponse(f"{url_prefix}/metering-points/{metering_point_id}", status_code=302)

    @router.post("/metering-points/{metering_point_id}/delete")
    async def metering_point_delete(
        metering_point_id: str,
        request: Request,
        db: AsyncSession = Depends(get_db),
    ):
        await require_permission(request, db, modul_name, "delete")
        result = await db.execute(
            select(MeteringPoint).where(MeteringPoint.id == metering_point_id, MeteringPoint.medium == medium)
        )
        metering_point = result.scalar_one_or_none()
        if metering_point:
            await db.delete(metering_point)
            await db.commit()
        return RedirectResponse(f"{url_prefix}/metering-points", status_code=302)

    # -----------------------------------------------------------------
    # Swap meter
    # -----------------------------------------------------------------

    @router.post("/metering-points/{metering_point_id}/meter/exchange")
    async def meter_exchange(
        metering_point_id: str,
        request: Request,
        new_number: str = Form(...),
        removed_at: str = Form(...),
        installed_at: str = Form(...),
        calibrated_until: str = Form(""),
        initial_reading: str = Form("0"),
        db: AsyncSession = Depends(get_db),
    ):
        await require_permission(request, db, modul_name, "write")
        metering_point = await _load_metering_point_with_details(db, metering_point_id)
        if not metering_point:
            raise HTTPException(status_code=404)

        old_meter = metering_point.current_meter
        if old_meter:
            old_meter.is_active = False
            old_meter.removed_at = date.fromisoformat(removed_at)

        new_meter = Meter(
            metering_point_id=metering_point_id,
            number=new_number.strip(),
            is_active=True,
            calibrated_until=int(calibrated_until) if calibrated_until.strip() else None,
            installed_at=date.fromisoformat(installed_at),
            initial_reading=_parse_number(initial_reading, decimal_places) or Decimal("0"),
        )
        db.add(new_meter)
        await db.commit()
        return RedirectResponse(f"{url_prefix}/metering-points/{metering_point_id}", status_code=302)

    # -----------------------------------------------------------------
    # Meter readings: create, delete
    # -----------------------------------------------------------------

    @router.post("/metering-points/{metering_point_id}/readings/new")
    async def reading_create(
        metering_point_id: str,
        request: Request,
        year: int = Form(...),
        date_value: str = Form(..., alias="date"),
        reading: str = Form(...),
        note: str = Form(""),
        return_url: str = Form(f"{url_prefix}/readings"),
        db: AsyncSession = Depends(get_db),
    ):
        user = await require_permission(request, db, modul_name, "write")
        metering_point = await _load_metering_point_with_details(db, metering_point_id)
        if not metering_point:
            raise HTTPException(status_code=404)

        meter = metering_point.current_meter
        if not meter:
            raise HTTPException(status_code=400, detail=t_for(request, "metering.errors.no_active_meter"))

        new_reading = _parse_number(reading, decimal_places)
        if new_reading is None:
            message = urllib.parse.quote(t_for(request, "metering.errors.invalid_reading"))
            return RedirectResponse(f"{return_url}?error={message}", status_code=302)

        error_info = check_monotonicity(meter, year, new_reading)
        if error_info:
            error = t_for(request, error_info[0], **error_info[1])
            return RedirectResponse(f"{return_url}?error={urllib.parse.quote(error)}", status_code=302)

        existing = next((z for z in meter.readings if z.year == year), None)
        if existing:
            existing.reading = new_reading
            existing.date = date.fromisoformat(date_value)
            existing.note = note.strip() or None
            existing.recorded_by_id = user.id
        else:
            db.add(MeterReading(
                meter_id=meter.id,
                year=year,
                date=date.fromisoformat(date_value),
                reading=new_reading,
                note=note.strip() or None,
                recorded_by_id=user.id,
            ))

        await db.commit()
        return RedirectResponse(return_url, status_code=302)

    @router.post("/readings/{reading_id}/delete")
    async def reading_delete(
        reading_id: str,
        request: Request,
        db: AsyncSession = Depends(get_db),
    ):
        await require_permission(request, db, modul_name, "delete")
        result = await db.execute(select(MeterReading).where(MeterReading.id == reading_id))
        reading_entry = result.scalar_one_or_none()
        metering_point_id = None
        if reading_entry:
            meter_result = await db.execute(select(Meter).where(Meter.id == reading_entry.meter_id))
            meter = meter_result.scalar_one_or_none()
            metering_point_id = meter.metering_point_id if meter else None
            await db.delete(reading_entry)
            await db.commit()

        if metering_point_id:
            return RedirectResponse(f"{url_prefix}/metering-points/{metering_point_id}", status_code=302)
        return RedirectResponse(f"{url_prefix}/metering-points", status_code=302)

    # -----------------------------------------------------------------
    # Readings (mobile-friendly bulk entry)
    # -----------------------------------------------------------------

    @router.get("/readings", response_class=HTMLResponse)
    async def readings_list(
        request: Request,
        year: Optional[int] = None,
        error: Optional[str] = None,
        db: AsyncSession = Depends(get_db),
    ):
        user = await require_permission(request, db, modul_name, "read")
        if not year:
            year = date.today().year

        all_points = await _load_all_metering_points(db)

        def prepare_rows(type):
            filtered = [a for a in all_points if a.type == type]
            rows = []
            for a in filtered:
                z = a.current_meter
                if not z:
                    continue
                current_reading = next((zs for zs in z.readings if zs.year == year), None)
                rows.append({
                    "metering_point": a,
                    "meter": z,
                    "previous_year_value": reading_before_year(
                        z, year, exclude_id=current_reading.id if current_reading else None
                    ),
                    "entry": current_reading,
                })
            return rows

        main_meter_rows = prepare_rows(MeteringPointType.MAIN_METER)
        parcel_rows = sorted(
            prepare_rows(MeteringPointType.PARCEL),
            key=lambda z: z["metering_point"].parcel.plot_number if z["metering_point"].parcel else ""
        )
        club_rows = prepare_rows(MeteringPointType.CLUB)

        return templates.TemplateResponse("metering/readings_list.html", {
            **base_context(request),
            "request": request, "user": user, "year": year,
            "main_meter_rows": main_meter_rows,
            "parcel_rows": parcel_rows,
            "club_rows": club_rows,
            "error": error,
            "today": date.today().isoformat(),
        })

    # -----------------------------------------------------------------
    # Evaluation
    # -----------------------------------------------------------------

    @router.get("/evaluation", response_class=HTMLResponse)
    async def evaluation(
        request: Request,
        year: Optional[int] = None,
        db: AsyncSession = Depends(get_db),
    ):
        user = await require_permission(request, db, modul_name, "read")
        if not year:
            year = date.today().year

        all_points = await _load_all_metering_points(db)

        def rows_for_type(type):
            filtered = [a for a in all_points if a.type == type]
            rows = []
            for a in filtered:
                z = a.current_meter
                consumption = calculate_consumption(z, year) if z else None
                rows.append({"metering_point": a, "meter": z, "consumption": consumption})
            return rows

        main_meter_rows = rows_for_type(MeteringPointType.MAIN_METER)
        parcel_rows = sorted(
            rows_for_type(MeteringPointType.PARCEL),
            key=lambda z: z["metering_point"].parcel.plot_number if z["metering_point"].parcel else ""
        )
        club_rows = rows_for_type(MeteringPointType.CLUB)

        main_total = sum((z["consumption"] for z in main_meter_rows if z["consumption"] is not None), Decimal("0"))
        parcel_total = sum((z["consumption"] for z in parcel_rows if z["consumption"] is not None), Decimal("0"))
        club_total = sum((z["consumption"] for z in club_rows if z["consumption"] is not None), Decimal("0"))

        warning = None
        if main_total > 0 and (parcel_total + club_total) > main_total:
            warning = t_for(
                request, "metering.errors.overall_plausibility_evaluation",
                total=parcel_total + club_total, main=main_total, unit=unit,
            )

        available_years = sorted({
            zs.year for a in all_points for z in a.meters for zs in z.readings
        }, reverse=True)
        if year not in available_years:
            available_years.insert(0, year)

        return templates.TemplateResponse("metering/evaluation.html", {
            **base_context(request),
            "request": request, "user": user, "year": year,
            "available_years": available_years,
            "main_meter_rows": main_meter_rows,
            "parcel_rows": parcel_rows,
            "club_rows": club_rows,
            "main_total": main_total,
            "parcel_total": parcel_total,
            "club_total": club_total,
            "warning": warning,
        })

    @router.get("/evaluation/csv")
    async def evaluation_csv(
        request: Request,
        year: Optional[int] = None,
        db: AsyncSession = Depends(get_db),
    ):
        await require_permission(request, db, modul_name, "read")
        if not year:
            year = date.today().year

        all_points = await _load_all_metering_points(db)

        output = io.StringIO()
        writer = csv.writer(output, delimiter=";")
        writer.writerow(["Typ", "Zählpunkt", f"{medium_label_de}zähler-Nr.", "Zählerstand", f"Verbrauch ({unit})"])

        type_label = {
            MeteringPointType.MAIN_METER: "Hauptzähler",
            MeteringPointType.PARCEL: "Parcel",
            MeteringPointType.CLUB: "Verein",
        }

        for a in sorted(all_points, key=lambda a: (a.type.value, a.display_name)):
            z = a.current_meter
            if not z:
                continue
            entry = next((zs for zs in z.readings if zs.year == year), None)
            consumption = calculate_consumption(z, year)
            writer.writerow([
                type_label.get(a.type, a.type.value),
                a.display_name,
                z.number,
                f"{entry.reading:.{decimal_places}f}".replace(".", ",") if entry else "",
                f"{consumption:.{decimal_places}f}".replace(".", ",") if consumption is not None else "",
            ])

        output.seek(0)
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename={modul_name}verbrauch_{year}.csv"},
        )

    return router
