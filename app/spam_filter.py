"""
Spam filter for the ticket system (stage 3).

Two layers, combined:
1. Built-in heuristics (domain/keyword blocklist, link count) -- work
   immediately, no external service, configurable under
   /admin/settings.
2. Optional external API -- only active when a URL is configured. If
   the external call fails, it silently falls back to the heuristics;
   an outage of the external service must never block ticket creation.

The external API is a generic contract, not tied to one vendor -- see
`_external_check` below for the exact request/response shape, and
`integrations/spam-check-adapter/` for a runnable reference
implementation any admin can adapt to front a real provider (Akismet,
a self-hosted filter like rspamd, or a paid API). This used to be
hard-wired to apilayer.com's Spam Check API specifically; see
docs/ADR/0038-spam-filter-external-api-tied-to-apilayer-for-now.md for
why that happened and docs/ADR/0066-spam-filter-external-api-back-to-a-generic-contract.md
for why it moved back to a generic one (apilayer's API stopped working
and there was no fallback, since nothing else could speak its
vendor-specific contract).

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
from app.crypto_utils import decrypt, DecryptionError

logger = logging.getLogger(__name__)

_DEFAULT_THRESHOLD = 0.5


@dataclass
class SpamCheckResult:
    is_spam_suspected: bool
    score: Optional[float] = None
    reasoning: Optional[str] = None


def _comma_separated_list(value: Optional[str]) -> List[str]:
    if not value:
        return []
    return [part.strip().lower() for part in value.split(",") if part.strip()]


async def _load_configuration(db: AsyncSession) -> dict:
    key_list = [
        "spam_domain_blocklist", "spam_keyword_blocklist", "spam_schwellenwert",
        "spam_api_url", "spam_api_key",
    ]
    result = await db.execute(
        select(ClubSetting).where(ClubSetting.key.in_(key_list))
    )
    stored = {e.key: e.value for e in result.scalars().all() if e.value}

    try:
        threshold = float(stored.get("spam_schwellenwert", _DEFAULT_THRESHOLD))
    except ValueError:
        threshold = _DEFAULT_THRESHOLD

    try:
        api_key = decrypt(stored.get("spam_api_key")) or ""
    except DecryptionError:
        logger.error(
            "Could not decrypt the stored spam-check API key -- did SECRET_KEY change? "
            "Treating it as not configured until it's re-entered."
        )
        api_key = ""

    return {
        "domain_blocklist": _comma_separated_list(stored.get("spam_domain_blocklist")),
        "keyword_blocklist": _comma_separated_list(stored.get("spam_keyword_blocklist")),
        "threshold": threshold,
        "api_url": stored.get("spam_api_url", ""),
        "api_key": api_key,
    }


def _heuristic_score(
    sender_email: str, subject: str, content: str,
    domain_blocklist: List[str], keyword_blocklist: List[str],
) -> Tuple[float, List[str]]:
    """Computes a 0.0-1.0 score from simple, traceable rules."""
    score = 0.0
    reasons: List[str] = []

    sender_domain = sender_email.rsplit("@", 1)[-1].lower() if "@" in sender_email else ""
    if sender_domain and any(domain == sender_domain for domain in domain_blocklist):
        score += 0.6
        reasons.append(f"Sender domain '{sender_domain}' on blocklist")

    combined_text = f"{subject} {content}".lower()
    found_keywords = [kw for kw in keyword_blocklist if kw in combined_text]
    if found_keywords:
        score += min(0.5, 0.2 * len(found_keywords))
        reasons.append(f"Keywords found: {', '.join(found_keywords[:5])}")

    link_count = len(re.findall(r"https?://", content or "", flags=re.IGNORECASE))
    if link_count > 3:
        score += 0.2
        reasons.append(f"{link_count} links in text (unusually many)")

    return min(score, 1.0), reasons


async def _external_check(
    config: dict, sender_email: str, subject: str, content: str
) -> Optional[float]:
    """
    Calls the external spam-check API configured under Admin ->
    Settings, if any. Generic contract, so any provider can be wired up
    behind a thin adapter without touching this code (see
    integrations/spam-check-adapter/ for a runnable reference):

        POST {api_url}
        Authorization: Bearer {api_key}   (only sent if a key is set)
        Content-Type: application/json
        {"sender_email": "...", "subject": "...", "content": "..."}

        -> 200 OK
           {"spam_score": 0.0-1.0}

    Any mismatch (timeout, non-2xx, missing/non-numeric spam_score) is
    treated as "no external signal" -- returns None, same as no API
    being configured at all. An outage or misconfiguration of the
    external service must never block ticket creation.
    """
    if not config["api_url"]:
        return None

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            headers = {"Authorization": f"Bearer {config['api_key']}"} if config["api_key"] else {}
            response = await client.post(
                config["api_url"],
                headers=headers,
                json={"sender_email": sender_email, "subject": subject, "content": content},
            )
            response.raise_for_status()
            data = response.json()
            score = float(data.get("spam_score"))
            return max(0.0, min(1.0, score))
    except Exception as e:
        logger.warning(f"External spam check failed, falling back to heuristics only: {e}")
        return None


async def check_for_spam(
    sender_email: str, subject: str, content: str, db: AsyncSession
) -> SpamCheckResult:
    """
    Checks an incoming message for suspected spam. Combines built-in
    heuristics with an optional external API (maximum of both scores).
    An outage of the external API never blocks the check.
    """
    config = await _load_configuration(db)

    heuristic_score, reasons = _heuristic_score(
        sender_email, subject, content,
        config["domain_blocklist"], config["keyword_blocklist"],
    )

    external_score = await _external_check(config, sender_email, subject, content)
    if external_score is not None and external_score > heuristic_score:
        final_score = external_score
        reasons.append(f"External spam-check API scored this message {final_score:.2f}")
    else:
        final_score = heuristic_score

    is_suspected = final_score >= config["threshold"]

    return SpamCheckResult(
        is_spam_suspected=is_suspected,
        score=round(final_score, 2),
        reasoning="; ".join(reasons) if reasons else None,
    )
