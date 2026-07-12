"""
Hilfsfunktionen für das Versicherungsmodul (insurance): Kostenberechnung
und Haushalts-Erkennung (gleiche Adresse = automatisch mitversichert).
"""
from decimal import Decimal
from typing import List, Optional

from app.models import Member, MemberParcel, ParcelInsurance, InsuranceConfiguration


def _normalized_address(member: Member) -> tuple:
    """Normalisierte Adresse für den Haushaltsvergleich (whitespace/case-tolerant)."""
    return (
        (member.street or "").strip().lower(),
        (member.postal_code or "").strip().lower(),
        (member.city or "").strip().lower(),
    )


def primary_tenant_of(assignments: List[MemberParcel]) -> Optional[Member]:
    """Ermittelt den Hauptpächter einer Parcel (oder den ersten Pächter als Fallback)."""
    current = [a for a in assignments if a.assigned_until is None]
    if not current:
        return None
    primary = next((a for a in current if a.is_primary_tenant), current[0])
    return primary.member


def household_grouping(assignments: List[MemberParcel]) -> dict:
    """
    Teilt die aktuellen Pächter einer Parcel in "im Haushalt des
    Hauptpächters" (gleiche Adresse) und "außerhalb" auf.

    Rückgabe: {"household": [Member, ...], "external": [Member, ...]}
    Eine leere Adresse (alle Felder leer) zählt NICHT als Übereinstimmung,
    um Falsch-Zuordnungen bei fehlenden Adressdaten zu vermeiden.
    """
    current = [a for a in assignments if a.assigned_until is None]
    primary_tenant = primary_tenant_of(assignments)

    if not primary_tenant:
        return {"household": [], "external": []}

    primary_address = _normalized_address(primary_tenant)
    is_empty = primary_address == ("", "", "")

    household = []
    external = []

    for a in current:
        m = a.member
        if m.id == primary_tenant.id:
            household.append(m)
            continue
        if not is_empty and _normalized_address(m) == primary_address:
            household.append(m)
        else:
            external.append(m)

    return {"household": household, "external": external}


def calculate_insurance_cost(
    pi: ParcelInsurance, configuration: Optional[InsuranceConfiguration]
) -> dict:
    """
    Berechnet die Versicherungskosten einer Parcel für ein Jahr.
    Gibt ein Dict mit property_cost, accident_cost, total zurück.
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
