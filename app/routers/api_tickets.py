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
from app.models import Ticket, TicketMessage, TicketStatus, MessageDirection, User
from app.api_auth import get_current_api_user, require_write_access
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


@router.get("", response_model=List[TicketOut], summary="List tickets")
async def tickets_list(
    status_filter: Optional[str] = Query(None, alias="status"),
    assigned_to_id: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_api_user),
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


@router.get("/{ticket_id}", response_model=TicketDetailOut, summary="Retrieve ticket incl. history")
async def ticket_get(
    ticket_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_api_user),
):
    ticket = await _load_ticket(db, ticket_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket nicht gefunden")
    return ticket


@router.post(
    "", response_model=TicketDetailOut, status_code=status.HTTP_201_CREATED,
    summary="Create ticket",
    description="Creates a ticket with a first message. The sender is automatically "
                "linked to a member if the email address can be uniquely matched "
                "to a member.",
)
async def ticket_create(
    daten: TicketCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_write_access),
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
    "/{ticket_id}/status", response_model=TicketOut, summary="Change ticket status",
    description="POSTPONED requires postponed_until. CLOSED sets closed_at automatically.",
)
async def status_update(
    ticket_id: str,
    daten: TicketStatusUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_write_access),
):
    result = await db.execute(select(Ticket).where(Ticket.id == ticket_id))
    ticket = result.scalar_one_or_none()
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket nicht gefunden")

    neuer_status = TicketStatus(daten.status)

    if neuer_status == TicketStatus.POSTPONED and not daten.postponed_until:
        raise HTTPException(status_code=422, detail="postponed_until ist bei Status POSTPONED erforderlich")

    ticket.status = neuer_status
    ticket.postponed_until = daten.postponed_until if neuer_status == TicketStatus.POSTPONED else None
    ticket.closed_at = datetime.now(timezone.utc) if neuer_status == TicketStatus.CLOSED else None
    if neuer_status == TicketStatus.ACTIVE:
        ticket.assigned_to_id = None

    await db.commit()
    await db.refresh(ticket)
    return ticket


@router.put(
    "/{ticket_id}/assignment", response_model=TicketOut, summary="Assign ticket / clear assignment",
    description="Triggers an email notification to the assigned user upon assignment.",
)
async def assignment_update(
    ticket_id: str,
    daten: TicketAssignmentUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_write_access),
):
    result = await db.execute(select(Ticket).where(Ticket.id == ticket_id))
    ticket = result.scalar_one_or_none()
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket nicht gefunden")

    if daten.assigned_to_id:
        assignee_result = await db.execute(select(User).where(User.id == daten.assigned_to_id))
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
        ticket.status = TicketStatus.ACTIVE
        await db.commit()
        await db.refresh(ticket)

    return ticket


@router.put("/{ticket_id}/member", response_model=TicketOut, summary="Set member assignment")
async def member_assign(
    ticket_id: str,
    daten: TicketMemberUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_write_access),
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
    "/{ticket_id}/spam-status", response_model=TicketOut, summary="Set/clear spam suspicion",
    description="Primarily used to mark an automatically detected spam suspicion as "
                "a false positive (spam_suspected=false).",
)
async def spam_status_update(
    ticket_id: str,
    daten: TicketSpamUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_write_access),
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
    summary="List messages of a ticket",
)
async def messages_list(
    ticket_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_api_user),
):
    result = await db.execute(
        select(TicketMessage).where(TicketMessage.ticket_id == ticket_id).order_by(TicketMessage.created_at)
    )
    return result.scalars().all()


@router.post(
    "/{ticket_id}/messages", response_model=TicketMessageOut, status_code=status.HTTP_201_CREATED,
    summary="Add message/note",
    description="direction=INTERNAL for internal notes (never sent to the sender). "
                "Actual email delivery for OUTGOING will follow in stage 2.",
)
async def message_create(
    ticket_id: str,
    daten: TicketMessageCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_write_access),
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
        content=daten.content, authored_by_id=user.id, message_id=message_id,
    )
    db.add(message)
    await db.commit()
    await db.refresh(message)
    return message
