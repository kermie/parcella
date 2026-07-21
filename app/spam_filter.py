"""
Spam filter for the ticket system (stage 3).

Two layers, combined:
1. Built-in heuristics (domain/keyword blocklist, link count) -- work
   immediately, no external service, configurable under
   /admin/settings.
2. Optional external API (e.g. Akismet, a self-hosted filter) -- only
   active when a URL is configured. If the external call fails, it
   silently falls back to the heuristics; an outage of the external
   service must never block ticket creation.

The final score is the maximum of the heuristic and external scores.
If the score is >= the threshold, the message is flagged as suspected
spam.
"""
import logging
import re
from dataclasses import dataclass
from typing import Optional, List, Tuple

import httpx
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models import ClubSetting
from app.crypto_utils import entschluesseln

logger = logging.getLogger(__name__)

_STANDARD_SCHWELLENWERT = 0.5


@dataclass
class SpamPruefungsErgebnis:
    ist_spam_verdacht: bool
    score: Optional[float] = None
    begruendung: Optional[str] = None


def _liste_aus_kommagetrennt(value: Optional[str]) -> List[str]:
    if not value:
        return []
    return [teil.strip().lower() for teil in value.split(",") if teil.strip()]


async def _lade_konfiguration(db: AsyncSession) -> dict:
    schluessel_liste = [
        "spam_domain_blocklist", "spam_keyword_blocklist", "spam_schwellenwert",
        "spam_api_url", "spam_api_key",
    ]
    result = await db.execute(
        select(ClubSetting).where(ClubSetting.key.in_(schluessel_liste))
    )
    gespeichert = {e.key: e.value for e in result.scalars().all() if e.value}

    try:
        schwellenwert = float(gespeichert.get("spam_schwellenwert", _STANDARD_SCHWELLENWERT))
    except ValueError:
        schwellenwert = _STANDARD_SCHWELLENWERT

    return {
        "domain_blocklist": _liste_aus_kommagetrennt(gespeichert.get("spam_domain_blocklist")),
        "keyword_blocklist": _liste_aus_kommagetrennt(gespeichert.get("spam_keyword_blocklist")),
        "schwellenwert": schwellenwert,
        "api_url": gespeichert.get("spam_api_url", ""),
        "api_key": entschluesseln(gespeichert.get("spam_api_key")) or "",
    }


def _heuristik_score(
    absender_email: str, betreff: str, inhalt: str,
    domain_blocklist: List[str], keyword_blocklist: List[str],
) -> Tuple[float, List[str]]:
    """Computes a 0.0-1.0 score from simple, traceable rules."""
    score = 0.0
    gruende: List[str] = []

    absender_domain = absender_email.rsplit("@", 1)[-1].lower() if "@" in absender_email else ""
    if absender_domain and any(domain == absender_domain for domain in domain_blocklist):
        score += 0.6
        gruende.append(f"Absender-Domain '{absender_domain}' auf Sperrliste")

    text_gesamt = f"{betreff} {inhalt}".lower()
    gefundene_keywords = [kw for kw in keyword_blocklist if kw in text_gesamt]
    if gefundene_keywords:
        score += min(0.5, 0.2 * len(gefundene_keywords))
        gruende.append(f"Schlüsselwörter gefunden: {', '.join(gefundene_keywords[:5])}")

    anzahl_links = len(re.findall(r"https?://", inhalt or "", flags=re.IGNORECASE))
    if anzahl_links > 3:
        score += 0.2
        gruende.append(f"{anzahl_links} Links im Text (auffällig viele)")

    return min(score, 1.0), gruende


async def _externe_pruefung(
    konfig: dict, absender_email: str, betreff: str, inhalt: str
) -> Optional[float]:
    """
    Calls an optional external spam-check service. Expects a JSON
    response of the form {"spam_score": 0.0-1.0} -- so any service can
    be hooked up that fulfills this simple contract via a small adapter
    (e.g. a small cloud function). Returns None if no external API is
    configured or the call fails.
    """
    if not konfig["api_url"]:
        return None

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            headers = {"Authorization": f"Bearer {konfig['api_key']}"} if konfig["api_key"] else {}
            response = await client.post(
                konfig["api_url"],
                json={"absender_email": absender_email, "betreff": betreff, "inhalt": inhalt},
                headers=headers,
            )
            response.raise_for_status()
            daten = response.json()
            score = float(daten.get("spam_score", 0.0))
            return max(0.0, min(score, 1.0))
    except Exception as e:
        logger.warning(f"External spam check failed, falling back to heuristics only: {e}")
        return None


async def pruefe_auf_spam(
    absender_email: str, betreff: str, inhalt: str, db: AsyncSession
) -> SpamPruefungsErgebnis:
    """
    Checks an incoming message for suspected spam. Combines built-in
    heuristics with an optional external API (maximum of both scores).
    An outage of the external API never blocks the check.
    """
    konfig = await _lade_konfiguration(db)

    heuristik_score, gruende = _heuristik_score(
        absender_email, betreff, inhalt,
        konfig["domain_blocklist"], konfig["keyword_blocklist"],
    )

    externer_score = await _externe_pruefung(konfig, absender_email, betreff, inhalt)
    if externer_score is not None and externer_score > heuristik_score:
        finaler_score = externer_score
        gruende.append(f"Externe Pruefung: Score {externer_score:.2f}")
    else:
        finaler_score = heuristik_score

    ist_verdacht = finaler_score >= konfig["schwellenwert"]

    return SpamPruefungsErgebnis(
        ist_spam_verdacht=ist_verdacht,
        score=round(finaler_score, 2),
        begruendung="; ".join(gruende) if gruende else None,
    )
