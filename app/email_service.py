"""
Email sending via SMTP (aiosmtplib).

The SMTP configuration primarily comes from the database (ClubSettings,
editable under /admin/settings). If a value is missing there (e.g. on a
fresh install, before anyone has used the UI), it falls back to the
.env file -- so sending still works without any admin interaction, as
long as the environment variables are set.
"""
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional

import aiosmtplib
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.config import settings
from app.models import ClubSetting
from app.crypto_utils import entschluesseln

logger = logging.getLogger(__name__)


async def lade_smtp_konfiguration(db: AsyncSession) -> dict:
    """
    Loads the SMTP configuration: database values take precedence,
    missing values are filled in from the .env file (app.config.settings).
    """
    result = await db.execute(
        select(ClubSetting).where(
            ClubSetting.key.in_(
                ["smtp_host", "smtp_port", "smtp_user", "smtp_password", "smtp_from", "smtp_tls"]
            )
        )
    )
    gespeichert = {e.key: e.value for e in result.scalars().all() if e.value}

    def _bool(value) -> bool:
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in ("true", "1", "ja", "an")

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
    Sends an email. Returns True on success.

    If `db` is passed, the SMTP configuration comes from the database
    (with .env fallback). Without `db`, only the .env configuration is
    used (backwards compatibility).
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
        logger.warning("SMTP not configured -- email will not be sent.")
        logger.info(f"[DEV] To: {empfaenger} | Subject: {betreff}")
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
        logger.info(f"Email sent to {empfaenger}")
        return True
    except Exception as e:
        logger.error(f"Email error sending to {empfaenger}: {e}")
        return False


async def sende_einladung(email: str, einladungslink: str, invited_by: str, db: Optional[AsyncSession] = None) -> bool:
    betreff = f"Einladung zur {settings.app_name}"
    html = f"""
    <html><body>
    <h2>Einladung</h2>
    <p>Sie wurden von <strong>{invited_by}</strong> zur <strong>{settings.app_name}</strong> eingeladen.</p>
    <p>Klicken Sie auf den folgenden Link, um Ihr Konto einzurichten:</p>
    <p><a href="{einladungslink}">Einladung annehmen</a></p>
    <p>Dieser Link ist 7 Tage gültig.</p>
    </body></html>
    """
    return await sende_email(email, betreff, html, db=db)
