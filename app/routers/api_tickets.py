"""
API router: Ticket system -- tickets, messages, assignment, status.

Business logic shared with app/routers/tickets.py (HTML) lives in
app/services/tickets.py (ADR 0070) -- this router owns bearer-token
authentication, the fine-grained permission check (require_api_permission,
Group-based like the HTML side -- ADR 0070, not the coarser role-only
require_write_access other API routers still use), Pydantic body
parsing, and JSON response serialization.
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models import Ticket, TicketMessage, TicketStatus, MessageDirection, User
from app.api_auth import require_api_permission
from app.module_flags import require_module
from app.services.errors import ServiceError
from app.services.tickets import (
    create_ticket, change_status, assign_ticket, set_member, set_spam_status, add_message,
)
from app.i18n import t_for, DEFAULT_LANGUAGE
from app.schemas import (
    TicketCreate, TicketOut, TicketDetailOut, TicketStatusUpdate,
    TicketAssignmentUpdate, TicketMemberUpdate, TicketSpamUpdate,
    TicketMessageCreate, TicketMessageOut,
)

router = APIRouter(
    prefix="/api/v1/tickets",
    tags=["API: Tickets"],
    dependencies=[Depends(require_module("tickets"))],
)


def _lang(request: Request) -> str:
    return getattr(request.state, "language", DEFAULT_LANGUAGE)


def _service_error_to_http(request: Request, e: ServiceError) -> HTTPException:
    # Deliberately always 422 here, independent of e.http_status (which
    # is the HTML side's 400) -- 422 is this API's existing convention
    # for "well-formed request, business-rule violation" (see
    # api_metering.py), so the status code doesn't change, only the
    # previously-hard-coded-English detail text now shares the HTML
    # side's i18n key (ADR 0070).
    return HTTPException(status_code=422, detail=t_for(request, e.key, **e.params))


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
    user: User = Depends(require_api_permission("tickets", "read")),
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
    user: User = Depends(require_api_permission("tickets", "read")),
):
    ticket = await _load_ticket(db, ticket_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
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
    user: User = Depends(require_api_permission("tickets", "write")),
):
    ticket = await create_ticket(
        db, subject=daten.subject, sender_email=str(daten.sender_email),
        sender_name=daten.sender_name, message=daten.message,
    )
    await db.commit()
    return await _load_ticket(db, ticket.id)


@router.put(
    "/{ticket_id}/status", response_model=TicketOut, summary="Change ticket status",
    description="POSTPONED requires postponed_until. CLOSED sets closed_at automatically.",
)
async def status_update(
    ticket_id: str,
    daten: TicketStatusUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_api_permission("tickets", "write")),
):
    result = await db.execute(select(Ticket).where(Ticket.id == ticket_id))
    ticket = result.scalar_one_or_none()
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    try:
        await change_status(db, ticket, TicketStatus(daten.status), daten.postponed_until, acting_user=user)
    except ServiceError as e:
        raise _service_error_to_http(request, e)

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
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_api_permission("tickets", "write")),
):
    result = await db.execute(select(Ticket).where(Ticket.id == ticket_id))
    ticket = result.scalar_one_or_none()
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    assignee = None
    if daten.assigned_to_id:
        assignee_result = await db.execute(select(User).where(User.id == daten.assigned_to_id))
        assignee = assignee_result.scalar_one_or_none()
        if not assignee:
            raise HTTPException(status_code=404, detail="User not found")

    await assign_ticket(db, ticket, assignee, acting_user=user, lang=_lang(request))
    await db.commit()
    await db.refresh(ticket)
    return ticket


@router.put("/{ticket_id}/member", response_model=TicketOut, summary="Set member assignment")
async def member_assign(
    ticket_id: str,
    daten: TicketMemberUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_api_permission("tickets", "write")),
):
    result = await db.execute(select(Ticket).where(Ticket.id == ticket_id))
    ticket = result.scalar_one_or_none()
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    set_member(ticket, daten.member_id)
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
    user: User = Depends(require_api_permission("tickets", "write")),
):
    result = await db.execute(select(Ticket).where(Ticket.id == ticket_id))
    ticket = result.scalar_one_or_none()
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    set_spam_status(ticket, daten.spam_suspected, acting_user=user)
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
    user: User = Depends(require_api_permission("tickets", "read")),
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
    user: User = Depends(require_api_permission("tickets", "write")),
):
    ticket_result = await db.execute(
        select(Ticket).options(selectinload(Ticket.messages)).where(Ticket.id == ticket_id)
    )
    ticket = ticket_result.scalar_one_or_none()
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    direction = MessageDirection(daten.direction)
    message = await add_message(db, ticket, daten.content, direction, acting_user=user)
    await db.commit()
    await db.refresh(message)
    return message
