"""
E-Mail-Versand via SMTP (aiosmtplib).

Die SMTP-Konfiguration kommt primär aus der Datenbank (Vereinseinstellungen,
editierbar unter /admin/einstellungen). Fehlt dort ein Wert (z.B. bei einer
frischen Installation, bevor jemand die Oberfläche genutzt hat), wird auf
die .env-Datei zurückgegriffen – so funktioniert der Versand auch ganz ohne
Admin-Interaktion, wenn die Umgebungsvariablen gesetzt sind.
"""
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional

import aiosmtplib
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.config import settings
from app.models import Vereinseinstellung
from app.crypto_utils import entschluesseln

logger = logging.getLogger(__name__)


async def lade_smtp_konfiguration(db: AsyncSession) -> dict:
    """
    Lädt die SMTP-Konfiguration: Datenbank-Werte haben Vorrang, fehlende
    Werte werden aus der .env-Datei (app.config.settings) ergänzt.
    """
    result = await db.execute(
        select(Vereinseinstellung).where(
            Vereinseinstellung.schluessel.in_(
                ["smtp_host", "smtp_port", "smtp_user", "smtp_password", "smtp_from", "smtp_tls"]
            )
        )
    )
    gespeichert = {e.schluessel: e.wert for e in result.scalars().all() if e.wert}

    def _bool(wert) -> bool:
        if isinstance(wert, bool):
            return wert
        return str(wert).strip().lower() in ("true", "1", "ja", "an")

    port_wert = gespeichert.get("smtp_port")
    try:
        port = int(port_wert) if port_wert else settings.smtp_port
    except ValueError:
        port = settings.smtp_port

    return {
        "host": gespeichert.get("smtp_host") or settings.smtp_host,
        "port": port,
        "user": gespeichert.get("smtp_user") or settings.smtp_user,
        "password": entschluesseln(gespeichert.get("smtp_password")) or settings.smtp_password,
        "from": gespeichert.get("smtp_from") or settings.smtp_from,
        "tls": _bool(gespeichert.get("smtp_tls")) if "smtp_tls" in gespeichert else settings.smtp_tls,
    }


async def sende_email(
    empfaenger: str,
    betreff: str,
    html_body: str,
    text_body: Optional[str] = None,
    db: Optional[AsyncSession] = None,
) -> bool:
    """
    Sendet eine E-Mail. Gibt True bei Erfolg zurück.

    Wenn `db` übergeben wird, kommt die SMTP-Konfiguration aus der
    Datenbank (mit .env-Fallback). Ohne `db` wird ausschließlich die
    .env-Konfiguration genutzt (Abwärtskompatibilität).
    """
    if db is not None:
        konfig = await lade_smtp_konfiguration(db)
    else:
        konfig = {
            "host": settings.smtp_host,
            "port": settings.smtp_port,
            "user": settings.smtp_user,
            "password": settings.smtp_password,
            "from": settings.smtp_from,
            "tls": settings.smtp_tls,
        }

    if not konfig["host"] or not konfig["user"]:
        logger.warning("SMTP nicht konfiguriert – E-Mail wird nicht gesendet.")
        logger.info(f"[DEV] An: {empfaenger} | Betreff: {betreff}")
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = betreff
    msg["From"] = konfig["from"]
    msg["To"] = empfaenger

    if text_body:
        msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        await aiosmtplib.send(
            msg,
            hostname=konfig["host"],
            port=konfig["port"],
            username=konfig["user"],
            password=konfig["password"],
            start_tls=konfig["tls"],
        )
        logger.info(f"E-Mail gesendet an {empfaenger}")
        return True
    except Exception as e:
        logger.error(f"E-Mail-Fehler an {empfaenger}: {e}")
        return False


async def sende_einladung(email: str, einladungslink: str, eingeladen_von: str, db: Optional[AsyncSession] = None) -> bool:
    betreff = f"Einladung zur {settings.app_name}"
    html = f"""
    <html><body>
    <h2>Einladung</h2>
    <p>Sie wurden von <strong>{eingeladen_von}</strong> zur <strong>{settings.app_name}</strong> eingeladen.</p>
    <p>Klicken Sie auf den folgenden Link, um Ihr Konto einzurichten:</p>
    <p><a href="{einladungslink}">Einladung annehmen</a></p>
    <p>Dieser Link ist 7 Tage gültig.</p>
    </body></html>
    """
    return await sende_email(email, betreff, html, db=db)
