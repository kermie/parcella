"""
Encryption for sensitive setting values (e.g. SMTP password) stored in
the database. Uses Fernet (symmetric encryption from the
`cryptography` library), with a key derived from SECRET_KEY.

Important: if SECRET_KEY changes, already-encrypted values can no
longer be decrypted. This is intentional -- SECRET_KEY should stay
stable and secret regardless.
"""
import base64
import hashlib
import logging
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings

logger = logging.getLogger(__name__)


def _abgeleiteter_schluessel() -> bytes:
    """Derives a Fernet-valid 32-byte key from SECRET_KEY."""
    digest = hashlib.sha256(settings.secret_key.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


_fernet = Fernet(_abgeleiteter_schluessel())


def verschluesseln(klartext: str) -> str:
    """Encrypts a string for storage in the database."""
    if not klartext:
        return klartext
    return _fernet.encrypt(klartext.encode("utf-8")).decode("utf-8")


def entschluesseln(wert: Optional[str]) -> Optional[str]:
    """
    Decrypts a previously encrypted string.

    Backwards compatibility: values stored in plaintext before
    encryption was introduced aren't a valid Fernet token -- in that
    case the value is returned unchanged (as plaintext) instead of
    raising an error.
    """
    if not wert:
        return wert
    try:
        return _fernet.decrypt(wert.encode("utf-8")).decode("utf-8")
    except (InvalidToken, ValueError):
        logger.warning("Could not decrypt value -- treating it as plaintext (legacy data?).")
        return wert
