"""
Invoice generation (issue #57): computes what a DRAFT InvoiceRun's
invoices would look like, and -- once the council is happy with the
preview -- persists them with permanent invoice numbers.

Deliberately two-phase and one-way: compute_invoices_for_run() never
touches the database, so a preview can be shown as many times as
needed with zero side effects (the issue explicitly wants "a preview
first before sending"). finalize_run() is the one moment invoice
numbers get assigned and Invoice/InvoiceLineItem rows get created; it
runs the same computation once, in order, and flips the run to
FINALIZED. There is no "regenerate a draft run" -- doing that would
either waste/reuse invoice numbers, and a run's item definitions are
still fully editable right up until finalization anyway.
"""
from dataclasses import dataclass
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import (
    InvoiceRun, InvoiceRunStatus, InvoicePricingMode, Invoice, InvoiceLineItem,
    Parcel, ParcelStatus, MemberParcel, MeteringPoint, MeteringMedium, MeteringPointType, Meter,
    ParcelInsurance, InsuranceConfiguration, ClubSetting,
)
from app.insurance_utils import calculate_insurance_cost, _normalized_address
from app.meter_utils import calculate_consumption
from app.l10n import load_current_region, format_address


@dataclass
class ComputedLineItem:
    order_number: int
    name: str
    description: Optional[str]
    quantity: Decimal
    unit_price: Decimal
    line_total: Decimal


@dataclass
class ComputedInvoice:
    parcel: Parcel
    recipient_names: str
    recipient_address: str
    line_items: List[ComputedLineItem]
    subtotal: Decimal


def _group_recipient(members: list) -> Tuple[str, str, str, str]:
    """Groups invoice-address members of one parcel by shared address
    (same idea as insurance_utils.household_grouping, simplified: just
    the largest matching-address group, since there's no "external"
    bucket to keep separate here -- these are already exactly the
    people the invoice goes to). Returns (names, street, postal_code, city)."""
    if len(members) == 1:
        m = members[0]
        return m.full_name, (m.street or ""), (m.postal_code or ""), (m.city or "")

    groups: dict = {}
    for m in members:
        groups.setdefault(_normalized_address(m), []).append(m)
    best = max(groups.values(), key=len)
    m0 = best[0]
    names = "\n".join(m.full_name for m in best)
    return names, (m0.street or ""), (m0.postal_code or ""), (m0.city or "")


async def _load_metering_points_by_parcel(db: AsyncSession, medium: MeteringMedium) -> Dict[str, MeteringPoint]:
    result = await db.execute(
        select(MeteringPoint)
        .options(selectinload(MeteringPoint.meters).selectinload(Meter.readings))
        .where(MeteringPoint.medium == medium, MeteringPoint.type == MeteringPointType.PARCEL)
    )
    return {p.parcel_id: p for p in result.scalars().all() if p.parcel_id}


async def _load_parcel_insurance_by_parcel(db: AsyncSession, year: int) -> Dict[str, ParcelInsurance]:
    result = await db.execute(
        select(ParcelInsurance)
        .options(selectinload(ParcelInsurance.property_package), selectinload(ParcelInsurance.additional_persons))
        .where(ParcelInsurance.year == year)
    )
    return {pi.parcel_id: pi for pi in result.scalars().all()}


async def compute_invoices_for_run(db: AsyncSession, run: InvoiceRun) -> List[ComputedInvoice]:
    region = await load_current_region(db)

    parcels_result = await db.execute(
        select(Parcel)
        .options(selectinload(Parcel.member_assignments).selectinload(MemberParcel.member))
        .where(Parcel.status == ParcelStatus.ACTIVE)
        .order_by(Parcel.plot_number)
    )
    all_parcels = list(parcels_result.scalars().all())

    water_points = await _load_metering_points_by_parcel(db, MeteringMedium.WATER)
    electricity_points = await _load_metering_points_by_parcel(db, MeteringMedium.ELECTRICITY)
    parcel_insurance = await _load_parcel_insurance_by_parcel(db, run.year)

    insurance_config_result = await db.execute(
        select(InsuranceConfiguration).where(InsuranceConfiguration.year == run.year)
    )
    insurance_configuration = insurance_config_result.scalar_one_or_none()

    def item_quantity_and_price(definition, parcel, residents_count):
        mode = definition.pricing_mode
        if mode == InvoicePricingMode.FIXED_PER_PARCEL:
            if definition.unit_price is None:
                return None, None
            return Decimal("1"), Decimal(str(definition.unit_price))
        if mode == InvoicePricingMode.FIXED_PER_PERSON:
            if definition.unit_price is None or residents_count == 0:
                return None, None
            return Decimal(residents_count), Decimal(str(definition.unit_price))
        if mode == InvoicePricingMode.PER_SQM:
            if definition.unit_price is None or parcel.area_sqm is None:
                return None, None
            return Decimal(str(parcel.area_sqm)), Decimal(str(definition.unit_price))
        if mode in (InvoicePricingMode.WATER_USAGE, InvoicePricingMode.ELECTRICITY_USAGE):
            if definition.unit_price is None:
                return None, None
            points = water_points if mode == InvoicePricingMode.WATER_USAGE else electricity_points
            point = points.get(parcel.id)
            meter = point.current_meter if point else None
            consumption = calculate_consumption(meter, run.year) if meter else None
            if consumption is None:
                return None, None
            return consumption, Decimal(str(definition.unit_price))
        if mode == InvoicePricingMode.INSURANCE_COST:
            pi = parcel_insurance.get(parcel.id)
            if pi is None:
                return None, None
            cost = calculate_insurance_cost(pi, insurance_configuration)
            if cost["total"] <= 0:
                return None, None
            return Decimal("1"), cost["total"]
        return None, None

    computed: List[ComputedInvoice] = []
    for parcel in all_parcels:
        applicable_defs = [
            d for d in run.item_definitions
            if d.applies_to_all_parcels or any(s.parcel_id == parcel.id for s in d.parcel_scopes)
        ]
        if not applicable_defs:
            continue

        current_residents = [a for a in parcel.member_assignments if a.assigned_until is None]
        invoice_address_members = [a.member for a in current_residents if a.is_invoice_address]
        if not invoice_address_members:
            continue

        names, street, postal_code, city = _group_recipient(invoice_address_members)
        recipient_address = format_address(street, postal_code, city, region)

        line_items = []
        for definition in sorted(applicable_defs, key=lambda d: d.order_number):
            quantity, unit_price = item_quantity_and_price(definition, parcel, len(current_residents))
            if quantity is None or unit_price is None:
                continue
            line_total = (Decimal(quantity) * Decimal(unit_price)).quantize(Decimal("0.01"))
            line_items.append(ComputedLineItem(
                order_number=definition.order_number, name=definition.name, description=definition.description,
                quantity=Decimal(quantity), unit_price=Decimal(unit_price), line_total=line_total,
            ))

        if not line_items:
            continue

        subtotal = sum((li.line_total for li in line_items), Decimal("0"))
        computed.append(ComputedInvoice(
            parcel=parcel, recipient_names=names, recipient_address=recipient_address,
            line_items=line_items, subtotal=subtotal,
        ))

    return computed


async def _first_invoice_sequence(db: AsyncSession, year: int) -> int:
    result = await db.execute(select(Invoice.invoice_number).where(Invoice.invoice_number.like(f"{year}/%")))
    existing = [row[0] for row in result.all()]
    if existing:
        return max(int(n.split("/", 1)[1]) for n in existing) + 1

    start_result = await db.execute(select(ClubSetting).where(ClubSetting.key == "invoice_number_start"))
    entry = start_result.scalar_one_or_none()
    try:
        return int(entry.value) if entry and entry.value else 1
    except ValueError:
        return 1


async def finalize_run(db: AsyncSession, run: InvoiceRun) -> List[Invoice]:
    """Computes and PERSISTS every invoice for `run`, assigning
    permanent invoice numbers in order, then marks the run FINALIZED.
    Caller commits."""
    computed = await compute_invoices_for_run(db, run)

    invoices = []
    next_seq = await _first_invoice_sequence(db, run.year)
    for c in computed:
        invoice = Invoice(
            invoice_run_id=run.id, parcel_id=c.parcel.id, invoice_number=f"{run.year}/{next_seq}",
            recipient_names=c.recipient_names, recipient_address=c.recipient_address,
            subtotal=c.subtotal,
        )
        db.add(invoice)
        await db.flush()
        for li in c.line_items:
            db.add(InvoiceLineItem(
                invoice_id=invoice.id, order_number=li.order_number, name=li.name, description=li.description,
                quantity=li.quantity, unit_price=li.unit_price, line_total=li.line_total,
            ))
        invoices.append(invoice)
        next_seq += 1

    run.status = InvoiceRunStatus.FINALIZED
    return invoices
