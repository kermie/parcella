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
from app.models import Ticket, TicketMessage, TicketStatus, MessageDirection, Benutzer
from app.api_auth import get_current_api_user, require_schreibzugriff
from app.module_flags import require_modul
from app.ticket_utils import find_members_by_email
from app.ticket_mailer import send_ticket_reply
from app.email_service import sende_email
from app.schemas import (
    TicketCreate, TicketOut, TicketDetailOut, TicketStatusUpdate,
    TicketAssignmentUpdate, TicketMemberUpdate, TicketSpamUpdate,
    TicketMessageCreate, TicketMessageOut,
)

router = APIRouter(
    prefix="/api/v1/tickets",
    tags=["API: Tickets"],
    dependencies=[Depends(require_modul("tickets"))],
)


async def _load_ticket(db: AsyncSession, ticket_id: str) -> Optional[Ticket]:
    result = await db.execute(
        select(Ticket)
        .options(selectinload(Ticket.messages))
        .where(Ticket.id == ticket_id)
    )
    return result.scalar_one_or_none()


@router.get("", response_model=List[TicketOut], summary="Tickets auflisten")
async def tickets_list(
    status_filter: Optional[str] = Query(None, alias="status"),
    assigned_to_id: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    benutzer: Benutzer = Depends(get_current_api_user),
):
    query = select(Ticket).order_by(Ticket.created_at.desc()).limit(limit).offset(offset)

    if status_filter:
        query = query.where(Ticket.status == TicketStatus(status_filter))
    if assigned_to_id:
        query = query.where(Ticket.assigned_to_id == assigned_to_id)
    if search:
        query = query.where(
            or_(Ticket.subject.ilike(f"%{search}%"), Ticket.sender_email.ilike(f"%{search}%"))
        )

    result = await db.execute(query)
    return result.scalars().all()


@router.get("/{ticket_id}", response_model=TicketDetailOut, summary="Ticket inkl. Verlauf abrufen")
async def ticket_get(
    ticket_id: str,
    db: AsyncSession = Depends(get_db),
    benutzer: Benutzer = Depends(get_current_api_user),
):
    ticket = await _load_ticket(db, ticket_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket nicht gefunden")
    return ticket


@router.post(
    "", response_model=TicketDetailOut, status_code=status.HTTP_201_CREATED,
    summary="Ticket anlegen",
    description="Legt ein Ticket mit erster Nachricht an. Der Absender wird automatisch "
                "einem Member zugeordnet, falls die E-Mail-Adresse eindeutig einem "
                "Member zugeordnet werden kann.",
)
async def ticket_create(
    daten: TicketCreate,
    db: AsyncSession = Depends(get_db),
    benutzer: Benutzer = Depends(require_schreibzugriff),
):
    email = str(daten.sender_email).lower()
    matches = await find_members_by_email(db, email)
    member_id = matches[0].id if len(matches) == 1 else None

    ticket = Ticket(
        subject=daten.subject, sender_email=email,
        sender_name=daten.sender_name, member_id=member_id,
    )
    db.add(ticket)
    await db.flush()

    db.add(TicketMessage(ticket_id=ticket.id, direction=MessageDirection.INCOMING, content=daten.message))
    await db.commit()

    return await _load_ticket(db, ticket.id)


@router.put(
    "/{ticket_id}/status", response_model=TicketOut, summary="Ticket-Status ändern",
    description="DEFERRED erfordert deferred_until. CLOSED setzt closed_at automatisch.",
)
async def status_update(
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

    if neuer_status == TicketStatus.DEFERRED and not daten.deferred_until:
        raise HTTPException(status_code=422, detail="deferred_until ist bei Status DEFERRED erforderlich")

    ticket.status = neuer_status
    ticket.deferred_until = daten.deferred_until if neuer_status == TicketStatus.DEFERRED else None
    ticket.closed_at = datetime.now(timezone.utc) if neuer_status == TicketStatus.CLOSED else None
    if neuer_status == TicketStatus.UNASSIGNED:
        ticket.assigned_to_id = None

    await db.commit()
    await db.refresh(ticket)
    return ticket


@router.put(
    "/{ticket_id}/assignment", response_model=TicketOut, summary="Ticket zuweisen/Zuweisung aufheben",
    description="Löst bei Zuweisung eine E-Mail-Benachrichtigung an den zugewiesenen Benutzer aus.",
)
async def assignment_update(
    ticket_id: str,
    daten: TicketAssignmentUpdate,
    db: AsyncSession = Depends(get_db),
    benutzer: Benutzer = Depends(require_schreibzugriff),
):
    result = await db.execute(select(Ticket).where(Ticket.id == ticket_id))
    ticket = result.scalar_one_or_none()
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket nicht gefunden")

    if daten.assigned_to_id:
        assignee_result = await db.execute(select(Benutzer).where(Benutzer.id == daten.assigned_to_id))
        assignee = assignee_result.scalar_one_or_none()
        if not assignee:
            raise HTTPException(status_code=404, detail="Benutzer nicht gefunden")

        ticket.assigned_to_id = assignee.id
        ticket.status = TicketStatus.ASSIGNED
        await db.commit()
        await db.refresh(ticket)

        subject = f"Ticket zugewiesen: {ticket.subject}"
        html = (
            f"<html><body><p>Hallo {assignee.name},</p>"
            f"<p>Ihnen wurde ein Ticket im Gartenmanager zugewiesen:</p>"
            f"<p><strong>{ticket.subject}</strong></p>"
            f"<p>Bitte melden Sie sich im Gartenmanager an, um es zu bearbeiten.</p></body></html>"
        )
        await sende_email(assignee.email, subject, html, db=db)
    else:
        ticket.assigned_to_id = None
        ticket.status = TicketStatus.UNASSIGNED
        await db.commit()
        await db.refresh(ticket)

    return ticket


@router.put("/{ticket_id}/member", response_model=TicketOut, summary="Member-Zuordnung setzen")
async def member_assign(
    ticket_id: str,
    daten: TicketMemberUpdate,
    db: AsyncSession = Depends(get_db),
    benutzer: Benutzer = Depends(require_schreibzugriff),
):
    result = await db.execute(select(Ticket).where(Ticket.id == ticket_id))
    ticket = result.scalar_one_or_none()
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket nicht gefunden")

    ticket.member_id = daten.member_id
    await db.commit()
    await db.refresh(ticket)
    return ticket


@router.put(
    "/{ticket_id}/spam-status", response_model=TicketOut, summary="Spam-Verdacht setzen/aufheben",
    description="Wird primär genutzt, um einen automatisch erkannten Spam-Verdacht als "
                "falsch-positiv zu markieren (spam_suspected=false).",
)
async def spam_status_update(
    ticket_id: str,
    daten: TicketSpamUpdate,
    db: AsyncSession = Depends(get_db),
    benutzer: Benutzer = Depends(require_schreibzugriff),
):
    result = await db.execute(select(Ticket).where(Ticket.id == ticket_id))
    ticket = result.scalar_one_or_none()
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket nicht gefunden")

    ticket.spam_suspected = daten.spam_suspected
    await db.commit()
    await db.refresh(ticket)
    return ticket


@router.get(
    "/{ticket_id}/messages", response_model=List[TicketMessageOut],
    summary="Nachrichten eines Tickets auflisten",
)
async def messages_list(
    ticket_id: str,
    db: AsyncSession = Depends(get_db),
    benutzer: Benutzer = Depends(get_current_api_user),
):
    result = await db.execute(
        select(TicketMessage).where(TicketMessage.ticket_id == ticket_id).order_by(TicketMessage.created_at)
    )
    return result.scalars().all()


@router.post(
    "/{ticket_id}/messages", response_model=TicketMessageOut, status_code=status.HTTP_201_CREATED,
    summary="Nachricht/Notiz hinzufügen",
    description="direction=INTERNAL für interne Notizen (nie an den Absender gesendet). "
                "Der tatsächliche E-Mail-Versand für OUTGOING folgt in Etappe 2.",
)
async def message_create(
    ticket_id: str,
    daten: TicketMessageCreate,
    db: AsyncSession = Depends(get_db),
    benutzer: Benutzer = Depends(require_schreibzugriff),
):
    ticket_result = await db.execute(
        select(Ticket).options(selectinload(Ticket.messages)).where(Ticket.id == ticket_id)
    )
    ticket = ticket_result.scalar_one_or_none()
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket nicht gefunden")

    direction = MessageDirection(daten.direction)
    message_id = None
    if direction == MessageDirection.OUTGOING:
        message_id = await send_ticket_reply(ticket, daten.content, db)

    message = TicketMessage(
        ticket_id=ticket_id, direction=direction,
        content=daten.content, authored_by_id=benutzer.id, message_id=message_id,
    )
    db.add(message)
    await db.commit()
    await db.refresh(message)
    return message
