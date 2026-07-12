"""
Versicherungsmodul-Router (insurance): Konfiguration (Pakete, Beträge),
Parzellen-Verwaltung, Auswertung.
"""
import csv
import io
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Optional

from fastapi import APIRouter, Request, Form, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models import (
    PropertyInsurancePackage, InsuranceConfiguration, ParcelInsurance,
    AccidentInsuranceAdditionalPerson, Parcel, ParcelStatus, MemberParcel, Member,
)
from app.auth import require_user
from app.module_flags import require_modul
from app.insurance_utils import household_grouping, calculate_insurance_cost

router = APIRouter(
    prefix="/insurance",
    tags=["insurance"],
    dependencies=[Depends(require_modul("insurance"))],
)
templates = Jinja2Templates(directory="app/templates")


def _parse_decimal(value: str) -> Optional[Decimal]:
    value = value.strip().replace(",", ".")
    if not value:
        return None
    try:
        return Decimal(value)
    except InvalidOperation:
        return None


async def _get_configuration(db: AsyncSession, year: int) -> Optional[InsuranceConfiguration]:
    result = await db.execute(
        select(InsuranceConfiguration).where(InsuranceConfiguration.year == year)
    )
    return result.scalar_one_or_none()


async def _get_packages(db: AsyncSession, year: int) -> list:
    result = await db.execute(
        select(PropertyInsurancePackage)
        .where(PropertyInsurancePackage.year == year)
        .order_by(PropertyInsurancePackage.sort_order, PropertyInsurancePackage.amount_eur)
    )
    return result.scalars().all()


async def _get_or_create_pi(db: AsyncSession, parcel_id: str, year: int) -> ParcelInsurance:
    result = await db.execute(
        select(ParcelInsurance)
        .options(
            selectinload(ParcelInsurance.property_package),
            selectinload(ParcelInsurance.additional_persons),
        )
        .where(ParcelInsurance.parcel_id == parcel_id, ParcelInsurance.year == year)
    )
    pi = result.scalar_one_or_none()
    if not pi:
        pi = ParcelInsurance(parcel_id=parcel_id, year=year)
        db.add(pi)
        await db.commit()
        # Frisch angelegte Zeile mit eager-geladenen Beziehungen neu laden.
        # Ohne das würde ein späterer Zugriff auf pi.property_package/
        # pi.additional_persons einen synchronen Lazy-Load auslösen, der mit
        # dem asynchronen Datenbanktreiber zu "MissingGreenlet" führt.
        result = await db.execute(
            select(ParcelInsurance)
            .options(
                selectinload(ParcelInsurance.property_package),
                selectinload(ParcelInsurance.additional_persons),
            )
            .where(ParcelInsurance.id == pi.id)
        )
        pi = result.scalar_one()
    return pi


# ---------------------------------------------------------------------------
# Übersicht
# ---------------------------------------------------------------------------

@router.get("/", response_class=HTMLResponse)
async def insurance_overview(
    request: Request,
    year: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
):
    benutzer = await require_user(request, db)
    if not year:
        year = date.today().year

    configuration = await _get_configuration(db, year)
    packages = await _get_packages(db, year)

    pi_result = await db.execute(
        select(ParcelInsurance)
        .options(selectinload(ParcelInsurance.property_package), selectinload(ParcelInsurance.additional_persons))
        .where(ParcelInsurance.year == year)
    )
    all_pi = pi_result.scalars().all()

    count_property = sum(1 for pi in all_pi if pi.has_property_insurance)
    count_accident = sum(1 for pi in all_pi if pi.has_accident_insurance)

    total_property = Decimal("0")
    total_accident = Decimal("0")
    for pi in all_pi:
        cost = calculate_insurance_cost(pi, configuration)
        total_property += cost["property_cost"]
        total_accident += cost["accident_cost"]

    years_result = await db.execute(
        select(InsuranceConfiguration.year).order_by(InsuranceConfiguration.year.desc())
    )
    available_years = [r[0] for r in years_result.all()]
    if year not in available_years:
        available_years.insert(0, year)

    return templates.TemplateResponse("insurance/overview.html", {
        "request": request, "benutzer": benutzer, "year": year,
        "available_years": available_years,
        "configuration": configuration, "packages": packages,
        "count_property": count_property, "count_accident": count_accident,
        "total_property": total_property, "total_accident": total_accident,
        "total_overall": total_property + total_accident,
    })


# ---------------------------------------------------------------------------
# Konfiguration: Unfallbeträge + Sachversicherungs-Pakete
# ---------------------------------------------------------------------------

@router.get("/configuration", response_class=HTMLResponse)
async def configuration_page(
    request: Request,
    year: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
):
    benutzer = await require_user(request, db)
    if not year:
        year = date.today().year

    configuration = await _get_configuration(db, year)
    packages = await _get_packages(db, year)

    all_years_result = await db.execute(
        select(InsuranceConfiguration.year).order_by(InsuranceConfiguration.year.desc())
    )
    available_years = [r[0] for r in all_years_result.all()]
    if year not in available_years:
        available_years.insert(0, year)

    return templates.TemplateResponse("insurance/configuration.html", {
        "request": request, "benutzer": benutzer, "year": year,
        "available_years": available_years,
        "configuration": configuration, "packages": packages,
        "current_year": date.today().year,
    })


@router.post("/configuration/save")
async def configuration_save(
    request: Request,
    year: int = Form(...),
    accident_base_amount_eur: str = Form(...),
    accident_additional_amount_eur: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    await require_user(request, db)

    configuration = await _get_configuration(db, year)
    base = _parse_decimal(accident_base_amount_eur) or Decimal("0")
    additional = _parse_decimal(accident_additional_amount_eur) or Decimal("0")

    if configuration:
        configuration.accident_base_amount_eur = base
        configuration.accident_additional_amount_eur = additional
    else:
        db.add(InsuranceConfiguration(
            year=year, accident_base_amount_eur=base, accident_additional_amount_eur=additional,
        ))

    await db.commit()
    return RedirectResponse(f"/insurance/configuration?year={year}", status_code=302)


@router.post("/configuration/packages/new")
async def package_create(
    request: Request,
    year: int = Form(...),
    name: str = Form(...),
    amount_eur: str = Form(...),
    sort_order: int = Form(0),
    db: AsyncSession = Depends(get_db),
):
    await require_user(request, db)

    amount = _parse_decimal(amount_eur) or Decimal("0")
    db.add(PropertyInsurancePackage(
        year=year, name=name.strip(), amount_eur=amount, sort_order=sort_order,
    ))
    await db.commit()
    return RedirectResponse(f"/insurance/configuration?year={year}", status_code=302)


@router.post("/configuration/packages/{package_id}/edit")
async def package_update(
    package_id: str,
    request: Request,
    name: str = Form(...),
    amount_eur: str = Form(...),
    sort_order: int = Form(0),
    db: AsyncSession = Depends(get_db),
):
    await require_user(request, db)

    result = await db.execute(select(PropertyInsurancePackage).where(PropertyInsurancePackage.id == package_id))
    package = result.scalar_one_or_none()
    if not package:
        raise HTTPException(status_code=404)

    package.name = name.strip()
    package.amount_eur = _parse_decimal(amount_eur) or package.amount_eur
    package.sort_order = sort_order

    await db.commit()
    return RedirectResponse(f"/insurance/configuration?year={package.year}", status_code=302)


@router.post("/configuration/packages/{package_id}/delete")
async def package_delete(
    package_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    await require_user(request, db)

    result = await db.execute(select(PropertyInsurancePackage).where(PropertyInsurancePackage.id == package_id))
    package = result.scalar_one_or_none()
    year = package.year if package else date.today().year
    if package:
        await db.delete(package)
        await db.commit()

    return RedirectResponse(f"/insurance/configuration?year={year}", status_code=302)


# ---------------------------------------------------------------------------
# Parzellen: Liste, Detail/Bearbeiten
# ---------------------------------------------------------------------------

@router.get("/parcels", response_class=HTMLResponse)
async def insurance_parcels_list(
    request: Request,
    year: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
):
    benutzer = await require_user(request, db)
    if not year:
        year = date.today().year

    configuration = await _get_configuration(db, year)

    parcels_result = await db.execute(
        select(Parcel)
        .where(Parcel.status == ParcelStatus.ACTIVE)
        .order_by(Parcel.plot_number)
    )
    parcels = parcels_result.scalars().all()

    pi_result = await db.execute(
        select(ParcelInsurance)
        .options(selectinload(ParcelInsurance.property_package), selectinload(ParcelInsurance.additional_persons))
        .where(ParcelInsurance.year == year)
    )
    pi_by_parcel = {pi.parcel_id: pi for pi in pi_result.scalars().all()}

    rows = []
    for p in parcels:
        pi = pi_by_parcel.get(p.id)
        cost = calculate_insurance_cost(pi, configuration) if pi else {
            "property_cost": Decimal("0"), "accident_cost": Decimal("0"), "total": Decimal("0")
        }
        rows.append({"parcel": p, "pi": pi, "cost": cost})

    return templates.TemplateResponse("insurance/parcels_list.html", {
        "request": request, "benutzer": benutzer, "year": year,
        "rows": rows,
    })


@router.get("/parcels/{parcel_id}", response_class=HTMLResponse)
async def insurance_detail(
    parcel_id: str,
    request: Request,
    year: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
):
    benutzer = await require_user(request, db)
    if not year:
        year = date.today().year

    parcel_result = await db.execute(
        select(Parcel)
        .options(selectinload(Parcel.member_assignments).selectinload(MemberParcel.member))
        .where(Parcel.id == parcel_id)
    )
    parcel = parcel_result.scalar_one_or_none()
    if not parcel:
        raise HTTPException(status_code=404, detail="Parcel nicht gefunden")

    configuration = await _get_configuration(db, year)
    packages = await _get_packages(db, year)
    pi = await _get_or_create_pi(db, parcel_id, year)

    grouping = household_grouping(parcel.member_assignments)
    additional_ids = {a.member_id for a in pi.additional_persons}
    cost = calculate_insurance_cost(pi, configuration)

    return templates.TemplateResponse("insurance/detail.html", {
        "request": request, "benutzer": benutzer, "year": year,
        "parcel": parcel, "pi": pi, "configuration": configuration, "packages": packages,
        "household": grouping["household"], "external": grouping["external"],
        "additional_ids": additional_ids, "cost": cost,
    })


@router.post("/parcels/{parcel_id}/save")
async def insurance_save(
    parcel_id: str,
    request: Request,
    year: int = Form(...),
    has_property_insurance: bool = Form(False),
    property_package_id: str = Form(""),
    has_accident_insurance: bool = Form(False),
    additional_persons: list[str] = Form([]),
    db: AsyncSession = Depends(get_db),
):
    await require_user(request, db)

    pi = await _get_or_create_pi(db, parcel_id, year)

    pi.has_property_insurance = has_property_insurance
    pi.property_package_id = property_package_id.strip() or None if has_property_insurance else None

    pi.has_accident_insurance = has_accident_insurance

    # Zusatzpersonen komplett neu setzen (einfacher als Diff, Datenmenge ist klein)
    for ap in list(pi.additional_persons):
        await db.delete(ap)
    await db.flush()

    if has_accident_insurance:
        for member_id in additional_persons:
            db.add(AccidentInsuranceAdditionalPerson(
                parcel_insurance_id=pi.id, member_id=member_id,
            ))

    await db.commit()
    return RedirectResponse(f"/insurance/parcels/{parcel_id}?year={year}", status_code=302)


# ---------------------------------------------------------------------------
# Auswertung
# ---------------------------------------------------------------------------

@router.get("/evaluation", response_class=HTMLResponse)
async def insurance_evaluation(
    request: Request,
    year: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
):
    benutzer = await require_user(request, db)
    if not year:
        year = date.today().year

    configuration = await _get_configuration(db, year)

    pi_result = await db.execute(
        select(ParcelInsurance)
        .options(
            selectinload(ParcelInsurance.parcel),
            selectinload(ParcelInsurance.property_package),
            selectinload(ParcelInsurance.additional_persons),
        )
        .where(
            ParcelInsurance.year == year,
            (ParcelInsurance.has_property_insurance == True) |
            (ParcelInsurance.has_accident_insurance == True)
        )
    )
    all_pi = pi_result.scalars().all()
    all_pi.sort(key=lambda pi: pi.parcel.plot_number if pi.parcel else "")

    rows = []
    total_overall = Decimal("0")
    for pi in all_pi:
        cost = calculate_insurance_cost(pi, configuration)
        total_overall += cost["total"]
        rows.append({"pi": pi, "cost": cost})

    available_years_result = await db.execute(
        select(InsuranceConfiguration.year).order_by(InsuranceConfiguration.year.desc())
    )
    available_years = [r[0] for r in available_years_result.all()]
    if year not in available_years:
        available_years.insert(0, year)

    return templates.TemplateResponse("insurance/evaluation.html", {
        "request": request, "benutzer": benutzer, "year": year,
        "available_years": available_years,
        "rows": rows, "total_overall": total_overall,
    })


@router.get("/evaluation/csv")
async def insurance_evaluation_csv(
    request: Request,
    year: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
):
    await require_user(request, db)
    if not year:
        year = date.today().year

    configuration = await _get_configuration(db, year)

    pi_result = await db.execute(
        select(ParcelInsurance)
        .options(
            selectinload(ParcelInsurance.parcel),
            selectinload(ParcelInsurance.property_package),
            selectinload(ParcelInsurance.additional_persons),
        )
        .where(
            ParcelInsurance.year == year,
            (ParcelInsurance.has_property_insurance == True) |
            (ParcelInsurance.has_accident_insurance == True)
        )
    )
    all_pi = pi_result.scalars().all()
    all_pi.sort(key=lambda pi: pi.parcel.plot_number if pi.parcel else "")

    output = io.StringIO()
    writer = csv.writer(output, delimiter=";")
    writer.writerow([
        "Parcel", "Sachversicherung", "Sach-Paket", "Sach-Kosten (EUR)",
        "Unfallversicherung", "Zusatzpersonen", "Unfall-Kosten (EUR)", "Gesamt (EUR)"
    ])

    for entry in all_pi:
        pi = entry
        cost = calculate_insurance_cost(pi, configuration)
        writer.writerow([
            pi.parcel.plot_number if pi.parcel else "",
            "Ja" if pi.has_property_insurance else "Nein",
            pi.property_package.name if pi.property_package else "",
            f"{cost['property_cost']:.2f}".replace(".", ","),
            "Ja" if pi.has_accident_insurance else "Nein",
            len(pi.additional_persons),
            f"{cost['accident_cost']:.2f}".replace(".", ","),
            f"{cost['total']:.2f}".replace(".", ","),
        ])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=insurance_{year}.csv"},
    )
