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


def household_grouping(assignments: List[MemberParcel]) -> dict:
    """
    Teilt die aktuellen Bewohner einer Parcel in "im Haushalt" (teilen
    sich eine Adresse) und "außerhalb" (abweichende oder fehlende
    Adresse) auf.

    Bewusst OHNE Anker auf eine einzelne "Hauptperson" -- das Konzept
    von Haupt-/Mitpächter wurde entfernt (siehe Migration
    0022_remove_tenant_role): das Vereinsboard behandelt alle Bewohner
    einer Parcel gleichermaßen haftbar, unabhängig davon, wer zuerst
    unterschrieben hat. Stattdessen werden die Bewohner untereinander
    nach Adresse gruppiert; die GRÖSSTE Gruppe mit übereinstimmender
    Adresse gilt als der automatisch mitversicherte Haushalt, alle
    anderen (abweichende Adresse oder Einzelperson ohne Match) als
    "außerhalb" (optional gegen Aufpreis versicherbar).

    Rückgabe: {"household": [Member, ...], "external": [Member, ...]}
    Eine leere Adresse (alle Felder leer) zählt NICHT als Übereinstimmung,
    um Falsch-Zuordnungen bei fehlenden Adressdaten zu vermeiden -- auch
    wenn mehrere Bewohner zufällig alle eine leere Adresse haben, bilden
    sie deshalb KEINE gemeinsame Haushalts-Gruppe.
    """
    current = [a.member for a in assignments if a.assigned_until is None]
    if not current:
        return {"household": [], "external": []}

    # Einzelne Bewohner-Sonderfall: bei genau einer Person gibt es
    # niemanden zum Vergleichen -- diese Person zählt trivial als
    # Haushalt, unabhängig davon, ob eine Adresse hinterlegt ist.
    if len(current) == 1:
        return {"household": current, "external": []}

    # Nach normalisierter Adresse gruppieren; leere Adressen bleiben
    # explizit ungruppiert (jede für sich).
    groups: dict = {}
    unmatched: list = []
    for m in current:
        addr = _normalized_address(m)
        if addr == ("", "", ""):
            unmatched.append(m)
            continue
        groups.setdefault(addr, []).append(m)

    # Größte Adress-Gruppe (mit mindestens einer Person) ist der
    # Haushalt. Bei Gleichstand: die zuerst gefundene (stabile
    # Query-Reihenfolge), es gibt keine fachliche Grundlage, eine
    # bestimmte Gruppe zu bevorzugen.
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
