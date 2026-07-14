"""
Internationalisierung (i18n): eine Sprache pro Installation, umschaltbar
über eine Vereinseinstellung (ClubSetting-Schlüssel "language").

Architekturentscheidung (siehe docs/i18n.md):
- EINE Sprache pro Installation, nicht pro Benutzer und nicht per
  Browser-Erkennung. Ein Verein besteht i.d.R. aus einer Sprachgemeinschaft;
  eine Instanz pro Sprache zu betreiben wäre unnötig kompliziert.
- Einfaches JSON-Wörterbuch pro Sprache (app/translations/<code>.json),
  kein gettext/Babel – kein Kompilierschritt nötig, für jeden ohne
  Spezialwissen editierbar, und trotzdem mit Weblate & Co. kompatibel,
  falls später crowd-sourced Übersetzungen gewünscht sind.
- Deutsch ist weiterhin die Quellsprache, in der neue Oberflächentexte
  zuerst geschrieben werden. Der Laufzeit-Fallback ist jedoch Englisch:
  fehlt ein Schlüssel in der Zielsprache (z.B. weil ein Modul noch
  nicht übersetzt wurde), wird die englische Zeichenkette angezeigt,
  nicht die deutsche – und eine frische Installation ohne gesetzte
  ClubSetting "language" startet ebenfalls auf Englisch, nicht Deutsch
  (siehe DEFAULT_LANGUAGE unten). Das reine Autoren-Vorgehen (Deutsch
  zuerst schreiben, dann übersetzen) ändert sich dadurch nicht.
- Die aktuelle Sprache wird – wie schon die Modul-Flags (siehe
  app/module_flags.py) – einmal pro Request in einer Middleware geladen
  und unter request.state.language abgelegt.

Neues Modul übersetzen:
1. Schlüssel unter einem Namensraum in app/translations/de.json ergänzen,
   z.B. "purchase_requests": {"overview": {"title": "..."}}.
2. Dieselben Schlüssel in app/translations/en.json (oder jeder weiteren
   Sprachdatei) mit der Übersetzung ergänzen.
3. In Templates: {{ t('purchase_requests.overview.title') }}
4. In Python (Router): t_for(request, 'purchase_requests.overview.title')
"""
import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import Request
from jinja2 import pass_context
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models import ClubSetting

logger = logging.getLogger(__name__)

DEFAULT_LANGUAGE = "en"
TRANSLATIONS_DIR = Path(__file__).parent / "translations"

# Sprachen, die in der Vereinseinstellungen-Oberfläche zur Auswahl stehen.
# Weitere Sprachen: einfach eine neue app/translations/<code>.json anlegen
# und hier ergänzen (kein Code-Deploy-Schritt nötig, nur ein Neustart,
# damit load_translations() die neue Datei einliest).
AVAILABLE_LANGUAGES = {
    "de": "Deutsch",
    "en": "English",
    "pl": "Polski",
    "cs": "Čeština",
    "sk": "Slovenčina",
    "fr": "Français",
    "nl": "Nederlands",
}

# In-Memory-Cache: {"de": {...verschachteltes dict...}, "en": {...}}
_TRANSLATIONS: Dict[str, Dict[str, Any]] = {}


def load_translations() -> None:
    """Liest alle app/translations/*.json einmal beim Start in den Cache ein."""
    _TRANSLATIONS.clear()
    for path in TRANSLATIONS_DIR.glob("*.json"):
        lang_code = path.stem
        with open(path, encoding="utf-8") as f:
            _TRANSLATIONS[lang_code] = json.load(f)
    if DEFAULT_LANGUAGE not in _TRANSLATIONS:
        logger.error(
            f"Quellsprache '{DEFAULT_LANGUAGE}' nicht gefunden in {TRANSLATIONS_DIR} – "
            f"Übersetzungen werden nicht funktionieren."
        )


def _lookup(catalog: Dict[str, Any], dotted_key: str) -> Optional[str]:
    node: Any = catalog
    for part in dotted_key.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node if isinstance(node, str) else None


def translate(key: str, lang: str, **kwargs) -> str:
    """
    Löst einen gepunkteten Übersetzungsschlüssel auf (z.B. "tickets.overview.title").
    Fällt auf Deutsch zurück, falls in der Zielsprache nicht vorhanden;
    fällt auf den rohen Schlüssel zurück (mit Log-Warnung), falls auch dort nicht vorhanden.
    """
    value = _lookup(_TRANSLATIONS.get(lang, {}), key)
    if value is None and lang != DEFAULT_LANGUAGE:
        value = _lookup(_TRANSLATIONS.get(DEFAULT_LANGUAGE, {}), key)
    if value is None:
        logger.warning(f"Fehlender Übersetzungsschlüssel '{key}' (Sprache: {lang})")
        return key

    if kwargs:
        try:
            return value.format(**kwargs)
        except (KeyError, IndexError):
            return value
    return value


async def load_current_language(db: AsyncSession) -> str:
    """Liest die aktuell eingestellte Sprache aus ClubSetting (Default: Deutsch)."""
    result = await db.execute(select(ClubSetting).where(ClubSetting.key == "language"))
    entry = result.scalar_one_or_none()
    if entry and entry.value in AVAILABLE_LANGUAGES:
        return entry.value
    return DEFAULT_LANGUAGE


def t_for(request: Request, key: str, **kwargs) -> str:
    """Übersetzungshilfe für Python-Code (Router, Flash-Nachrichten, Fehlermeldungen)."""
    lang = getattr(request.state, "language", DEFAULT_LANGUAGE)
    return translate(key, lang, **kwargs)


@pass_context
def jinja_t(context, key: str, **kwargs) -> str:
    """Als Jinja-Global registriert (siehe app/main.py) – nutzt automatisch
    request.state.language des laufenden Requests, ohne dass jeder Router
    `t` explizit in den Template-Kontext einfügen muss."""
    request = context.get("request")
    lang = getattr(request.state, "language", DEFAULT_LANGUAGE) if request else DEFAULT_LANGUAGE
    return translate(key, lang, **kwargs)
