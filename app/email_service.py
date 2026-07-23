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
from app.crypto_utils import decrypt

logger = logging.getLogger(__name__)


async def load_smtp_configuration(db: AsyncSession) -> dict:
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
    stored = {e.key: e.value for e in result.scalars().all() if e.value}

    def _bool(value) -> bool:
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in ("true", "1", "ja", "an")

    port_value = stored.get("smtp_port")
    try:
        port = int(port_value) if port_value else settings.smtp_port
    except ValueError:
        port = settings.smtp_port

    return {
        "host": stored.get("smtp_host") or settings.smtp_host,
        "port": port,
        "user": stored.get("smtp_user") or settings.smtp_user,
        "password": decrypt(stored.get("smtp_password")) or settings.smtp_password,
        "from": stored.get("smtp_from") or settings.smtp_from,
        "tls": _bool(stored.get("smtp_tls")) if "smtp_tls" in stored else settings.smtp_tls,
    }


async def send_email(
    recipient: str,
    subject: str,
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
        config = await load_smtp_configuration(db)
    else:
        config = {
            "host": settings.smtp_host,
            "port": settings.smtp_port,
            "user": settings.smtp_user,
            "password": settings.smtp_password,
            "from": settings.smtp_from,
            "tls": settings.smtp_tls,
        }

    if not config["host"] or not config["user"]:
        logger.warning("SMTP not configured -- email will not be sent.")
        logger.info(f"[DEV] To: {recipient} | Subject: {subject}")
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = config["from"]
    msg["To"] = recipient

    if text_body:
        msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        await aiosmtplib.send(
            msg,
            hostname=config["host"],
            port=config["port"],
            username=config["user"],
            password=config["password"],
            start_tls=config["tls"],
        )
        logger.info(f"Email sent to {recipient}")
        return True
    except Exception as e:
        logger.error(f"Email error sending to {recipient}: {e}")
        return False
