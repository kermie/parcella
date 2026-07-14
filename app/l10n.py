"""
Lokalisierung (l10n): Zahlen-, Währungs- und Adressformate pro Verein.

Architekturentscheidung: bewusst GETRENNT von app/i18n.py (Sprache der
Oberfläche). Sprache und regionale Formatierungskonventionen sind zwei
unabhängige Dinge -- z.B. sagt "Englisch als Oberflächensprache" nichts
darüber aus, ob Zahlen als "1,234.50" oder "1.234,50" dargestellt werden
sollen, oder ob mit £, € oder zł gerechnet wird. Ein Verein wählt daher
zusätzlich zur Sprache (ClubSetting "language") eine Region (ClubSetting
"region", z.B. "de_DE") und eine Währung (ClubSetting "currency", z.B.
"EUR") -- beide unabhängig voneinander und unabhängig von der Sprache.

Zahlen- und Währungsformatierung nutzt die Bibliothek Babel, statt
eigene Regex-/String-Hacks zu bauen (z.B. das vorherige
".replace(',', '.')" im Dashboard-Template war genau so ein Hack, der
nur für Deutsch stimmte). Babel kennt pro Locale die korrekten
Tausender-/Dezimaltrennzeichen UND die korrekte Position des
Währungssymbols (z.B. "€ 1.234,50" in den Niederlanden vs.
"1.234,50 €" in Deutschland vs. "£1,234.50" in Großbritannien).

Adressformat ist komplexer als Zahlen (unterschiedliche Feld-Reihenfolge,
nicht nur unterschiedliche Trennzeichen) und wird deshalb bewusst NICHT
über eine externe Bibliothek gelöst (z.B. Google libaddressinput wäre
für den Umfang dieses Projekts überdimensioniert), sondern über eine
einfache Vorlage pro Region (siehe ADDRESS_FORMATS unten). Für die
aktuell unterstützten 7 Länder reicht eine einzige Unterscheidung:
Postleitzahl vor dem Ort (kontinentaleuropäisch) vs. danach (UK).
"""
import logging
from typing import Optional

from fastapi import Request
from jinja2 import pass_context
from babel.numbers import format_currency, format_decimal, get_currency_symbol
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models import ClubSetting

logger = logging.getLogger(__name__)

DEFAULT_REGION = "de_DE"
DEFAULT_CURRENCY = "EUR"

# Region-Auswahl fuer /admin/settings. Namen bewusst als Landesname auf
# Englisch gehalten (wie bei den Waehrungscodes), um nicht zusaetzlich
# 7 weitere Uebersetzungsschluessel je Sprache zu benoetigen -- Laender-
# und Waehrungscodes sind allgemein verstaendlich genug.
AVAILABLE_REGIONS = {
    "de_DE": "Germany",
    "en_GB": "United Kingdom",
    "fr_FR": "France",
    "nl_NL": "Netherlands",
    "pl_PL": "Poland",
    "cs_CZ": "Czech Republic",
    "sk_SK": "Slovakia",
}

# Waehrungsauswahl. Bewusst nicht 1:1 an AVAILABLE_REGIONS gekoppelt --
# z.B. koennte ein franzoesischsprachiger Verein in der Schweiz CHF
# nutzen. Beide Einstellungen bleiben unabhaengig waehlbar.
AVAILABLE_CURRENCIES = {
    "EUR": "Euro (\u20ac)",
    "GBP": "British Pound (\u00a3)",
    "PLN": "Polish Z\u0142oty (z\u0142)",
    "CZK": "Czech Koruna (K\u010d)",
}

# {street} und {postal_code} und {city} -- Reihenfolge pro Region.
# Kontinentaleuropaeisch: PLZ vor Ort, zweite Zeile.
# UK: Ort vor Postcode, Postcode auf eigener letzter Zeile.
ADDRESS_FORMATS = {
    "de_DE": "{street}\n{postal_code} {city}",
    "fr_FR": "{street}\n{postal_code} {city}",
    "nl_NL": "{street}\n{postal_code} {city}",
    "pl_PL": "{street}\n{postal_code} {city}",
    "cs_CZ": "{street}\n{postal_code} {city}",
    "sk_SK": "{street}\n{postal_code} {city}",
    "en_GB": "{street}\n{city}\n{postal_code}",
}


async def load_current_region(db: AsyncSession) -> str:
    """Liest die aktuell eingestellte Region aus ClubSetting (Default: Germany)."""
    result = await db.execute(select(ClubSetting).where(ClubSetting.key == "region"))
    entry = result.scalar_one_or_none()
    if entry and entry.value in AVAILABLE_REGIONS:
        return entry.value
    return DEFAULT_REGION


async def load_current_currency(db: AsyncSession) -> str:
    """Liest die aktuell eingestellte Waehrung aus ClubSetting (Default: EUR)."""
    result = await db.execute(select(ClubSetting).where(ClubSetting.key == "currency"))
    entry = result.scalar_one_or_none()
    if entry and entry.value in AVAILABLE_CURRENCIES:
        return entry.value
    return DEFAULT_CURRENCY


def format_money(amount, region: str, currency: str) -> str:
    """Formatiert einen Geldbetrag passend zu Region und Waehrung (via Babel)."""
    try:
        return format_currency(amount, currency, locale=region)
    except Exception:
        logger.warning(f"format_money fehlgeschlagen fuer region={region} currency={currency}")
        return f"{amount} {currency}"


def format_number(value, region: str, decimals: int = 0) -> str:
    """Formatiert eine Zahl (Tausender-/Dezimaltrennzeichen) passend zur Region."""
    try:
        return format_decimal(value, locale=region, format=f"#,##0.{'0' * decimals}" if decimals else "#,##0")
    except Exception:
        logger.warning(f"format_number fehlgeschlagen fuer region={region}")
        return str(value)


def format_address(street: str, postal_code: str, city: str, region: str) -> str:
    """Formatiert eine Adresse (Feld-Reihenfolge) passend zur Region."""
    template = ADDRESS_FORMATS.get(region, ADDRESS_FORMATS[DEFAULT_REGION])
    return template.format(street=street or "", postal_code=postal_code or "", city=city or "")


def format_address_lines(street: str, postal_code: str, city: str, region: str) -> list:
    """Wie format_address, aber als Liste von Zeilen, wobei komplett leere
    Zeilen (z.B. wenn weder PLZ noch Ort gesetzt sind) uebersprungen werden.
    Fuer die HTML-Darstellung mit <br> zwischen den Zeilen gedacht."""
    raw = format_address(street, postal_code, city, region)
    return [line.strip() for line in raw.split("\n") if line.strip()]


def _get_state(request: Optional[Request]):
    region = getattr(request.state, "region", DEFAULT_REGION) if request else DEFAULT_REGION
    currency = getattr(request.state, "currency", DEFAULT_CURRENCY) if request else DEFAULT_CURRENCY
    return region, currency


@pass_context
def jinja_money(context, amount, currency: Optional[str] = None) -> str:
    """Als Jinja-Filter registriert: {{ amount|money }}. Nutzt automatisch
    request.state.region/currency des laufenden Requests, sofern kein
    abweichender currency-Parameter uebergeben wird."""
    request = context.get("request")
    region, default_currency = _get_state(request)
    return format_money(amount, region, currency or default_currency)


@pass_context
def jinja_number(context, value, decimals: int = 0) -> str:
    """Als Jinja-Filter registriert: {{ value|number }} oder {{ value|number(2) }}."""
    request = context.get("request")
    region, _ = _get_state(request)
    return format_number(value, region, decimals)


@pass_context
def jinja_address(context, street: str, postal_code: str, city: str) -> str:
    """Als Jinja-Global registriert: {{ address(street, postal_code, city) }}."""
    request = context.get("request")
    region, _ = _get_state(request)
    return format_address(street, postal_code, city, region)


@pass_context
def jinja_address_lines(context, street: str, postal_code: str, city: str) -> list:
    """Als Jinja-Global registriert: {{ address_lines(street, postal_code, city) }}
    -- Liste von Zeilen (leere Zeilen bereits entfernt), z.B. fuer
    {{ address_lines(...)|join('<br>')|safe }}."""
    request = context.get("request")
    region, _ = _get_state(request)
    return format_address_lines(street, postal_code, city, region)


@pass_context
def jinja_currency_symbol(context) -> str:
    """Als Jinja-Global registriert: {{ currency_symbol() }} -- fuer
    Eingabefeld-Hinweise (z.B. input-group-text neben einem <input>),
    wo nur das Symbol der aktuell eingestellten Waehrung gebraucht wird,
    nicht ein vollstaendig formatierter Betrag."""
    request = context.get("request")
    region, currency = _get_state(request)
    try:
        return get_currency_symbol(currency, locale=region)
    except Exception:
        return currency
