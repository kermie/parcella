"""
API-Router: Ticketsystem – Tickets, Nachrichten, Zuweisung, Status.
"""
from datetime import date, datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models import Ticket, TicketNachricht, TicketStatus, NachrichtRichtung, Benutzer
from app.api_auth import get_current_api_user, require_schreibzugriff
from app.module_flags import require_modul
from app.ticket_utils import finde_mitglieder_per_email
from app.email_service import sende_email
from app.schemas import (
    TicketCreate, TicketOut, TicketDetailOut, TicketStatusUpdate,
    TicketZuweisungUpdate, TicketMitgliedUpdate,
    TicketNachrichtCreate, TicketNachrichtOut,
)

router = APIRouter(
    prefix="/api/v1/tickets",
    tags=["API: Tickets"],
    dependencies=[Depends(require_modul("tickets"))],
)


async def _lade_ticket(db: AsyncSession, ticket_id: str) -> Optional[Ticket]:
    result = await db.execute(
        select(Ticket)
        .options(selectinload(Ticket.nachrichten))
        .where(Ticket.id == ticket_id)
    )
    return result.scalar_one_or_none()


@router.get("", response_model=List[TicketOut], summary="Tickets auflisten")
async def tickets_auflisten(
    status_filter: Optional[str] = Query(None, alias="status"),
    zugewiesen_an_id: Optional[str] = Query(None),
    suche: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    benutzer: Benutzer = Depends(get_current_api_user),
):
    query = select(Ticket).order_by(Ticket.erstellt_am.desc()).limit(limit).offset(offset)

    if status_filter:
        query = query.where(Ticket.status == TicketStatus(status_filter))
    if zugewiesen_an_id:
        query = query.where(Ticket.zugewiesen_an_id == zugewiesen_an_id)
    if suche:
        query = query.where(
            or_(Ticket.betreff.ilike(f"%{suche}%"), Ticket.absender_email.ilike(f"%{suche}%"))
        )

    result = await db.execute(query)
    return result.scalars().all()


@router.get("/{ticket_id}", response_model=TicketDetailOut, summary="Ticket inkl. Verlauf abrufen")
async def ticket_abrufen(
    ticket_id: str,
    db: AsyncSession = Depends(get_db),
    benutzer: Benutzer = Depends(get_current_api_user),
):
    ticket = await _lade_ticket(db, ticket_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket nicht gefunden")
    return ticket


@router.post(
    "", response_model=TicketDetailOut, status_code=status.HTTP_201_CREATED,
    summary="Ticket anlegen",
    description="Legt ein Ticket mit erster Nachricht an. Der Absender wird automatisch "
                "einem Mitglied zugeordnet, falls die E-Mail-Adresse eindeutig einem "
                "Mitglied zugeordnet werden kann.",
)
async def ticket_erstellen(
    daten: TicketCreate,
    db: AsyncSession = Depends(get_db),
    benutzer: Benutzer = Depends(require_schreibzugriff),
):
    email = str(daten.absender_email).lower()
    treffer = await finde_mitglieder_per_email(db, email)
    mitglied_id = treffer[0].id if len(treffer) == 1 else None

    ticket = Ticket(
        betreff=daten.betreff, absender_email=email,
        absender_name=daten.absender_name, mitglied_id=mitglied_id,
    )
    db.add(ticket)
    await db.flush()

    db.add(TicketNachricht(ticket_id=ticket.id, richtung=NachrichtRichtung.EINGEHEND, inhalt=daten.nachricht))
    await db.commit()

    return await _lade_ticket(db, ticket.id)


@router.put(
    "/{ticket_id}/status", response_model=TicketOut, summary="Ticket-Status ändern",
    description="ZURUECKGESTELLT erfordert zurueckgestellt_bis. GESCHLOSSEN setzt geschlossen_am automatisch.",
)
async def status_aendern(
    ticket_id: str,
    daten: TicketStatusUpdate,
    db: AsyncSession = Depends(get_db),
    benutzer: Benutzer = Depends(require_schreibzugriff),
):
    result = await db.execute(select(Ticket).where(Ticket.id == ticket_id))
    ticket = result.scalar_one_or_none()
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket nicht gefunden")

    neuer_status = TicketStatus(daten.status)

    if neuer_status == TicketStatus.ZURUECKGESTELLT and not daten.zurueckgestellt_bis:
        raise HTTPException(status_code=422, detail="zurueckgestellt_bis ist bei Status ZURUECKGESTELLT erforderlich")

    ticket.status = neuer_status
    ticket.zurueckgestellt_bis = daten.zurueckgestellt_bis if neuer_status == TicketStatus.ZURUECKGESTELLT else None
    ticket.geschlossen_am = datetime.now(timezone.utc) if neuer_status == TicketStatus.GESCHLOSSEN else None
    if neuer_status == TicketStatus.NICHT_ZUGEWIESEN:
        ticket.zugewiesen_an_id = None

    await db.commit()
    await db.refresh(ticket)
    return ticket


@router.put(
    "/{ticket_id}/zuweisung", response_model=TicketOut, summary="Ticket zuweisen/Zuweisung aufheben",
    description="Löst bei Zuweisung eine E-Mail-Benachrichtigung an den zugewiesenen Benutzer aus.",
)
async def zuweisung_aendern(
    ticket_id: str,
    daten: TicketZuweisungUpdate,
    db: AsyncSession = Depends(get_db),
    benutzer: Benutzer = Depends(require_schreibzugriff),
):
    result = await db.execute(select(Ticket).where(Ticket.id == ticket_id))
    ticket = result.scalar_one_or_none()
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket nicht gefunden")

    if daten.benutzer_id:
        zugewiesener_result = await db.execute(select(Benutzer).where(Benutzer.id == daten.benutzer_id))
        zugewiesener = zugewiesener_result.scalar_one_or_none()
        if not zugewiesener:
            raise HTTPException(status_code=404, detail="Benutzer nicht gefunden")

        ticket.zugewiesen_an_id = zugewiesener.id
        ticket.status = TicketStatus.ZUGEWIESEN
        await db.commit()
        await db.refresh(ticket)

        betreff = f"Ticket zugewiesen: {ticket.betreff}"
        html = (
            f"<html><body><p>Hallo {zugewiesener.name},</p>"
            f"<p>Ihnen wurde ein Ticket im Gartenmanager zugewiesen:</p>"
            f"<p><strong>{ticket.betreff}</strong></p>"
            f"<p>Bitte melden Sie sich im Gartenmanager an, um es zu bearbeiten.</p></body></html>"
        )
        await sende_email(zugewiesener.email, betreff, html, db=db)
    else:
        ticket.zugewiesen_an_id = None
        ticket.status = TicketStatus.NICHT_ZUGEWIESEN
        await db.commit()
        await db.refresh(ticket)

    return ticket


@router.put("/{ticket_id}/mitglied", response_model=TicketOut, summary="Mitglied-Zuordnung setzen")
async def mitglied_zuordnen(
    ticket_id: str,
    daten: TicketMitgliedUpdate,
    db: AsyncSession = Depends(get_db),
    benutzer: Benutzer = Depends(require_schreibzugriff),
):
    result = await db.execute(select(Ticket).where(Ticket.id == ticket_id))
    ticket = result.scalar_one_or_none()
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket nicht gefunden")

    ticket.mitglied_id = daten.mitglied_id
    await db.commit()
    await db.refresh(ticket)
    return ticket


@router.get(
    "/{ticket_id}/nachrichten", response_model=List[TicketNachrichtOut],
    summary="Nachrichten eines Tickets auflisten",
)
async def nachrichten_auflisten(
    ticket_id: str,
    db: AsyncSession = Depends(get_db),
    benutzer: Benutzer = Depends(get_current_api_user),
):
    result = await db.execute(
        select(TicketNachricht).where(TicketNachricht.ticket_id == ticket_id).order_by(TicketNachricht.erstellt_am)
    )
    return result.scalars().all()


@router.post(
    "/{ticket_id}/nachrichten", response_model=TicketNachrichtOut, status_code=status.HTTP_201_CREATED,
    summary="Nachricht/Notiz hinzufügen",
    description="richtung=INTERN für interne Notizen (nie an den Absender gesendet). "
                "Der tatsächliche E-Mail-Versand für AUSGEHEND folgt in Etappe 2.",
)
async def nachricht_erstellen(
    ticket_id: str,
    daten: TicketNachrichtCreate,
    db: AsyncSession = Depends(get_db),
    benutzer: Benutzer = Depends(require_schreibzugriff),
):
    ticket_result = await db.execute(select(Ticket).where(Ticket.id == ticket_id))
    if not ticket_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Ticket nicht gefunden")

    nachricht = TicketNachricht(
        ticket_id=ticket_id, richtung=NachrichtRichtung(daten.richtung),
        inhalt=daten.inhalt, verfasst_von_id=benutzer.id,
    )
    db.add(nachricht)
    await db.commit()
    await db.refresh(nachricht)
    return nachricht
