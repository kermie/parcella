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
from app.auth import require_user
from app.i18n import t_for, translate, DEFAULT_LANGUAGE
from app.module_flags import require_module
from app.meter_utils import (
    calculate_consumption, check_monotonicity, total_consumption_for_type, reading_before_year
)

from app.templating import templates
templates.env.filters["fmt"] = lambda value, stellen: f"{float(value):.{stellen}f}"


def _parse_number(value: str, dezimalstellen: int) -> Optional[Decimal]:
    value = value.strip().replace(",", ".")
    if not value:
        return None
    try:
        zahl = Decimal(value)
    except InvalidOperation:
        return None
    quant = Decimal("1") if dezimalstellen == 0 else Decimal("1." + "0" * dezimalstellen)
    return zahl.quantize(quant)


def create_metering_router(
    medium: MeteringMedium,
    url_prefix: str,
    modul_name: str,
    medium_label_key: str,
    unit: str,
    icon: str,
    dezimalstellen: int,
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
        dezimalstellen: number of decimal places for display/input
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

    basis_context_ohne_label = {
        "medium": medium.value,
        "unit": unit,
        "icon": icon,
        "url_prefix": url_prefix,
        "dezimalstellen": dezimalstellen,
    }

    def basis_context(request: Request) -> dict:
        return {**basis_context_ohne_label, "medium_label": medium_label(request)}

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
        user = await require_user(request, db)
        if not year:
            year = date.today().year

        alle = await _load_all_metering_points(db)
        haupt = [a for a in alle if a.type == MeteringPointType.MAIN_METER]
        parcels = [a for a in alle if a.type == MeteringPointType.PARCEL]
        verein = [a for a in alle if a.type == MeteringPointType.CLUB]

        v_haupt = total_consumption_for_type(haupt, year)
        v_parzellen = total_consumption_for_type(parcels, year)
        v_verein = total_consumption_for_type(verein, year)

        warnung = None
        if v_haupt > 0 and (v_parzellen + v_verein) > v_haupt:
            warnung = t_for(
                request, "metering.errors.overall_plausibility_overview",
                parcels=v_parzellen, club=v_verein, main=v_haupt, unit=unit, year=year,
            )

        offene = 0
        for a in alle:
            z = a.current_meter
            if z and not any(zs.year == year for zs in z.readings):
                offene += 1

        verfuegbare_jahre = sorted({
            zs.year for a in alle for z in a.meters for zs in z.readings
        }, reverse=True)
        if year not in verfuegbare_jahre:
            verfuegbare_jahre.insert(0, year)

        return templates.TemplateResponse("metering/overview.html", {
            **basis_context(request),
            "request": request, "user": user, "year": year,
            "verfuegbare_jahre": verfuegbare_jahre,
            "anzahl_hauptzaehler": len(haupt),
            "anzahl_parzellen": len(parcels),
            "anzahl_verein": len(verein),
            "verbrauch_haupt": v_haupt,
            "verbrauch_parzellen": v_parzellen,
            "verbrauch_verein": v_verein,
            "warnung": warnung,
            "offene_ablesungen": offene,
        })

    # -----------------------------------------------------------------
    # MeteringPoints: list, create, detail, edit, delete
    # -----------------------------------------------------------------

    @router.get("/metering-points", response_class=HTMLResponse)
    async def metering_points_list(request: Request, db: AsyncSession = Depends(get_db)):
        user = await require_user(request, db)
        alle = await _load_all_metering_points(db)

        def sortkey(a):
            if a.type == MeteringPointType.MAIN_METER:
                return (0, "")
            if a.type == MeteringPointType.PARCEL:
                return (1, a.parcel.plot_number if a.parcel else "")
            return (2, a.label or "")

        alle.sort(key=sortkey)

        return templates.TemplateResponse("metering/metering_points_list.html", {
            **basis_context(request),
            "request": request, "user": user,
            "metering_points": alle, "MeteringPointType": MeteringPointType,
            "year": date.today().year,
        })

    @router.get("/metering-points/new", response_class=HTMLResponse)
    async def metering_point_new_page(request: Request, db: AsyncSession = Depends(get_db)):
        user = await require_user(request, db)
        result = await db.execute(
            select(Parcel).where(Parcel.status == ParcelStatus.ACTIVE).order_by(Parcel.plot_number)
        )
        alle_parzellen = result.scalars().all()

        return templates.TemplateResponse("metering/metering_point_form.html", {
            **basis_context(request),
            "request": request, "user": user,
            "alle_parzellen": alle_parzellen, "heute": date.today().isoformat(),
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
        await require_user(request, db)

        metering_point = MeteringPoint(
            medium=medium,
            type=MeteringPointType(type),
            parcel_id=parcel_id.strip() or None,
            label=label.strip() or None,
            notes=notes.strip() or None,
        )
        db.add(metering_point)
        await db.flush()

        reading = _parse_number(initial_reading, dezimalstellen) or Decimal("0")

        zaehler = Meter(
            metering_point_id=metering_point.id,
            number=number.strip(),
            is_active=True,
            calibrated_until=int(calibrated_until) if calibrated_until.strip() else None,
            installed_at=date.fromisoformat(installed_at) if installed_at.strip() else None,
            initial_reading=reading,
        )
        db.add(zaehler)

        await db.commit()
        return RedirectResponse(f"{url_prefix}/metering-points/{metering_point.id}", status_code=302)

    @router.get("/metering-points/{metering_point_id}", response_class=HTMLResponse)
    async def metering_point_detail(
        metering_point_id: str,
        request: Request,
        db: AsyncSession = Depends(get_db),
    ):
        user = await require_user(request, db)
        metering_point = await _load_metering_point_with_details(db, metering_point_id)
        if not metering_point:
            raise HTTPException(status_code=404, detail=t_for(request, "metering.errors.point_not_found", medium=medium_label(request)))

        current_meter = metering_point.current_meter
        former_meters = sorted(
            [z for z in metering_point.meters if not z.is_active],
            key=lambda z: z.removed_at or date.min,
            reverse=True,
        )

        staende_mit_verbrauch = []
        if current_meter:
            for z in sorted(current_meter.readings, key=lambda z: z.year, reverse=True):
                staende_mit_verbrauch.append({
                    "reading": z,
                    "verbrauch": calculate_consumption(current_meter, z.year),
                })

        return templates.TemplateResponse("metering/metering_point_detail.html", {
            **basis_context(request),
            "request": request, "user": user,
            "metering_point": metering_point,
            "current_meter": current_meter,
            "former_meters": former_meters,
            "staende_mit_verbrauch": staende_mit_verbrauch,
            "heute": date.today().isoformat(),
            "aktuelles_jahr": date.today().year,
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
        await require_user(request, db)
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
        await require_user(request, db)
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
        neue_nummer: str = Form(...),
        removed_at: str = Form(...),
        installed_at: str = Form(...),
        calibrated_until: str = Form(""),
        initial_reading: str = Form("0"),
        db: AsyncSession = Depends(get_db),
    ):
        await require_user(request, db)
        metering_point = await _load_metering_point_with_details(db, metering_point_id)
        if not metering_point:
            raise HTTPException(status_code=404)

        alter_zaehler = metering_point.current_meter
        if alter_zaehler:
            alter_zaehler.is_active = False
            alter_zaehler.removed_at = date.fromisoformat(removed_at)

        neuer_zaehler = Meter(
            metering_point_id=metering_point_id,
            number=neue_nummer.strip(),
            is_active=True,
            calibrated_until=int(calibrated_until) if calibrated_until.strip() else None,
            installed_at=date.fromisoformat(installed_at),
            initial_reading=_parse_number(initial_reading, dezimalstellen) or Decimal("0"),
        )
        db.add(neuer_zaehler)
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
        rueck_url: str = Form(f"{url_prefix}/readings"),
        db: AsyncSession = Depends(get_db),
    ):
        user = await require_user(request, db)
        metering_point = await _load_metering_point_with_details(db, metering_point_id)
        if not metering_point:
            raise HTTPException(status_code=404)

        zaehler = metering_point.current_meter
        if not zaehler:
            raise HTTPException(status_code=400, detail=t_for(request, "metering.errors.no_active_meter"))

        neuer_stand = _parse_number(reading, dezimalstellen)
        if neuer_stand is None:
            meldung = urllib.parse.quote(t_for(request, "metering.errors.invalid_reading"))
            return RedirectResponse(f"{rueck_url}?fehler={meldung}", status_code=302)

        fehler_info = check_monotonicity(zaehler, year, neuer_stand)
        if fehler_info:
            fehler = t_for(request, fehler_info[0], **fehler_info[1])
            return RedirectResponse(f"{rueck_url}?fehler={urllib.parse.quote(fehler)}", status_code=302)

        existing = next((z for z in zaehler.readings if z.year == year), None)
        if existing:
            existing.reading = neuer_stand
            existing.date = date.fromisoformat(date_value)
            existing.note = note.strip() or None
            existing.recorded_by_id = user.id
        else:
            db.add(MeterReading(
                meter_id=zaehler.id,
                year=year,
                date=date.fromisoformat(date_value),
                reading=neuer_stand,
                note=note.strip() or None,
                recorded_by_id=user.id,
            ))

        await db.commit()
        return RedirectResponse(rueck_url, status_code=302)

    @router.post("/readings/{reading_id}/delete")
    async def reading_delete(
        reading_id: str,
        request: Request,
        db: AsyncSession = Depends(get_db),
    ):
        await require_user(request, db)
        result = await db.execute(select(MeterReading).where(MeterReading.id == reading_id))
        reading_entry = result.scalar_one_or_none()
        metering_point_id = None
        if reading_entry:
            zaehler_result = await db.execute(select(Meter).where(Meter.id == reading_entry.zaehler_id))
            zaehler = zaehler_result.scalar_one_or_none()
            metering_point_id = zaehler.metering_point_id if zaehler else None
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
        fehler: Optional[str] = None,
        db: AsyncSession = Depends(get_db),
    ):
        user = await require_user(request, db)
        if not year:
            year = date.today().year

        alle = await _load_all_metering_points(db)

        def prepare_rows(type):
            gefiltert = [a for a in alle if a.type == type]
            zeilen = []
            for a in gefiltert:
                z = a.current_meter
                if not z:
                    continue
                aktuelle_ablesung = next((zs for zs in z.readings if zs.year == year), None)
                zeilen.append({
                    "metering_point": a,
                    "zaehler": z,
                    "vorjahreswert": reading_before_year(
                        z, year, exclude_id=aktuelle_ablesung.id if aktuelle_ablesung else None
                    ),
                    "entry": aktuelle_ablesung,
                })
            return zeilen

        hauptzaehler_zeilen = prepare_rows(MeteringPointType.MAIN_METER)
        parzellen_zeilen = sorted(
            prepare_rows(MeteringPointType.PARCEL),
            key=lambda z: z["metering_point"].parcel.plot_number if z["metering_point"].parcel else ""
        )
        verein_zeilen = prepare_rows(MeteringPointType.CLUB)

        return templates.TemplateResponse("metering/readings_list.html", {
            **basis_context(request),
            "request": request, "user": user, "year": year,
            "hauptzaehler_zeilen": hauptzaehler_zeilen,
            "parzellen_zeilen": parzellen_zeilen,
            "verein_zeilen": verein_zeilen,
            "fehler": fehler,
            "heute": date.today().isoformat(),
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
        user = await require_user(request, db)
        if not year:
            year = date.today().year

        alle = await _load_all_metering_points(db)

        def rows_for_type(type):
            gefiltert = [a for a in alle if a.type == type]
            zeilen = []
            for a in gefiltert:
                z = a.current_meter
                verbrauch = calculate_consumption(z, year) if z else None
                zeilen.append({"metering_point": a, "zaehler": z, "verbrauch": verbrauch})
            return zeilen

        hauptzaehler_zeilen = rows_for_type(MeteringPointType.MAIN_METER)
        parzellen_zeilen = sorted(
            rows_for_type(MeteringPointType.PARCEL),
            key=lambda z: z["metering_point"].parcel.plot_number if z["metering_point"].parcel else ""
        )
        verein_zeilen = rows_for_type(MeteringPointType.CLUB)

        summe_haupt = sum((z["verbrauch"] for z in hauptzaehler_zeilen if z["verbrauch"] is not None), Decimal("0"))
        summe_parzellen = sum((z["verbrauch"] for z in parzellen_zeilen if z["verbrauch"] is not None), Decimal("0"))
        summe_verein = sum((z["verbrauch"] for z in verein_zeilen if z["verbrauch"] is not None), Decimal("0"))

        warnung = None
        if summe_haupt > 0 and (summe_parzellen + summe_verein) > summe_haupt:
            warnung = t_for(
                request, "metering.errors.overall_plausibility_evaluation",
                total=summe_parzellen + summe_verein, main=summe_haupt, unit=unit,
            )

        verfuegbare_jahre = sorted({
            zs.year for a in alle for z in a.meters for zs in z.readings
        }, reverse=True)
        if year not in verfuegbare_jahre:
            verfuegbare_jahre.insert(0, year)

        return templates.TemplateResponse("metering/evaluation.html", {
            **basis_context(request),
            "request": request, "user": user, "year": year,
            "verfuegbare_jahre": verfuegbare_jahre,
            "hauptzaehler_zeilen": hauptzaehler_zeilen,
            "parzellen_zeilen": parzellen_zeilen,
            "verein_zeilen": verein_zeilen,
            "summe_haupt": summe_haupt,
            "summe_parzellen": summe_parzellen,
            "summe_verein": summe_verein,
            "warnung": warnung,
        })

    @router.get("/evaluation/csv")
    async def evaluation_csv(
        request: Request,
        year: Optional[int] = None,
        db: AsyncSession = Depends(get_db),
    ):
        await require_user(request, db)
        if not year:
            year = date.today().year

        alle = await _load_all_metering_points(db)

        output = io.StringIO()
        writer = csv.writer(output, delimiter=";")
        writer.writerow(["Typ", "Zählpunkt", f"{medium_label_de}zähler-Nr.", "Zählerstand", f"Verbrauch ({unit})"])

        typ_label = {
            MeteringPointType.MAIN_METER: "Hauptzähler",
            MeteringPointType.PARCEL: "Parcel",
            MeteringPointType.CLUB: "Verein",
        }

        for a in sorted(alle, key=lambda a: (a.type.value, a.display_name)):
            z = a.current_meter
            if not z:
                continue
            entry = next((zs for zs in z.readings if zs.year == year), None)
            verbrauch = calculate_consumption(z, year)
            writer.writerow([
                typ_label.get(a.type, a.type.value),
                a.display_name,
                z.number,
                f"{entry.reading:.{dezimalstellen}f}".replace(".", ",") if entry else "",
                f"{verbrauch:.{dezimalstellen}f}".replace(".", ",") if verbrauch is not None else "",
            ])

        output.seek(0)
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename={modul_name}verbrauch_{year}.csv"},
        )

    return router
