"""
Hilfsfunktionen für das Metering-Modul (Wasser + Strom): Verbrauchsberechnung
und Plausibilitätsprüfung. Medium-agnostisch – funktioniert identisch für
Wasseruhren wie für Stromzähler.
"""
from datetime import date
from typing import Optional, List
from decimal import Decimal

from app.models import Meter, MeterReading


def sorted_readings(meter: Meter) -> List[MeterReading]:
    """Ablesungen eines Zählers, chronologisch nach Jahr sortiert."""
    return sorted(meter.readings, key=lambda z: z.year)


def reading_before_year(meter: Meter, year: int, exclude_id: Optional[str] = None) -> Decimal:
    """
    Ermittelt den relevanten Vorwert für die Verbrauchsberechnung eines
    bestimmten Jahres: die letzte Ablesung VOR diesem Jahr, oder falls
    keine existiert, den Anfangsstand des Zählers.
    """
    fruehere = [
        z for z in sorted_readings(meter)
        if z.year < year and z.id != exclude_id
    ]
    if fruehere:
        return Decimal(str(fruehere[-1].reading))
    return Decimal(str(meter.initial_reading))


def reading_after_year(meter: Meter, year: int, exclude_id: Optional[str] = None) -> Optional[Decimal]:
    """Die nächste vorhandene Ablesung NACH einem Jahr, falls vorhanden (für Editier-Plausibilität)."""
    spaetere = [
        z for z in sorted_readings(meter)
        if z.year > year and z.id != exclude_id
    ]
    if spaetere:
        return Decimal(str(spaetere[0].reading))
    return None


def calculate_consumption(meter: Meter, year: int) -> Optional[Decimal]:
    """
    Verbrauch eines Zählers in einem bestimmten Jahr = Ablesung dieses
    Jahres minus letzte Ablesung davor (oder Anfangsstand). Gibt None
    zurück, wenn für dieses Jahr keine Ablesung vorliegt.
    """
    aktuelle = next((z for z in meter.readings if z.year == year), None)
    if not aktuelle:
        return None
    vorwert = reading_before_year(meter, year, exclude_id=aktuelle.id)
    return Decimal(str(aktuelle.reading)) - vorwert


def check_monotonicity(
    meter: Meter, year: int, neuer_stand: Decimal, exclude_id: Optional[str] = None
) -> Optional[tuple]:
    """
    Plausibilitätsprüfung: der Zählerstand eines Zählers darf über die
    Zeit nicht sinken. Gibt bei einem Fehlschlag ein Tupel
    (Übersetzungsschlüssel, Formatierungsparameter) zurück, sonst None.

    Ein Tupel statt einer fertig formatierten deutschen Zeichenkette,
    damit sowohl die (weiterhin deutsche) REST-API als auch die
    (übersetzbare) Web-Oberfläche denselben Prüfcode nutzen können –
    siehe format_monotonicity_error_de() für die API-Seite und
    app.i18n.translate() für die Web-Seite.
    """
    vorwert = reading_before_year(meter, year, exclude_id=exclude_id)
    if neuer_stand < vorwert:
        return ("metering.errors.reading_below_previous", {"new_value": neuer_stand, "previous_value": vorwert})

    nachwert = reading_after_year(meter, year, exclude_id=exclude_id)
    if nachwert is not None and neuer_stand > nachwert:
        return ("metering.errors.reading_above_later", {"new_value": neuer_stand, "later_value": nachwert})

    return None


_MONOTONICITY_MESSAGES_DE = {
    "metering.errors.reading_below_previous": (
        "Der Zählerstand ({new_value}) darf nicht kleiner sein als der "
        "vorherige Stand ({previous_value}) desselben Zählers."
    ),
    "metering.errors.reading_above_later": (
        "Der Zählerstand ({new_value}) darf nicht größer sein als der "
        "bereits erfasste spätere Stand ({later_value}) desselben Zählers."
    ),
}


def format_monotonicity_error_de(key: str, params: dict) -> str:
    """Formatiert das Ergebnis von check_monotonicity() auf Deutsch – für
    die REST-API, die (wie der Rest der API-Oberfläche) nicht übersetzt wird."""
    return _MONOTONICITY_MESSAGES_DE[key].format(**params)


def total_consumption_for_type(metering_points: List, year: int) -> Decimal:
    """
    Summiert den Verbrauch aller aktiven Zähler einer Liste von
    MeteringPoints für ein bestimmtes Jahr. MeteringPoints/Zähler ohne
    Ablesung für dieses Jahr tragen 0 bei (statt die Summe zu verfälschen
    oder einen Fehler zu werfen) – die Auswertungsseite weist Lücken
    separat aus.
    """
    gesamt = Decimal("0")
    for metering_point in metering_points:
        for meter in metering_point.meters:
            consumption = calculate_consumption(meter, year)
            if consumption is not None:
                gesamt += consumption
    return gesamt


# Rundung: wie viele Nachkommastellen werden pro Medium angezeigt/erfasst?
# Wasser wird mit einer Nachkommastelle abgelesen (m³), Strom als Ganzzahl (kWh).
DECIMAL_PLACES_PER_MEDIUM = {
    "WATER": 1,
    "ELECTRICITY": 0,
}


def round_for_medium(value: Decimal, medium: str) -> Decimal:
    """Rundet einen Wert auf die für das Medium übliche Nachkommastellen-Anzahl."""
    stellen = DECIMAL_PLACES_PER_MEDIUM.get(medium, 1)
    quant = Decimal("1") if stellen == 0 else Decimal("1." + "0" * stellen)
    return value.quantize(quant)
