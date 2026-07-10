"""
Verschlüsselung für sensible Einstellungswerte (z.B. SMTP-Passwort), die
in der Datenbank gespeichert werden. Nutzt Fernet (symmetrische
Verschlüsselung aus der `cryptography`-Bibliothek), mit einem aus
SECRET_KEY abgeleiteten Schlüssel.

Wichtig: Wenn sich SECRET_KEY ändert, können bereits verschlüsselte Werte
nicht mehr entschlüsselt werden. Das ist beabsichtigt – SECRET_KEY sollte
ohnehin stabil und geheim gehalten werden.
"""
import base64
import hashlib
import logging
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings

logger = logging.getLogger(__name__)


def _abgeleiteter_schluessel() -> bytes:
    """Leitet einen für Fernet gültigen 32-Byte-Schlüssel aus SECRET_KEY ab."""
    digest = hashlib.sha256(settings.secret_key.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


_fernet = Fernet(_abgeleiteter_schluessel())


def verschluesseln(klartext: str) -> str:
    """Verschlüsselt einen String für die Speicherung in der Datenbank."""
    if not klartext:
        return klartext
    return _fernet.encrypt(klartext.encode("utf-8")).decode("utf-8")


def entschluesseln(wert: Optional[str]) -> Optional[str]:
    """
    Entschlüsselt einen zuvor verschlüsselten String.

    Abwärtskompatibilität: Werte, die vor Einführung der Verschlüsselung
    im Klartext gespeichert wurden, sind kein gültiges Fernet-Token –
    in diesem Fall wird der Wert unverändert (als Klartext) zurückgegeben,
    statt einen Fehler zu werfen.
    """
    if not wert:
        return wert
    try:
        return _fernet.decrypt(wert.encode("utf-8")).decode("utf-8")
    except (InvalidToken, ValueError):
        logger.warning("Konnte Wert nicht entschlüsseln – behandle ihn als Klartext (Altbestand?).")
        return wert
