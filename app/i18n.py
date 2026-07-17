"""
Internationalization (i18n): one language per installation, switchable
via a club setting (ClubSetting key "language").

Architecture decision (see docs/i18n-l10n.md):
- ONE language per installation, not per user and not browser-detected.
  An association is usually a single language community; running one
  instance per language would be needlessly complicated.
- Simple JSON dictionary per language (app/translations/<code>.json),
  no gettext/Babel -- no build step needed, editable by anyone without
  special tooling, and still compatible with Weblate & co. if
  crowd-sourced translation is ever wanted.
- English is the one and only base/authoring language: new UI text is
  written in English first, then translated into the other six
  (German, Polish, Czech, Slovak, French, Dutch). This project is
  open-source and meant for adoption by any allotment garden
  association in any country, so there's a single source of truth for
  both code and UI text -- not split between an "authoring language"
  and a "fallback language" the way it briefly was early on.
- English is also the runtime fallback and the fresh-install default:
  if a key is missing for the selected language (e.g. a brand-new
  module before its Czech translation is ready), the English string is
  shown. A fresh installation with no "language" ClubSetting set yet
  also starts in English (see DEFAULT_LANGUAGE below). Since English is
  now also the authoring language, this is no longer two separate
  decisions -- they're the same thing.
- The current language is loaded once per request in a middleware --
  same pattern as the module flags (see app/module_flags.py) -- and
  stored under request.state.language.

Translating a new module:
1. Add keys under a namespace in app/translations/en.json (the source
   of truth), e.g. "purchase_requests": {"overview": {"title": "..."}}.
2. Add the same keys, translated, to every other
   app/translations/<code>.json file (de, pl, cs, sk, fr, nl).
3. In templates: {{ t('purchase_requests.overview.title') }}
4. In Python (routers): t_for(request, 'purchase_requests.overview.title')
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

# Languages offered in the club-settings UI. To add another language,
# just create a new app/translations/<code>.json and add it here (no
# code-deploy step needed, just a restart so load_translations() picks
# up the new file).
AVAILABLE_LANGUAGES = {
    "de": "Deutsch",
    "en": "English",
    "pl": "Polski",
    "cs": "Čeština",
    "sk": "Slovenčina",
    "fr": "Français",
    "nl": "Nederlands",
}

# In-memory cache: {"en": {...nested dict...}, "de": {...}}
_TRANSLATIONS: Dict[str, Dict[str, Any]] = {}


def load_translations() -> None:
    """Reads every app/translations/*.json into the cache once at startup."""
    _TRANSLATIONS.clear()
    for path in TRANSLATIONS_DIR.glob("*.json"):
        lang_code = path.stem
        with open(path, encoding="utf-8") as f:
            _TRANSLATIONS[lang_code] = json.load(f)
    if DEFAULT_LANGUAGE not in _TRANSLATIONS:
        logger.error(
            f"Source language '{DEFAULT_LANGUAGE}' not found in {TRANSLATIONS_DIR} -- "
            f"translations will not work."
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
    Resolves a dotted translation key (e.g. "tickets.overview.title").
    Falls back to English if not present in the target language; falls
    back to the raw key (with a log warning) if not present there either.
    """
    value = _lookup(_TRANSLATIONS.get(lang, {}), key)
    if value is None and lang != DEFAULT_LANGUAGE:
        value = _lookup(_TRANSLATIONS.get(DEFAULT_LANGUAGE, {}), key)
    if value is None:
        logger.warning(f"Missing translation key '{key}' (language: {lang})")
        return key

    if kwargs:
        try:
            return value.format(**kwargs)
        except (KeyError, IndexError):
            return value
    return value


async def load_current_language(db: AsyncSession) -> str:
    """Reads the currently configured language from ClubSetting (default: English)."""
    result = await db.execute(select(ClubSetting).where(ClubSetting.key == "language"))
    entry = result.scalar_one_or_none()
    if entry and entry.value in AVAILABLE_LANGUAGES:
        return entry.value
    return DEFAULT_LANGUAGE


def t_for(request: Request, key: str, **kwargs) -> str:
    """Translation helper for Python code (routers, flash messages, error messages)."""
    lang = getattr(request.state, "language", DEFAULT_LANGUAGE)
    return translate(key, lang, **kwargs)


@pass_context
def jinja_t(context, key: str, **kwargs) -> str:
    """Registered as a Jinja global (see app/main.py) -- automatically uses
    request.state.language of the current request, so no router needs to
    inject `t` into the template context itself."""
    request = context.get("request")
    lang = getattr(request.state, "language", DEFAULT_LANGUAGE) if request else DEFAULT_LANGUAGE
    return translate(key, lang, **kwargs)
