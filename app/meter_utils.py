"""
Helper functions for the metering module (water + electricity):
consumption calculation and plausibility checking. Medium-agnostic --
works identically for water meters and electricity meters.
"""
from datetime import date
from typing import Optional, List
from decimal import Decimal

from app.models import Meter, MeterReading


def sorted_readings(meter: Meter) -> List[MeterReading]:
    """A meter's readings, sorted chronologically by year."""
    return sorted(meter.readings, key=lambda z: z.year)


def reading_before_year(meter: Meter, year: int, exclude_id: Optional[str] = None) -> Decimal:
    """
    Determines the relevant prior reading for calculating a given
    year's consumption: the last reading BEFORE that year, or the
    meter's initial reading if none exists.
    """
    fruehere = [
        z for z in sorted_readings(meter)
        if z.year < year and z.id != exclude_id
    ]
    if fruehere:
        return Decimal(str(fruehere[-1].reading))
    return Decimal(str(meter.initial_reading))


def reading_after_year(meter: Meter, year: int, exclude_id: Optional[str] = None) -> Optional[Decimal]:
    """The next existing reading AFTER a year, if any (for edit-time plausibility checks)."""
    spaetere = [
        z for z in sorted_readings(meter)
        if z.year > year and z.id != exclude_id
    ]
    if spaetere:
        return Decimal(str(spaetere[0].reading))
    return None


def calculate_consumption(meter: Meter, year: int) -> Optional[Decimal]:
    """
    A meter's consumption in a given year = that year's reading minus
    the last reading before it (or the initial reading). Returns None
    if no reading exists for that year.
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
    Plausibility check: a meter's reading may not decrease over time.
    On failure, returns a tuple (translation key, formatting
    parameters); otherwise None.

    A tuple instead of a ready-formatted German string, so that both
    the REST API (whose error text stays German -- see
    format_monotonicity_error_de() below) and the (translatable) web
    UI can share the same check code -- see app.i18n.translate() for
    the web-UI side.
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
    """Formats the result of check_monotonicity() in German -- for the
    REST API, which (like the rest of the API surface) is not
    translated. See _MONOTONICITY_MESSAGES_DE above: this is a
    deliberate scope decision (API error text stays German for now),
    not an oversight -- flagged for a future i18n decision, not
    changed as part of a code-comment cleanup."""
    return _MONOTONICITY_MESSAGES_DE[key].format(**params)


def total_consumption_for_type(metering_points: List, year: int) -> Decimal:
    """
    Sums the consumption of all active meters across a list of
    MeteringPoints for a given year. MeteringPoints/meters with no
    reading for that year contribute 0 (instead of skewing the sum or
    raising an error) -- the evaluation page reports gaps separately.
    """
    gesamt = Decimal("0")
    for metering_point in metering_points:
        for meter in metering_point.meters:
            consumption = calculate_consumption(meter, year)
            if consumption is not None:
                gesamt += consumption
    return gesamt


# Rounding: how many decimal places are shown/recorded per medium?
# Water is read to one decimal place (m³), electricity as a whole number (kWh).
DECIMAL_PLACES_PER_MEDIUM = {
    "WATER": 1,
    "ELECTRICITY": 0,
}


def round_for_medium(value: Decimal, medium: str) -> Decimal:
    """Rounds a value to the number of decimal places customary for the medium."""
    stellen = DECIMAL_PLACES_PER_MEDIUM.get(medium, 1)
    quant = Decimal("1") if stellen == 0 else Decimal("1." + "0" * stellen)
    return value.quantize(quant)
