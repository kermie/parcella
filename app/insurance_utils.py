"""
Helper functions for the insurance module: cost calculation and
household detection (same address = automatically co-insured).
"""
from decimal import Decimal
from typing import List, Optional

from app.models import Member, MemberParcel, ParcelInsurance, InsuranceConfiguration


def _normalized_address(member: Member) -> tuple:
    """Normalized address for household comparison (whitespace/case-tolerant)."""
    return (
        (member.street or "").strip().lower(),
        (member.postal_code or "").strip().lower(),
        (member.city or "").strip().lower(),
    )


def household_grouping(assignments: List[MemberParcel]) -> dict:
    """
    Splits a parcel's current residents into "in the household" (share
    an address) and "external" (differing or missing address).

    Deliberately WITHOUT anchoring on a single "primary person" -- the
    main-tenant/co-tenant concept was removed (see migration
    0022_remove_tenant_role): the board treats every resident of a
    parcel as equally liable, regardless of who signed first. Instead,
    residents are grouped among themselves by address; the LARGEST
    group sharing an address is treated as the automatically
    co-insured household, and everyone else (differing address, or a
    single person with no match) as "external" (optionally insurable
    for an extra fee).

    Returns: {"household": [Member, ...], "external": [Member, ...]}
    An empty address (all fields empty) does NOT count as a match, to
    avoid false groupings from missing address data -- even if several
    residents happen to all have an empty address, they therefore do
    NOT form a shared household group.
    """
    current = [a.member for a in assignments if a.assigned_until is None]
    if not current:
        return {"household": [], "external": []}

    # Single-resident special case: with exactly one person there's
    # nobody to compare against -- that person trivially counts as the
    # household, regardless of whether an address is on file.
    if len(current) == 1:
        return {"household": current, "external": []}

    # Group by normalized address; empty addresses explicitly stay
    # ungrouped (each on its own).
    groups: dict = {}
    unmatched: list = []
    for m in current:
        addr = _normalized_address(m)
        if addr == ("", "", ""):
            unmatched.append(m)
            continue
        groups.setdefault(addr, []).append(m)

    # The largest address group (with at least one person) is the
    # household. In case of a tie: the first one found (stable query
    # order) -- there's no substantive basis for preferring one group
    # over another.
    if groups:
        household = max(groups.values(), key=len)
    else:
        household = []

    household_ids = {m.id for m in household}
    external = [m for m in current if m.id not in household_ids]

    return {"household": household, "external": external}


def calculate_insurance_cost(
    pi: ParcelInsurance, configuration: Optional[InsuranceConfiguration]
) -> dict:
    """
    Calculates a parcel's insurance cost for a year.
    Returns a dict with property_cost, accident_cost, total.
    """
    property_cost = Decimal("0")
    if pi.has_property_insurance and pi.property_package:
        property_cost = Decimal(str(pi.property_package.amount_eur))

    accident_cost = Decimal("0")
    if pi.has_accident_insurance and configuration:
        base = Decimal(str(configuration.accident_base_amount_eur))
        additional = Decimal(str(configuration.accident_additional_amount_eur))
        additional_count = len(pi.additional_persons)
        accident_cost = base + (additional * additional_count)

    return {
        "property_cost": property_cost,
        "accident_cost": accident_cost,
        "total": property_cost + accident_cost,
    }
