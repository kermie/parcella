"""
API-Router: Insurance – Sachversicherungs-Pakete (property insurance
packages), Konfiguration, Parcel-Versicherungsstatus, Auswertung.
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models import (
    PropertyInsurancePackage, InsuranceConfiguration, ParcelInsurance,
    AccidentInsuranceAdditionalPerson, Parcel, Benutzer,
)
from app.api_auth import get_current_api_user, require_schreibzugriff
from app.module_flags import require_modul
from app.insurance_utils import calculate_insurance_cost
from app.schemas import (
    PropertyInsurancePackageOut, PropertyInsurancePackageCreate,
    InsuranceConfigurationOut, InsuranceConfigurationCreate,
    ParcelInsuranceOut, ParcelInsuranceUpdate, ParcelInsuranceCostOut,
)

router = APIRouter(
    prefix="/api/v1/insurance",
    tags=["API: Insurance"],
    dependencies=[Depends(require_modul("insurance"))],
)


# ---------------------------------------------------------------------------
# Sachversicherungs-Pakete (property insurance packages)
# ---------------------------------------------------------------------------

@router.get("/packages", response_model=List[PropertyInsurancePackageOut], summary="Pakete auflisten")
async def packages_list(
    year: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db),
    benutzer: Benutzer = Depends(get_current_api_user),
):
    query = select(PropertyInsurancePackage).order_by(PropertyInsurancePackage.year.desc(), PropertyInsurancePackage.sort_order)
    if year:
        query = query.where(PropertyInsurancePackage.year == year)
    result = await db.execute(query)
    return result.scalars().all()


@router.post(
    "/packages", response_model=PropertyInsurancePackageOut, status_code=status.HTTP_201_CREATED,
    summary="Paket anlegen",
)
async def package_create(
    daten: PropertyInsurancePackageCreate,
    db: AsyncSession = Depends(get_db),
    benutzer: Benutzer = Depends(require_schreibzugriff),
):
    package = PropertyInsurancePackage(**daten.model_dump())
    db.add(package)
    await db.commit()
    await db.refresh(package)
    return package


@router.put("/packages/{package_id}", response_model=PropertyInsurancePackageOut, summary="Paket aktualisieren")
async def package_update(
    package_id: str,
    daten: PropertyInsurancePackageCreate,
    db: AsyncSession = Depends(get_db),
    benutzer: Benutzer = Depends(require_schreibzugriff),
):
    result = await db.execute(select(PropertyInsurancePackage).where(PropertyInsurancePackage.id == package_id))
    package = result.scalar_one_or_none()
    if not package:
        raise HTTPException(status_code=404, detail="Paket nicht gefunden")

    for feld, wert in daten.model_dump().items():
        setattr(package, feld, wert)

    await db.commit()
    await db.refresh(package)
    return package


@router.delete("/packages/{package_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Paket löschen")
async def package_delete(
    package_id: str,
    db: AsyncSession = Depends(get_db),
    benutzer: Benutzer = Depends(require_schreibzugriff),
):
    result = await db.execute(select(PropertyInsurancePackage).where(PropertyInsurancePackage.id == package_id))
    package = result.scalar_one_or_none()
    if package:
        await db.delete(package)
        await db.commit()


# ---------------------------------------------------------------------------
# Konfiguration (Unfallversicherungs-Beträge / accident insurance amounts)
# ---------------------------------------------------------------------------

@router.get(
    "/configuration/{year}", response_model=InsuranceConfigurationOut,
    summary="Konfiguration für ein Jahr abrufen",
)
async def configuration_get(
    year: int,
    db: AsyncSession = Depends(get_db),
    benutzer: Benutzer = Depends(get_current_api_user),
):
    result = await db.execute(select(InsuranceConfiguration).where(InsuranceConfiguration.year == year))
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail=f"Keine Konfiguration für {year}")
    return config


@router.put(
    "/configuration/{year}", response_model=InsuranceConfigurationOut,
    summary="Konfiguration setzen (Upsert)",
)
async def configuration_set(
    year: int,
    daten: InsuranceConfigurationCreate,
    db: AsyncSession = Depends(get_db),
    benutzer: Benutzer = Depends(require_schreibzugriff),
):
    result = await db.execute(select(InsuranceConfiguration).where(InsuranceConfiguration.year == year))
    config = result.scalar_one_or_none()

    if config:
        config.accident_base_amount_eur = daten.accident_base_amount_eur
        config.accident_additional_amount_eur = daten.accident_additional_amount_eur
    else:
        config = InsuranceConfiguration(
            year=year, accident_base_amount_eur=daten.accident_base_amount_eur,
            accident_additional_amount_eur=daten.accident_additional_amount_eur,
        )
        db.add(config)

    await db.commit()
    await db.refresh(config)
    return config


# ---------------------------------------------------------------------------
# Parcel-Versicherungsstatus
# ---------------------------------------------------------------------------

async def _load_pi(db: AsyncSession, parcel_id: str, year: int) -> Optional[ParcelInsurance]:
    result = await db.execute(
        select(ParcelInsurance)
        .options(
            selectinload(ParcelInsurance.property_package),
            selectinload(ParcelInsurance.additional_persons),
        )
        .where(ParcelInsurance.parcel_id == parcel_id, ParcelInsurance.year == year)
    )
    return result.scalar_one_or_none()


def _to_cost_schema(pi: ParcelInsurance, config: Optional[InsuranceConfiguration]) -> ParcelInsuranceCostOut:
    cost = calculate_insurance_cost(pi, config)
    # Erst das Basis-Schema (nur echte ORM-Spalten) validieren, dann die
    # berechneten Felder ergänzen – model_validate(pi) direkt auf das
    # Zielschema würde fehlschlagen, da property_cost_eur/accident_cost_eur/
    # total_cost_eur keine echten Attribute auf pi sind, sondern erst
    # berechnet werden müssen.
    base = ParcelInsuranceOut.model_validate(pi)
    return ParcelInsuranceCostOut(
        **base.model_dump(),
        additional_person_member_ids=[a.member_id for a in pi.additional_persons],
        property_cost_eur=cost["property_cost"],
        accident_cost_eur=cost["accident_cost"],
        total_cost_eur=cost["total"],
    )


@router.get(
    "/parcels/{parcel_id}/{year}", response_model=ParcelInsuranceCostOut,
    summary="Versicherungsstatus einer Parcel abrufen",
    description="Gibt 404 zurück, wenn für diese Parcel/Jahr noch kein Status existiert "
                "(anders als die Web-UI wird er über die API nicht automatisch angelegt).",
)
async def insurance_get(
    parcel_id: str,
    year: int,
    db: AsyncSession = Depends(get_db),
    benutzer: Benutzer = Depends(get_current_api_user),
):
    pi = await _load_pi(db, parcel_id, year)
    if not pi:
        raise HTTPException(status_code=404, detail="Kein Versicherungsstatus für diese Parcel/Jahr")

    config_result = await db.execute(select(InsuranceConfiguration).where(InsuranceConfiguration.year == year))
    config = config_result.scalar_one_or_none()
    return _to_cost_schema(pi, config)


@router.put(
    "/parcels/{parcel_id}/{year}", response_model=ParcelInsuranceCostOut,
    summary="Versicherungsstatus setzen (Upsert)",
    description="Legt den Status an, falls er nicht existiert, und ersetzt die Liste der Zusatzpersonen komplett.",
)
async def insurance_set(
    parcel_id: str,
    year: int,
    daten: ParcelInsuranceUpdate,
    db: AsyncSession = Depends(get_db),
    benutzer: Benutzer = Depends(require_schreibzugriff),
):
    parcel_result = await db.execute(select(Parcel).where(Parcel.id == parcel_id))
    if not parcel_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Parcel nicht gefunden")

    pi = await _load_pi(db, parcel_id, year)
    if not pi:
        pi = ParcelInsurance(parcel_id=parcel_id, year=year)
        db.add(pi)
        await db.commit()
        # Frisch angelegte Zeile mit eager-geladenen Beziehungen neu laden –
        # sonst löst der Zugriff auf pi.additional_persons weiter unten
        # einen synchronen Lazy-Load aus, der mit dem asynchronen
        # Datenbanktreiber zu "MissingGreenlet" führt (siehe
        # docs/module-tickets.md für das gleiche Muster im Ticketsystem).
        pi = await _load_pi(db, parcel_id, year)

    pi.has_property_insurance = daten.has_property_insurance
    pi.property_package_id = daten.property_package_id if daten.has_property_insurance else None
    pi.has_accident_insurance = daten.has_accident_insurance

    for ap in list(pi.additional_persons):
        await db.delete(ap)
    await db.flush()

    if daten.has_accident_insurance:
        for member_id in daten.additional_person_member_ids:
            db.add(AccidentInsuranceAdditionalPerson(parcel_insurance_id=pi.id, member_id=member_id))

    await db.commit()

    # Wichtig: pi.property_package wurde ggf. schon VOR dem Setzen von
    # property_package_id geladen (z.B. beim Neuanlegen weiter oben, als der
    # Wert noch None war). Ein erneutes Abfragen über _load_pi würde wegen
    # SQLAlchemys Identity Map dasselbe (bereits als "geladen" markierte,
    # aber inzwischen veraltete) Objekt zurückgeben, OHNE die Beziehung neu
    # zu holen – da expire_on_commit=False gesetzt ist. db.refresh()
    # erzwingt das gezielte Neuladen genau dieser Beziehungen.
    await db.refresh(pi, attribute_names=["property_package", "additional_persons"])

    config_result = await db.execute(select(InsuranceConfiguration).where(InsuranceConfiguration.year == year))
    config = config_result.scalar_one_or_none()
    return _to_cost_schema(pi, config)


# ---------------------------------------------------------------------------
# Auswertung
# ---------------------------------------------------------------------------

@router.get(
    "/evaluation/{year}", response_model=List[ParcelInsuranceCostOut],
    summary="Jahresauswertung: alle versicherten Parzellen mit Kosten",
)
async def evaluation(
    year: int,
    db: AsyncSession = Depends(get_db),
    benutzer: Benutzer = Depends(get_current_api_user),
):
    config_result = await db.execute(select(InsuranceConfiguration).where(InsuranceConfiguration.year == year))
    config = config_result.scalar_one_or_none()

    result = await db.execute(
        select(ParcelInsurance)
        .options(selectinload(ParcelInsurance.property_package), selectinload(ParcelInsurance.additional_persons))
        .where(
            ParcelInsurance.year == year,
            (ParcelInsurance.has_property_insurance == True) | (ParcelInsurance.has_accident_insurance == True)
        )
    )
    return [_to_cost_schema(pi, config) for pi in result.scalars().all()]
