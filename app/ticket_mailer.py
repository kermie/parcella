"""
Ticket-Postfach-Integration (Etappe 2): IMAP-Abruf eingehender E-Mails und
SMTP-Versand ausgehender Antworten – über DASSELBE Postfach, das auch für
allgemeine System-E-Mails (Einladungen) genutzt wird. Es gibt bewusst nur
EINEN Satz SMTP-Zugangsdaten (siehe app/email_service.py); hier kommen nur
die zusätzlichen IMAP-Felder dazu, die zum Empfangen nötig sind.

Design-Entscheidungen (siehe auch docs/module-tickets.md):
- Betriebsdaten (letzte verarbeitete UID, Fehler) leben in der bestehenden
  `vereinseinstellungen`-Tabelle statt einer eigenen Tabelle – konsistent
  mit dem Rest des Projekts, keine weitere Migration nötig.
- IMAP-Abruf läuft synchron (Python-Bordmittel `imaplib`/`email`), aber in
  einem Thread-Executor (`asyncio.to_thread`), damit der Event-Loop nicht
  blockiert.
- Threading: eingehende Antworten werden über die Header `In-Reply-To`/
  `References` einem bestehenden Ticket zugeordnet (Vergleich mit den
  gespeicherten `message_id`-Werten vorheriger Nachrichten). Schlägt das
  fehl, wird ersatzweise nach Absender-Adresse + ähnlichem Betreff in
  offenen Tickets gesucht. Ohne Treffer entsteht ein neues Ticket.
"""
import asyncio
import email
import imaplib
import logging
import re
from datetime import datetime, timezone
from email.header import decode_header
from email.mime.text import MIMEText
from email.utils import parseaddr, make_msgid
from typing import Optional, List, Dict, Any

import aiosmtplib
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models import ClubSetting, Ticket, TicketMessage, TicketStatus, MessageDirection
from app.email_service import lade_smtp_konfiguration
from app.ticket_utils import find_members_by_email
from app.spam_filter import pruefe_auf_spam

logger = logging.getLogger(__name__)

# Betriebsdaten-Schlüssel in der vereinseinstellungen-Tabelle
_KEY_LAST_UID = "ticket_imap_letzte_uid"
_KEY_LAST_FETCH = "ticket_imap_letzter_abruf"
_KEY_LAST_ERROR = "ticket_imap_letzter_fehler"


async def _read_setting(db: AsyncSession, key: str) -> Optional[str]:
    result = await db.execute(select(ClubSetting).where(ClubSetting.key == key))
    entry = result.scalar_one_or_none()
    return entry.value if entry else None


async def _write_setting(db: AsyncSession, key: str, value: Optional[str]) -> None:
    result = await db.execute(select(ClubSetting).where(ClubSetting.key == key))
    entry = result.scalar_one_or_none()
    if entry:
        entry.value = value
    else:
        db.add(ClubSetting(key=key, value=value))


async def load_inbox_configuration(db: AsyncSession) -> Dict[str, Any]:
    """
    Lädt die Postfach-Konfiguration: SMTP-Zugangsdaten kommen aus der
    bereits bestehenden allgemeinen E-Mail-Konfiguration (dasselbe Postfach
    wie für Einladungen) – nur IMAP-Host/-Port/-SSL sind ticketspezifisch,
    da die allgemeine Konfiguration nur zum Senden gedacht ist.
    """
    smtp_config = await lade_smtp_konfiguration(db)

    result = await db.execute(
        select(ClubSetting).where(
            ClubSetting.key.in_(["imap_host", "imap_port", "imap_ssl"])
        )
    )
    stored = {e.key: e.value for e in result.scalars().all() if e.value}

    def _bool(value, default=True) -> bool:
        if value is None:
            return default
        return str(value).strip().lower() in ("true", "1", "ja", "an")

    return {
        "imap_host": stored.get("imap_host", ""),
        "imap_port": int(stored.get("imap_port") or 993),
        "imap_user": smtp_config["user"],       # dasselbe Postfach wie SMTP
        "imap_password": smtp_config["password"],
        "imap_ssl": _bool(stored.get("imap_ssl"), True),
        "smtp_host": smtp_config["host"],
        "smtp_port": smtp_config["port"],
        "smtp_user": smtp_config["user"],
        "smtp_password": smtp_config["password"],
        "smtp_tls": smtp_config["tls"],
        "smtp_from": smtp_config["from"],
    }


def is_inbox_configured(config: Dict[str, Any]) -> bool:
    return bool(config["imap_host"] and config["smtp_host"] and config["smtp_user"])


def _safe_decode(payload: bytes, charset: Optional[str]) -> str:
    """
    Dekodiert Bytes mit dem angegebenen Zeichensatz, fällt aber sicher auf
    UTF-8 zurück, wenn der Zeichensatz Python unbekannt ist.

    Manche Mailserver/-clients deklarieren nicht-standardkonforme
    Zeichensatznamen wie "unknown-8bit" – das führt zu einem LookupError,
    noch bevor überhaupt ein einziges Byte dekodiert wird. errors="replace"
    allein hilft hier NICHT, da es nur fehlerhafte Bytes bei einem
    BEKANNTEN Zeichensatz abfängt, nicht einen unbekannten Namen selbst.
    """
    try:
        return payload.decode(charset or "utf-8", errors="replace")
    except LookupError:
        return payload.decode("utf-8", errors="replace")


def _decode_header(value: Optional[str]) -> str:
    if not value:
        return ""
    parts = decode_header(value)
    result = ""
    for text, encoding in parts:
        if isinstance(text, bytes):
            result += _safe_decode(text, encoding)
        else:
            result += text
    return result


def _extract_text(msg) -> str:
    """Bevorzugt text/plain, fällt auf grob bereinigtes text/html zurück."""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and not part.get("Content-Disposition"):
                payload = part.get_payload(decode=True)
                if payload:
                    return _safe_decode(payload, part.get_content_charset())
        for part in msg.walk():
            if part.get_content_type() == "text/html" and not part.get("Content-Disposition"):
                payload = part.get_payload(decode=True)
                if payload:
                    html = _safe_decode(payload, part.get_content_charset())
                    return re.sub(r"<[^>]+>", "", html).strip()
        return ""
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            return _safe_decode(payload, msg.get_content_charset())
        return ""


def _fetch_highest_uid_sync(config: Dict[str, Any]) -> int:
    """
    Ermittelt nur die höchste aktuell vorhandene UID, OHNE eine einzige
    Nachricht abzurufen. Wird für die Erstsynchronisierung genutzt (siehe
    process_incoming_mails) – verhindert, dass beim allerersten Abruf
    Tausende bestehende Alt-Mails auf einmal als Tickets importiert werden.
    """
    if config["imap_ssl"]:
        connection = imaplib.IMAP4_SSL(config["imap_host"], config["imap_port"])
    else:
        connection = imaplib.IMAP4(config["imap_host"], config["imap_port"])

    try:
        connection.login(config["imap_user"], config["imap_password"])
        connection.select("INBOX")
        status, data = connection.uid("search", None, "ALL")
        if status != "OK" or not data or not data[0]:
            return 0
        all_uids = [int(u) for u in data[0].split()]
        return max(all_uids) if all_uids else 0
    finally:
        try:
            connection.close()
        except Exception:
            pass
        try:
            connection.logout()
        except Exception:
            pass


def _fetch_new_mails_sync(config: Dict[str, Any], last_uid: Optional[int]) -> List[Dict[str, Any]]:
    """
    Synchrone IMAP-Abfrage (läuft in einem Thread-Executor). Gibt eine Liste
    geparster Nachrichten zurück, jeweils mit uid, message_id, in_reply_to,
    references, from_email, from_name, subject, text.
    """
    results: List[Dict[str, Any]] = []

    if config["imap_ssl"]:
        connection = imaplib.IMAP4_SSL(config["imap_host"], config["imap_port"])
    else:
        connection = imaplib.IMAP4(config["imap_host"], config["imap_port"])

    try:
        connection.login(config["imap_user"], config["imap_password"])
        connection.select("INBOX")

        status, data = connection.uid("search", None, "ALL")
        if status != "OK" or not data or not data[0]:
            return results

        all_uids = [int(u) for u in data[0].split()]
        new_uids = [u for u in all_uids if last_uid is None or u > last_uid]

        for uid in new_uids:
            status, msg_data = connection.uid("fetch", str(uid), "(RFC822)")
            if status != "OK" or not msg_data or not msg_data[0]:
                continue

            raw = msg_data[0][1]
            msg = email.message_from_bytes(raw)

            from_name, from_email = parseaddr(_decode_header(msg.get("From", "")))
            subject = _decode_header(msg.get("Subject", "(ohne Betreff)"))
            message_id = (msg.get("Message-ID") or "").strip()
            in_reply_to = (msg.get("In-Reply-To") or "").strip()
            references = (msg.get("References") or "").strip()

            results.append({
                "uid": uid,
                "message_id": message_id,
                "in_reply_to": in_reply_to,
                "references": references,
                "from_email": from_email.strip().lower(),
                "from_name": from_name.strip(),
                "subject": subject.strip() or "(ohne Betreff)",
                "text": _extract_text(msg).strip(),
            })

        return results
    finally:
        try:
            connection.close()
        except Exception:
            pass
        try:
            connection.logout()
        except Exception:
            pass


def _normalize_subject(subject: str) -> str:
    """Entfernt Antwort-/Weiterleitungs-Präfixe für den Fallback-Betreffvergleich."""
    return re.sub(r"^(re|aw|fwd?)\s*:\s*", "", subject.strip(), flags=re.IGNORECASE).strip().lower()


async def _find_matching_ticket(db: AsyncSession, mail: Dict[str, Any]) -> Optional[Ticket]:
    """Sucht ein bestehendes Ticket für eine eingehende Antwort."""
    # 1. Über Message-ID-Threading (In-Reply-To oder References)
    candidate_ids = []
    if mail["in_reply_to"]:
        candidate_ids.append(mail["in_reply_to"])
    if mail["references"]:
        candidate_ids.extend(mail["references"].split())

    if candidate_ids:
        result = await db.execute(
            select(TicketMessage).where(TicketMessage.message_id.in_(candidate_ids))
        )
        match = result.scalars().first()
        if match:
            ticket_result = await db.execute(select(Ticket).where(Ticket.id == match.ticket_id))
            ticket = ticket_result.scalar_one_or_none()
            if ticket:
                return ticket

    # 2. Fallback: gleicher Absender + ähnlicher Betreff, nicht geschlossen
    normalized = _normalize_subject(mail["subject"])
    result = await db.execute(
        select(Ticket).where(
            Ticket.sender_email == mail["from_email"],
            Ticket.status != TicketStatus.CLOSED,
        )
    )
    for ticket in result.scalars().all():
        if _normalize_subject(ticket.subject) == normalized:
            return ticket

    return None


async def process_incoming_mails(db: AsyncSession) -> int:
    """
    Ruft neue E-Mails ab und verarbeitet sie zu Tickets/Nachrichten.
    Gibt die Anzahl neu verarbeiteter Mails zurück. Fehler werden abgefangen
    und in der Datenbank vermerkt, statt den Hintergrundjob abstürzen zu lassen.
    """
    config = await load_inbox_configuration(db)
    if not is_inbox_configured(config):
        return 0

    last_uid_str = await _read_setting(db, _KEY_LAST_UID)
    first_run = last_uid_str is None
    last_uid = int(last_uid_str) if last_uid_str else None

    if first_run:
        # Beim allerersten Abruf werden bestehende E-Mails NICHT importiert –
        # nur die aktuell höchste UID wird als Startpunkt gemerkt. Sonst
        # würde ein Postfach mit tausenden Alt-Mails auf einen Schlag komplett
        # (und sehr langsam, ggf. mit Verbindungsabbruch) importiert werden.
        try:
            highest_uid = await asyncio.to_thread(_fetch_highest_uid_sync, config)
        except Exception as e:
            logger.error(f"IMAP-Erstsynchronisierung fehlgeschlagen: {e}")
            await _write_setting(db, _KEY_LAST_ERROR, str(e))
            await db.commit()
            return 0

        await _write_setting(db, _KEY_LAST_UID, str(highest_uid))
        await _write_setting(db, _KEY_LAST_FETCH, datetime.now(timezone.utc).isoformat())
        await _write_setting(db, _KEY_LAST_ERROR, None)
        await db.commit()
        logger.info(
            f"Ticket-Postfach: Erstsynchronisierung abgeschlossen (UID {highest_uid}). "
            f"Bestehende E-Mails wurden übersprungen, ab jetzt werden nur neue E-Mails verarbeitet."
        )
        return 0

    try:
        mails = await asyncio.to_thread(_fetch_new_mails_sync, config, last_uid)
    except Exception as e:
        logger.error(f"IMAP-Abruf fehlgeschlagen: {e}")
        await _write_setting(db, _KEY_LAST_ERROR, str(e))
        await db.commit()
        return 0

    processed = 0
    highest_uid = last_uid or 0

    for mail in mails:
        highest_uid = max(highest_uid, mail["uid"])

        if not mail["from_email"]:
            continue  # unbrauchbare Nachricht (keine Absenderadresse geparst)

        ticket = await _find_matching_ticket(db, mail)

        if ticket:
            # Geschlossenes, zurückgestelltes oder auf Antwort wartendes
            # Ticket bei neuer Antwort automatisch wieder aktivieren --
            # eine Antwort des Absenders beendet jede Form von "wir warten".
            if ticket.status in (TicketStatus.CLOSED, TicketStatus.POSTPONED, TicketStatus.WAITING):
                ticket.status = TicketStatus.ASSIGNED if ticket.assigned_to_id else TicketStatus.ACTIVE
                ticket.closed_at = None
                ticket.postponed_until = None
        else:
            # Spam-Prüfung nur für neue Tickets, nicht für Antworten auf
            # bestehende – spart unnötige (ggf. kostenpflichtige) externe Aufrufe.
            spam_result = await pruefe_auf_spam(mail["from_email"], mail["subject"], mail["text"], db)

            matches = await find_members_by_email(db, mail["from_email"])
            member_id = matches[0].id if len(matches) == 1 else None

            ticket = Ticket(
                subject=mail["subject"],
                sender_email=mail["from_email"],
                sender_name=mail["from_name"] or None,
                member_id=member_id,
                spam_suspected=spam_result.ist_spam_verdacht,
                spam_score=spam_result.score,
                spam_reasoning=spam_result.begruendung,
            )
            db.add(ticket)
            await db.flush()

        db.add(TicketMessage(
            ticket_id=ticket.id,
            direction=MessageDirection.INCOMING,
            content=mail["text"] or "(kein Textinhalt)",
            message_id=mail["message_id"] or None,
            in_reply_to=mail["in_reply_to"] or None,
        ))
        processed += 1

    await _write_setting(db, _KEY_LAST_UID, str(highest_uid) if highest_uid else None)
    await _write_setting(db, _KEY_LAST_FETCH, datetime.now(timezone.utc).isoformat())
    await _write_setting(db, _KEY_LAST_ERROR, None)
    await db.commit()

    return processed


async def send_ticket_reply(ticket: Ticket, content: str, db: AsyncSession) -> Optional[str]:
    """
    Sendet eine Antwort auf ein Ticket per E-Mail an den Absender, über das
    konfigurierte Ticket-Postfach. Gibt die generierte Message-ID zurück
    (zum Speichern auf der TicketMessage, für künftiges Threading), oder
    None, falls das Postfach nicht konfiguriert ist oder der Versand fehlschlug.
    """
    config = await load_inbox_configuration(db)
    if not is_inbox_configured(config):
        logger.warning("Ticket-Postfach nicht konfiguriert – Antwort wird nicht per E-Mail versendet.")
        return None

    last_incoming = next(
        (m for m in reversed(ticket.messages) if m.direction == MessageDirection.INCOMING and m.message_id),
        None
    )

    new_message_id = make_msgid()

    subject = ticket.subject
    if not subject.lower().startswith("re:"):
        subject = f"Re: {subject}"

    msg = MIMEText(content, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = config["smtp_from"]
    msg["To"] = ticket.sender_email
    msg["Message-ID"] = new_message_id
    if last_incoming:
        msg["In-Reply-To"] = last_incoming.message_id
        msg["References"] = last_incoming.message_id

    try:
        await aiosmtplib.send(
            msg,
            hostname=config["smtp_host"],
            port=config["smtp_port"],
            username=config["smtp_user"],
            password=config["smtp_password"],
            start_tls=config["smtp_tls"],
        )
        return new_message_id
    except Exception as e:
        logger.error(f"Ticket-Antwort konnte nicht gesendet werden: {e}")
        return None


async def inbox_status(db: AsyncSession) -> Dict[str, Optional[str]]:
    """Betriebsstatus für die Anzeige in der Oberfläche (letzter Abruf, letzter Fehler)."""
    return {
        "letzter_abruf": await _read_setting(db, _KEY_LAST_FETCH),
        "letzter_fehler": await _read_setting(db, _KEY_LAST_ERROR),
    }
