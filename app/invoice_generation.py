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

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import (
    InvoiceRun, InvoiceRunStatus, InvoicePricingMode, Invoice, InvoiceLineItem,
    Parcel, ParcelStatus, MemberParcel, Member, MeteringPoint, MeteringMedium, MeteringPointType, Meter,
    ParcelInsurance, InsuranceConfiguration, ClubSetting, WorkHoursMode,
)
from app.database import active_member_filter
from app.insurance_utils import calculate_insurance_cost, _normalized_address
from app.meter_utils import calculate_consumption
from app.l10n import load_current_region, format_address
from app.area_utils import compute_area_b_sqm
from app.work_hours_evaluation import compute_work_hours_shortfalls

# Issue #65: club-configurable invoice number format/starting sequence.
# Freely typed (e.g. "R-{year}-{number}"), not a fixed list -- {year}
# and {number} are the only placeholders, and DEFAULT_INVOICE_NUMBER_FORMAT
# matches every invoice number ever produced before this setting
# existed, so an install that never touches the setting sees no change.
DEFAULT_INVOICE_NUMBER_FORMAT = "{year}/{number}"
INVOICE_NUMBER_FORMAT_MAX_LENGTH = 30
INVOICE_NUMBER_FORMAT_EXAMPLES = [
    "{year}/{number}", "{year}-{number}", "{number}/{year}", "R-{year}-{number}",
]


def is_valid_invoice_number_format(format_str: str) -> bool:
    """A safe, well-formed format: {number} is required (guarantees
    every generated number is unique), {year} is optional, any other
    literal text is fine -- but no other placeholder, no format spec
    (e.g. "{number:03d}"), and no stray/unmatched braces, since this
    string is fed straight into str.format(year=..., number=...) at
    finalize time and a malformed one would crash that (see
    finalize_run). Removing the two literal placeholder substrings and
    checking no "{"/"}" remains catches all of that in one step."""
    if not format_str or not format_str.strip():
        return False
    if len(format_str) > INVOICE_NUMBER_FORMAT_MAX_LENGTH:
        return False
    if "{number}" not in format_str:
        return False
    remainder = format_str.replace("{year}", "").replace("{number}", "")
    return "{" not in remainder and "}" not in remainder


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
    """Either `parcel` or `member` is set, never both/neither -- a
    parcel invoice (every plot-scoped pricing mode) or a member
    invoice (fixed_per_person items, billed to targeted members
    directly regardless of parcel status; see
    _compute_member_invoices)."""
    recipient_names: str
    recipient_address: str
    line_items: List[ComputedLineItem]
    subtotal: Decimal
    parcel: Optional[Parcel] = None
    member: Optional[Member] = None


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


async def _compute_member_invoices(
    db: AsyncSession, run: InvoiceRun, region: str,
    work_hours_mode: Optional[WorkHoursMode] = None, work_hours_by_member: Optional[Dict[str, float]] = None,
) -> List[ComputedInvoice]:
    """fixed_per_person items are person-scoped, not parcel-scoped --
    excluded from the parcel loop entirely (see compute_invoices_for_run)
    and handled solely here, via each definition's own
    applies_to_all_members/member_scopes, exactly mirroring how the
    parcel loop uses applies_to_all_parcels/parcel_scopes. Billed to
    targeted members directly regardless of current parcel status.
    Quantity is always 1 -- a flat fee per person, not a per-resident
    count. A member targeted by multiple fixed_per_person definitions
    gets ONE invoice with multiple lines, not one invoice per
    definition (mirrors how a parcel's multiple applicable items merge
    onto its one invoice).

    work_hours_shortfall items (issue #83) also land here when that
    year's work-hours mode is PER_MEMBER -- but unlike fixed_per_person,
    they ignore applies_to_all_members/member_scopes entirely (a member
    explicitly asked for no manual scoping) and bill exactly whoever
    app/work_hours_evaluation.py computed a nonzero amount for, at that
    computed amount rather than definition.unit_price."""
    work_hours_by_member = work_hours_by_member or {}
    applicable_defs = [
        d for d in run.item_definitions
        if d.pricing_mode == InvoicePricingMode.FIXED_PER_PERSON
        or (d.pricing_mode == InvoicePricingMode.WORK_HOURS_SHORTFALL and work_hours_mode == WorkHoursMode.PER_MEMBER)
    ]
    if not applicable_defs:
        return []

    members_result = await db.execute(
        select(Member).where(active_member_filter()).order_by(Member.last_name, Member.first_name)
    )
    all_members = list(members_result.scalars().all())

    # Members owing a work-hours shortfall might not all satisfy
    # active_member_filter() (app/work_hours_evaluation.py's own
    # PER_MEMBER criteria is "not soft-deleted and has a parcel", not
    # the same predicate) -- fetch them directly so a real shortfall
    # never silently disappears due to a filter mismatch.
    work_hours_members_by_id: Dict[str, Member] = {}
    if work_hours_by_member:
        wh_result = await db.execute(select(Member).where(Member.id.in_(list(work_hours_by_member.keys()))))
        work_hours_members_by_id = {m.id: m for m in wh_result.scalars().all()}

    lines_by_member: Dict[str, List[ComputedLineItem]] = {}
    members_by_id: Dict[str, Member] = {}
    for definition in sorted(applicable_defs, key=lambda d: d.order_number):
        if definition.pricing_mode == InvoicePricingMode.WORK_HOURS_SHORTFALL:
            targets_with_price = [
                (work_hours_members_by_id[mid], Decimal(str(amount)))
                for mid, amount in work_hours_by_member.items()
                if mid in work_hours_members_by_id
            ]
        else:
            if definition.unit_price is None:
                continue
            if definition.applies_to_all_members:
                targets = all_members
            else:
                scoped_ids = {s.member_id for s in definition.member_scopes}
                targets = [m for m in all_members if m.id in scoped_ids]
            unit_price = Decimal(str(definition.unit_price))
            targets_with_price = [(m, unit_price) for m in targets]

        if not targets_with_price:
            continue

        for member, price in targets_with_price:
            members_by_id[member.id] = member
            lines_by_member.setdefault(member.id, []).append(ComputedLineItem(
                order_number=definition.order_number, name=definition.name, description=definition.description,
                quantity=Decimal("1"), unit_price=price, line_total=price,
            ))

    computed: List[ComputedInvoice] = []
    for member_id, line_items in lines_by_member.items():
        member = members_by_id[member_id]
        subtotal = sum((li.line_total for li in line_items), Decimal("0"))
        recipient_address = format_address(member.street, member.postal_code, member.city, region)
        computed.append(ComputedInvoice(
            member=member, recipient_names=member.full_name, recipient_address=recipient_address,
            line_items=line_items, subtotal=subtotal,
        ))

    computed.sort(key=lambda c: (c.member.last_name, c.member.first_name))
    return computed


def _parcel_is_billable(parcel: Parcel) -> bool:
    """Whether `parcel` currently has at least one invoice-address
    resident -- the same check compute_invoices_for_run's parcel loop
    uses to decide whether a parcel gets an invoice at all. Shared with
    the communal-area-share denominator below (issue #82), so "how many
    parcels split Area B" always matches "how many parcels actually get
    billed"."""
    current_residents = [a for a in parcel.member_assignments if a.assigned_until is None]
    return any(a.is_invoice_address for a in current_residents)


def _parcel_in_scope(definition, parcel: Parcel) -> bool:
    return definition.applies_to_all_parcels or any(s.parcel_id == parcel.id for s in definition.parcel_scopes)


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

    # "Share of the lease for the communal area" (issue #82): Area B is
    # split evenly across however many parcels actually get billed for
    # each such item definition, so the sum of every tenant's share
    # reconstructs the whole communal area -- the club only enters the
    # price per sqm by hand. The denominator is per-definition (not
    # global) since two communal-share items could theoretically be
    # scoped to different subsets of parcels.
    area_b_sqm = await compute_area_b_sqm(db)
    communal_share_denominators: Dict[str, int] = {
        d.id: sum(1 for p in all_parcels if _parcel_in_scope(d, p) and _parcel_is_billable(p))
        for d in run.item_definitions
        if d.pricing_mode == InvoicePricingMode.COMMUNAL_AREA_SHARE
    }

    # "Charge those who not completely or never used their work
    # sessions, according to /work-hours/evaluation" (issue #83) --
    # fully computed and automatically excludes exempt/fulfilled
    # parcels or members (see app/work_hours_evaluation.py), so it's
    # only computed at all when a definition actually uses it (the
    # per-member evaluation involves an hours+exemption query per
    # member, unlike the other precomputed structures above).
    work_hours_mode: Optional[WorkHoursMode] = None
    work_hours_by_parcel: Dict[str, float] = {}
    work_hours_by_member: Dict[str, float] = {}
    if any(d.pricing_mode == InvoicePricingMode.WORK_HOURS_SHORTFALL for d in run.item_definitions):
        work_hours_mode, work_hours_by_parcel, work_hours_by_member = await compute_work_hours_shortfalls(db, run.year)

    def _applies_to_parcel_loop(definition) -> bool:
        mode = definition.pricing_mode
        if mode == InvoicePricingMode.FIXED_PER_PERSON:
            return False
        if mode == InvoicePricingMode.WORK_HOURS_SHORTFALL:
            return work_hours_mode == WorkHoursMode.PER_PARCEL
        return True

    def item_quantity_and_price(definition, parcel):
        mode = definition.pricing_mode
        if mode == InvoicePricingMode.FIXED_PER_PARCEL:
            if definition.unit_price is None:
                return None, None
            return Decimal("1"), Decimal(str(definition.unit_price))
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
        if mode == InvoicePricingMode.COMMUNAL_AREA_SHARE:
            if definition.unit_price is None:
                return None, None
            denom = communal_share_denominators.get(definition.id, 0)
            if denom == 0 or area_b_sqm is None or area_b_sqm <= 0:
                return None, None
            # The division rarely comes out even (e.g. 8000/3 sqm), and
            # an un-rounded Decimal division keeps expanding to the
            # context's full precision (issue #89: a real invoice
            # showed "36.74796747967479674796747967"). Cut off to one
            # decimal place here, at the source, so preview and PDF
            # rendering never see the raw repeating fraction.
            share = Decimal(str(area_b_sqm)) / Decimal(denom)
            return share.quantize(Decimal("0.1")), Decimal(str(definition.unit_price))
        if mode == InvoicePricingMode.WORK_HOURS_SHORTFALL:
            amount = work_hours_by_parcel.get(parcel.id)
            if amount is None:
                return None, None
            return Decimal("1"), Decimal(str(amount))
        return None, None

    computed: List[ComputedInvoice] = []
    for parcel in all_parcels:
        applicable_defs = [
            d for d in run.item_definitions
            if _applies_to_parcel_loop(d)
            and (d.pricing_mode == InvoicePricingMode.WORK_HOURS_SHORTFALL or _parcel_in_scope(d, parcel))
        ]
        if not applicable_defs:
            continue

        if not _parcel_is_billable(parcel):
            continue
        invoice_address_members = [
            a.member for a in parcel.member_assignments if a.assigned_until is None and a.is_invoice_address
        ]

        names, street, postal_code, city = _group_recipient(invoice_address_members)
        recipient_address = format_address(street, postal_code, city, region)

        line_items = []
        for definition in sorted(applicable_defs, key=lambda d: d.order_number):
            quantity, unit_price = item_quantity_and_price(definition, parcel)
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

    computed.extend(await _compute_member_invoices(db, run, region, work_hours_mode, work_hours_by_member))
    return computed


class SequenceCollisionError(Exception):
    """Raised when the ClubSetting "invoice_number_start" override
    would collide with sequence numbers already assigned in the same year."""


async def _first_invoice_sequence(db: AsyncSession, run: InvoiceRun, invoice_count: int) -> int:
    """The sequence number to start `run`'s numbering at.

    ClubSetting "invoice_number_start" (issue #65, reworked per a later
    request) is a one-shot override: whenever it holds a number, that
    number always wins for the very next run finalized -- regardless
    of year or of what's already been invoiced -- and is checked for
    collisions against every sequence number already used in that
    run's year before being accepted. Once successfully used it's
    cleared back to blank here (the caller commits), so it doesn't
    keep forcing every future run back to the same starting point --
    "start at 20000" should mean *this* run starts there and everything
    after just continues sequentially, not that every run forever
    starts at 20000.

    With no override set: one past whatever's already been assigned
    this year (across every run, so a second run in the same year
    continues rather than collides), or 1 if this is the year's first
    invoice ever.
    """
    existing_result = await db.execute(
        select(Invoice.sequence_number)
        .join(InvoiceRun, Invoice.invoice_run_id == InvoiceRun.id)
        .where(InvoiceRun.year == run.year)
    )
    existing_sequences = {row[0] for row in existing_result.all()}

    setting_result = await db.execute(select(ClubSetting).where(ClubSetting.key == "invoice_number_start"))
    entry = setting_result.scalar_one_or_none()
    override_value = None
    if entry and entry.value and entry.value.strip().isdigit():
        override_value = int(entry.value.strip())

    if override_value is not None:
        wanted_range = set(range(override_value, override_value + invoice_count))
        collisions = sorted(wanted_range & existing_sequences)
        if collisions:
            raise SequenceCollisionError(
                f"Starting at {override_value} would collide with already-used sequence number(s) "
                f"{collisions[0]}"
                + (f"..{collisions[-1]}" if len(collisions) > 1 else "")
                + f" in {run.year}."
            )
        entry.value = ""
        return override_value

    if existing_sequences:
        return max(existing_sequences) + 1
    return 1


async def _invoice_number_format(db: AsyncSession) -> str:
    result = await db.execute(select(ClubSetting).where(ClubSetting.key == "invoice_number_format"))
    entry = result.scalar_one_or_none()
    if entry and entry.value and is_valid_invoice_number_format(entry.value):
        return entry.value
    return DEFAULT_INVOICE_NUMBER_FORMAT


async def finalize_run(db: AsyncSession, run: InvoiceRun) -> List[Invoice]:
    """Computes and PERSISTS every invoice for `run`, assigning
    permanent invoice numbers in order, then marks the run FINALIZED.
    Raises SequenceCollisionError (before creating anything) if the
    "invoice_number_start" override would collide with existing
    numbers. Caller commits."""
    computed = await compute_invoices_for_run(db, run)

    number_format = await _invoice_number_format(db)
    invoices = []
    next_seq = await _first_invoice_sequence(db, run, len(computed))
    for c in computed:
        invoice_number = number_format.format(year=run.year, number=next_seq)
        invoice = Invoice(
            invoice_run_id=run.id,
            parcel_id=c.parcel.id if c.parcel else None,
            member_id=c.member.id if c.member else None,
            invoice_number=invoice_number, sequence_number=next_seq,
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
