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

from app.models import Vereinseinstellung, Ticket, TicketNachricht, TicketStatus, NachrichtRichtung
from app.email_service import lade_smtp_konfiguration
from app.ticket_utils import finde_mitglieder_per_email
from app.spam_filter import pruefe_auf_spam

logger = logging.getLogger(__name__)

# Betriebsdaten-Schlüssel in der vereinseinstellungen-Tabelle
_SCHLUESSEL_LETZTE_UID = "ticket_imap_letzte_uid"
_SCHLUESSEL_LETZTER_ABRUF = "ticket_imap_letzter_abruf"
_SCHLUESSEL_LETZTER_FEHLER = "ticket_imap_letzter_fehler"


async def _lese_einstellung(db: AsyncSession, schluessel: str) -> Optional[str]:
    result = await db.execute(select(Vereinseinstellung).where(Vereinseinstellung.schluessel == schluessel))
    eintrag = result.scalar_one_or_none()
    return eintrag.wert if eintrag else None


async def _schreibe_einstellung(db: AsyncSession, schluessel: str, wert: Optional[str]) -> None:
    result = await db.execute(select(Vereinseinstellung).where(Vereinseinstellung.schluessel == schluessel))
    eintrag = result.scalar_one_or_none()
    if eintrag:
        eintrag.wert = wert
    else:
        db.add(Vereinseinstellung(schluessel=schluessel, wert=wert))


async def lade_postfach_konfiguration(db: AsyncSession) -> Dict[str, Any]:
    """
    Lädt die Postfach-Konfiguration: SMTP-Zugangsdaten kommen aus der
    bereits bestehenden allgemeinen E-Mail-Konfiguration (dasselbe Postfach
    wie für Einladungen) – nur IMAP-Host/-Port/-SSL sind ticketspezifisch,
    da die allgemeine Konfiguration nur zum Senden gedacht ist.
    """
    smtp_konfig = await lade_smtp_konfiguration(db)

    result = await db.execute(
        select(Vereinseinstellung).where(
            Vereinseinstellung.schluessel.in_(["imap_host", "imap_port", "imap_ssl"])
        )
    )
    gespeichert = {e.schluessel: e.wert for e in result.scalars().all() if e.wert}

    def _bool(wert, default=True) -> bool:
        if wert is None:
            return default
        return str(wert).strip().lower() in ("true", "1", "ja", "an")

    return {
        "imap_host": gespeichert.get("imap_host", ""),
        "imap_port": int(gespeichert.get("imap_port") or 993),
        "imap_user": smtp_konfig["user"],       # dasselbe Postfach wie SMTP
        "imap_password": smtp_konfig["password"],
        "imap_ssl": _bool(gespeichert.get("imap_ssl"), True),
        "smtp_host": smtp_konfig["host"],
        "smtp_port": smtp_konfig["port"],
        "smtp_user": smtp_konfig["user"],
        "smtp_password": smtp_konfig["password"],
        "smtp_tls": smtp_konfig["tls"],
        "smtp_from": smtp_konfig["from"],
    }


def ist_postfach_konfiguriert(konfig: Dict[str, Any]) -> bool:
    return bool(konfig["imap_host"] and konfig["smtp_host"] and konfig["smtp_user"])


def _dekodiere_header(wert: Optional[str]) -> str:
    if not wert:
        return ""
    teile = decode_header(wert)
    ergebnis = ""
    for text, kodierung in teile:
        if isinstance(text, bytes):
            ergebnis += text.decode(kodierung or "utf-8", errors="replace")
        else:
            ergebnis += text
    return ergebnis


def _extrahiere_text(nachricht) -> str:
    """Bevorzugt text/plain, fällt auf grob bereinigtes text/html zurück."""
    if nachricht.is_multipart():
        for teil in nachricht.walk():
            if teil.get_content_type() == "text/plain" and not teil.get("Content-Disposition"):
                payload = teil.get_payload(decode=True)
                if payload:
                    return payload.decode(teil.get_content_charset() or "utf-8", errors="replace")
        for teil in nachricht.walk():
            if teil.get_content_type() == "text/html" and not teil.get("Content-Disposition"):
                payload = teil.get_payload(decode=True)
                if payload:
                    html = payload.decode(teil.get_content_charset() or "utf-8", errors="replace")
                    return re.sub(r"<[^>]+>", "", html).strip()
        return ""
    else:
        payload = nachricht.get_payload(decode=True)
        if payload:
            return payload.decode(nachricht.get_content_charset() or "utf-8", errors="replace")
        return ""


def _hole_hoechste_uid_sync(konfig: Dict[str, Any]) -> int:
    """
    Ermittelt nur die höchste aktuell vorhandene UID, OHNE eine einzige
    Nachricht abzurufen. Wird für die Erstsynchronisierung genutzt (siehe
    verarbeite_eingehende_mails) – verhindert, dass beim allerersten Abruf
    Tausende bestehende Alt-Mails auf einmal als Tickets importiert werden.
    """
    if konfig["imap_ssl"]:
        verbindung = imaplib.IMAP4_SSL(konfig["imap_host"], konfig["imap_port"])
    else:
        verbindung = imaplib.IMAP4(konfig["imap_host"], konfig["imap_port"])

    try:
        verbindung.login(konfig["imap_user"], konfig["imap_password"])
        verbindung.select("INBOX")
        status, daten = verbindung.uid("search", None, "ALL")
        if status != "OK" or not daten or not daten[0]:
            return 0
        alle_uids = [int(u) for u in daten[0].split()]
        return max(alle_uids) if alle_uids else 0
    finally:
        try:
            verbindung.close()
        except Exception:
            pass
        try:
            verbindung.logout()
        except Exception:
            pass


def _hole_neue_mails_sync(konfig: Dict[str, Any], letzte_uid: Optional[int]) -> List[Dict[str, Any]]:
    """
    Synchrone IMAP-Abfrage (läuft in einem Thread-Executor). Gibt eine Liste
    geparster Nachrichten zurück, jeweils mit uid, message_id, in_reply_to,
    references, von_email, von_name, betreff, text.
    """
    ergebnisse: List[Dict[str, Any]] = []

    if konfig["imap_ssl"]:
        verbindung = imaplib.IMAP4_SSL(konfig["imap_host"], konfig["imap_port"])
    else:
        verbindung = imaplib.IMAP4(konfig["imap_host"], konfig["imap_port"])

    try:
        verbindung.login(konfig["imap_user"], konfig["imap_password"])
        verbindung.select("INBOX")

        if letzte_uid:
            suchbereich = f"{letzte_uid + 1}:*"
        else:
            suchbereich = "1:*"

        status, daten = verbindung.uid("search", None, "ALL")
        if status != "OK" or not daten or not daten[0]:
            return ergebnisse

        alle_uids = [int(u) for u in daten[0].split()]
        neue_uids = [u for u in alle_uids if letzte_uid is None or u > letzte_uid]

        for uid in neue_uids:
            status, msg_daten = verbindung.uid("fetch", str(uid), "(RFC822)")
            if status != "OK" or not msg_daten or not msg_daten[0]:
                continue

            roh = msg_daten[0][1]
            nachricht = email.message_from_bytes(roh)

            von_name, von_email = parseaddr(_dekodiere_header(nachricht.get("From", "")))
            betreff = _dekodiere_header(nachricht.get("Subject", "(ohne Betreff)"))
            message_id = (nachricht.get("Message-ID") or "").strip()
            in_reply_to = (nachricht.get("In-Reply-To") or "").strip()
            references = (nachricht.get("References") or "").strip()

            ergebnisse.append({
                "uid": uid,
                "message_id": message_id,
                "in_reply_to": in_reply_to,
                "references": references,
                "von_email": von_email.strip().lower(),
                "von_name": von_name.strip(),
                "betreff": betreff.strip() or "(ohne Betreff)",
                "text": _extrahiere_text(nachricht).strip(),
            })

        return ergebnisse
    finally:
        try:
            verbindung.close()
        except Exception:
            pass
        try:
            verbindung.logout()
        except Exception:
            pass


def _bereinige_betreff(betreff: str) -> str:
    """Entfernt Antwort-/Weiterleitungs-Präfixe für den Fallback-Betreffvergleich."""
    return re.sub(r"^(re|aw|fwd?)\s*:\s*", "", betreff.strip(), flags=re.IGNORECASE).strip().lower()


async def _finde_passendes_ticket(db: AsyncSession, mail: Dict[str, Any]) -> Optional[Ticket]:
    """Sucht ein bestehendes Ticket für eine eingehende Antwort."""
    # 1. Über Message-ID-Threading (In-Reply-To oder References)
    kandidaten_ids = []
    if mail["in_reply_to"]:
        kandidaten_ids.append(mail["in_reply_to"])
    if mail["references"]:
        kandidaten_ids.extend(mail["references"].split())

    if kandidaten_ids:
        result = await db.execute(
            select(TicketNachricht).where(TicketNachricht.message_id.in_(kandidaten_ids))
        )
        treffer = result.scalars().first()
        if treffer:
            ticket_result = await db.execute(select(Ticket).where(Ticket.id == treffer.ticket_id))
            ticket = ticket_result.scalar_one_or_none()
            if ticket:
                return ticket

    # 2. Fallback: gleicher Absender + ähnlicher Betreff, nicht geschlossen
    bereinigt = _bereinige_betreff(mail["betreff"])
    result = await db.execute(
        select(Ticket).where(
            Ticket.absender_email == mail["von_email"],
            Ticket.status != TicketStatus.GESCHLOSSEN,
        )
    )
    for ticket in result.scalars().all():
        if _bereinige_betreff(ticket.betreff) == bereinigt:
            return ticket

    return None


async def verarbeite_eingehende_mails(db: AsyncSession) -> int:
    """
    Ruft neue E-Mails ab und verarbeitet sie zu Tickets/Nachrichten.
    Gibt die Anzahl neu verarbeiteter Mails zurück. Fehler werden abgefangen
    und in der Datenbank vermerkt, statt den Hintergrundjob abstürzen zu lassen.
    """
    konfig = await lade_postfach_konfiguration(db)
    if not ist_postfach_konfiguriert(konfig):
        return 0

    letzte_uid_str = await _lese_einstellung(db, _SCHLUESSEL_LETZTE_UID)
    erstlauf = letzte_uid_str is None
    letzte_uid = int(letzte_uid_str) if letzte_uid_str else None

    if erstlauf:
        # Beim allerersten Abruf werden bestehende E-Mails NICHT importiert –
        # nur die aktuell höchste UID wird als Startpunkt gemerkt. Sonst
        # würde ein Postfach mit tausenden Alt-Mails auf einen Schlag komplett
        # (und sehr langsam, ggf. mit Verbindungsabbruch) importiert werden.
        try:
            hoechste_uid = await asyncio.to_thread(_hole_hoechste_uid_sync, konfig)
        except Exception as e:
            logger.error(f"IMAP-Erstsynchronisierung fehlgeschlagen: {e}")
            await _schreibe_einstellung(db, _SCHLUESSEL_LETZTER_FEHLER, str(e))
            await db.commit()
            return 0

        await _schreibe_einstellung(db, _SCHLUESSEL_LETZTE_UID, str(hoechste_uid))
        await _schreibe_einstellung(db, _SCHLUESSEL_LETZTER_ABRUF, datetime.now(timezone.utc).isoformat())
        await _schreibe_einstellung(db, _SCHLUESSEL_LETZTER_FEHLER, None)
        await db.commit()
        logger.info(
            f"Ticket-Postfach: Erstsynchronisierung abgeschlossen (UID {hoechste_uid}). "
            f"Bestehende E-Mails wurden übersprungen, ab jetzt werden nur neue E-Mails verarbeitet."
        )
        return 0

    try:
        mails = await asyncio.to_thread(_hole_neue_mails_sync, konfig, letzte_uid)
    except Exception as e:
        logger.error(f"IMAP-Abruf fehlgeschlagen: {e}")
        await _schreibe_einstellung(db, _SCHLUESSEL_LETZTER_FEHLER, str(e))
        await db.commit()
        return 0

    verarbeitet = 0
    hoechste_uid = letzte_uid or 0

    for mail in mails:
        hoechste_uid = max(hoechste_uid, mail["uid"])

        if not mail["von_email"]:
            continue  # unbrauchbare Nachricht (keine Absenderadresse geparst)

        spam_ergebnis = await pruefe_auf_spam(mail["von_email"], mail["betreff"], mail["text"])

        ticket = await _finde_passendes_ticket(db, mail)

        if ticket:
            # Geschlossenes Ticket bei neuer Antwort automatisch wieder öffnen
            if ticket.status == TicketStatus.GESCHLOSSEN:
                ticket.status = TicketStatus.ZUGEWIESEN if ticket.zugewiesen_an_id else TicketStatus.NICHT_ZUGEWIESEN
                ticket.geschlossen_am = None
        else:
            treffer = await finde_mitglieder_per_email(db, mail["von_email"])
            mitglied_id = treffer[0].id if len(treffer) == 1 else None

            ticket = Ticket(
                betreff=mail["betreff"],
                absender_email=mail["von_email"],
                absender_name=mail["von_name"] or None,
                mitglied_id=mitglied_id,
                spam_verdacht=spam_ergebnis.ist_spam_verdacht,
                spam_score=spam_ergebnis.score,
            )
            db.add(ticket)
            await db.flush()

        db.add(TicketNachricht(
            ticket_id=ticket.id,
            richtung=NachrichtRichtung.EINGEHEND,
            inhalt=mail["text"] or "(kein Textinhalt)",
            message_id=mail["message_id"] or None,
            in_reply_to=mail["in_reply_to"] or None,
        ))
        verarbeitet += 1

    await _schreibe_einstellung(db, _SCHLUESSEL_LETZTE_UID, str(hoechste_uid) if hoechste_uid else None)
    await _schreibe_einstellung(db, _SCHLUESSEL_LETZTER_ABRUF, datetime.now(timezone.utc).isoformat())
    await _schreibe_einstellung(db, _SCHLUESSEL_LETZTER_FEHLER, None)
    await db.commit()

    return verarbeitet


async def sende_ticket_antwort(ticket: Ticket, inhalt: str, db: AsyncSession) -> Optional[str]:
    """
    Sendet eine Antwort auf ein Ticket per E-Mail an den Absender, über das
    konfigurierte Ticket-Postfach. Gibt die generierte Message-ID zurück
    (zum Speichern auf der TicketNachricht, für künftiges Threading), oder
    None, falls das Postfach nicht konfiguriert ist oder der Versand fehlschlug.
    """
    konfig = await lade_postfach_konfiguration(db)
    if not ist_postfach_konfiguriert(konfig):
        logger.warning("Ticket-Postfach nicht konfiguriert – Antwort wird nicht per E-Mail versendet.")
        return None

    letzte_eingehende = next(
        (n for n in reversed(ticket.nachrichten) if n.richtung == NachrichtRichtung.EINGEHEND and n.message_id),
        None
    )

    neue_message_id = make_msgid()

    betreff = ticket.betreff
    if not betreff.lower().startswith("re:"):
        betreff = f"Re: {betreff}"

    msg = MIMEText(inhalt, "plain", "utf-8")
    msg["Subject"] = betreff
    msg["From"] = konfig["smtp_from"]
    msg["To"] = ticket.absender_email
    msg["Message-ID"] = neue_message_id
    if letzte_eingehende:
        msg["In-Reply-To"] = letzte_eingehende.message_id
        msg["References"] = letzte_eingehende.message_id

    try:
        await aiosmtplib.send(
            msg,
            hostname=konfig["smtp_host"],
            port=konfig["smtp_port"],
            username=konfig["smtp_user"],
            password=konfig["smtp_password"],
            start_tls=konfig["smtp_tls"],
        )
        return neue_message_id
    except Exception as e:
        logger.error(f"Ticket-Antwort konnte nicht gesendet werden: {e}")
        return None


async def postfach_status(db: AsyncSession) -> Dict[str, Optional[str]]:
    """Betriebsstatus für die Anzeige in der Oberfläche (letzter Abruf, letzter Fehler)."""
    return {
        "letzter_abruf": await _lese_einstellung(db, _SCHLUESSEL_LETZTER_ABRUF),
        "letzter_fehler": await _lese_einstellung(db, _SCHLUESSEL_LETZTER_FEHLER),
    }
