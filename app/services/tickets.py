"""
Shared ticket business logic, called by both app/routers/tickets.py
(HTML) and app/routers/api_tickets.py (API) -- see ADR 0070.

Pilot module for the shared-service-layer pattern: extracted here are
the query filter, status-transition rule (including the audit-trail
write, previously HTML-only), assignment + its notification email
(previously hard-coded English on the API side), member linking, and
spam-status changes. Callers are responsible for authentication and the
fine-grained permission check (still at the router boundary -- see
app/permissions.py / app/api_auth.py's require_api_permission), for
parsing their own transport's input, and for the final db.commit() --
functions here call db.flush() only, so a router can batch several
calls (bulk operations) into one commit.
"""
from datetime import date, datetime, timezone
from typing import List, Optional

from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.change_tracker import ChangeTracker
from app.config import settings
from app.email_service import send_email
from app.i18n import translate
from app.models import MessageDirection, Ticket, TicketMessage, TicketStatus, User
from app.services.errors import ServiceError
from app.ticket_mailer import send_ticket_reply
from app.ticket_utils import find_members_by_email


def filtered_tickets_query(filter: str, search: str, user_id: str):
    """Shared between the ticket overview (first page) and the JSON
    endpoint its infinite scroll polls for every page after that --
    both must agree on exactly which tickets match the current filter/
    search, and on a stable order (created_at ties broken by id,
    otherwise offset-based paging can skip or repeat rows)."""
    query = select(Ticket).options(selectinload(Ticket.assigned_to), selectinload(Ticket.member))

    # "Active" and "Mine" deliberately show ONLY operationally open
    # tickets (ACTIVE/ASSIGNED/WAITING) -- POSTPONED tickets are
    # intentionally invisible until their date. DELETED never appears
    # in any view (soft-delete, no trash view built).
    open_statuses = [TicketStatus.ACTIVE, TicketStatus.ASSIGNED, TicketStatus.WAITING]

    if filter == "active":
        query = query.where(Ticket.status.in_(open_statuses), Ticket.spam_suspected == False)
    elif filter == "mine":
        query = query.where(
            Ticket.assigned_to_id == user_id, Ticket.status.in_(open_statuses)
        )
    elif filter == "waiting":
        query = query.where(Ticket.status == TicketStatus.WAITING)
    elif filter == "postponed":
        query = query.where(Ticket.status == TicketStatus.POSTPONED)
    elif filter == "closed":
        query = query.where(Ticket.status == TicketStatus.CLOSED)
    elif filter == "spam":
        query = query.where(Ticket.spam_suspected == True, Ticket.status != TicketStatus.DELETED)
    elif filter == "all":
        query = query.where(Ticket.status != TicketStatus.DELETED)

    if search:
        query = query.where(
            or_(
                Ticket.subject.ilike(f"%{search}%"),
                Ticket.sender_email.ilike(f"%{search}%"),
                Ticket.sender_name.ilike(f"%{search}%"),
            )
        )

    return query.order_by(Ticket.created_at.desc(), Ticket.id.desc())


async def create_ticket(
    db: AsyncSession, *, subject: str, sender_email: str, sender_name: Optional[str], message: str,
) -> Ticket:
    """Creates a ticket with its first (INCOMING) message, auto-linking
    the sender to a member on a unique email match. Caller commits."""
    sender_email = sender_email.strip().lower()
    matches = await find_members_by_email(db, sender_email)
    member_id = matches[0].id if len(matches) == 1 else None

    ticket = Ticket(
        subject=subject.strip(),
        sender_email=sender_email,
        sender_name=(sender_name or "").strip() or None,
        member_id=member_id,
    )
    db.add(ticket)
    await db.flush()

    db.add(TicketMessage(
        ticket_id=ticket.id, direction=MessageDirection.INCOMING, content=message.strip(),
    ))
    await db.flush()
    return ticket


async def change_status(
    db: AsyncSession,
    ticket: Ticket,
    new_status: TicketStatus,
    postponed_until: Optional[date],
    *,
    acting_user: User,
) -> None:
    """
    Sets the new status on a ticket including side effects
    (postponed_until, closed_at, assigned_to_id) and writes the audit
    trail -- shared by single-ticket and bulk status changes, HTML and
    API alike, so all four call sites are guaranteed to apply the same
    rule and none can skip the ChangeHistory write (ADR 0070 -- API-
    driven ticket changes previously left no audit trail at all).
    """
    if new_status == TicketStatus.ASSIGNED:
        raise ServiceError("errors.ticket_status_assigned_manual", http_status=400)

    tracker = ChangeTracker(ticket, "Ticket", ["status", "postponed_until", "closed_at"])

    ticket.status = new_status

    if new_status == TicketStatus.POSTPONED:
        if postponed_until is None:
            raise ServiceError("errors.deferred_date_required", http_status=400)
        ticket.postponed_until = postponed_until
    else:
        ticket.postponed_until = None

    ticket.closed_at = datetime.now(timezone.utc) if new_status == TicketStatus.CLOSED else None

    if new_status == TicketStatus.ACTIVE:
        ticket.assigned_to_id = None

    await tracker.commit(db, acting_user.id)
    await db.flush()


async def bulk_change_status(
    db: AsyncSession,
    tickets: List[Ticket],
    new_status: TicketStatus,
    postponed_until: Optional[date],
    *,
    acting_user: User,
) -> None:
    for ticket in tickets:
        await change_status(db, ticket, new_status, postponed_until, acting_user=acting_user)


def _assignment_single_email(assignee: User, ticket: Ticket, lang: str) -> tuple[str, str]:
    subject = translate("email.ticket_assigned_single.subject", lang, subject=ticket.subject)
    html = f"""
    <html><body>
    <p>{translate("email.ticket_assigned_single.greeting", lang, name=assignee.name)}</p>
    <p>{translate("email.ticket_assigned_single.body", lang, app_name=settings.app_name)}</p>
    <p><strong>{ticket.subject}</strong></p>
    <p>{translate("email.ticket_assigned_single.instruction", lang, app_name=settings.app_name)}</p>
    </body></html>
    """
    return subject, html


async def assign_ticket(
    db: AsyncSession, ticket: Ticket, assignee: Optional[User], *, acting_user: User, lang: str,
) -> None:
    """Assigns (or clears the assignment of) a ticket and, on
    assignment, sends the assignee a notification email localized to
    the club's configured language (ADR 0070 -- the API previously sent
    a hard-coded English email regardless of that setting)."""
    tracker = ChangeTracker(ticket, "Ticket", ["status", "assigned_to_id"])

    if assignee is not None:
        ticket.assigned_to_id = assignee.id
        ticket.status = TicketStatus.ASSIGNED
        await tracker.commit(db, acting_user.id)
        await db.flush()

        subject, html = _assignment_single_email(assignee, ticket, lang)
        await send_email(assignee.email, subject, html, db=db)
    else:
        ticket.assigned_to_id = None
        ticket.status = TicketStatus.ACTIVE
        await tracker.commit(db, acting_user.id)
        await db.flush()


async def bulk_assign_tickets(
    db: AsyncSession, tickets: List[Ticket], assignee: Optional[User], *, acting_user: User, lang: str,
) -> None:
    """Bulk counterpart of assign_ticket() -- sends ONE combined email
    to the assignee instead of one per ticket, to avoid flooding their
    inbox."""
    for ticket in tickets:
        tracker = ChangeTracker(ticket, "Ticket", ["status", "assigned_to_id"])
        if assignee:
            ticket.assigned_to_id = assignee.id
            ticket.status = TicketStatus.ASSIGNED
        else:
            ticket.assigned_to_id = None
            ticket.status = TicketStatus.ACTIVE
        await tracker.commit(db, acting_user.id)
    await db.flush()

    if assignee:
        subject = translate(
            "email.ticket_assigned_bulk.subject", lang, count=len(tickets), app_name=settings.app_name
        )
        items = "".join(f"<li>{t.subject}</li>" for t in tickets)
        html = f"""
        <html><body>
        <p>{translate("email.ticket_assigned_bulk.greeting", lang, name=assignee.name)}</p>
        <p>{translate("email.ticket_assigned_bulk.body", lang, count=len(tickets), app_name=settings.app_name)}</p>
        <ul>{items}</ul>
        <p>{translate("email.ticket_assigned_bulk.instruction", lang, app_name=settings.app_name)}</p>
        </body></html>
        """
        await send_email(assignee.email, subject, html, db=db)


def set_member(ticket: Ticket, member_id: Optional[str]) -> None:
    ticket.member_id = member_id.strip() if member_id and member_id.strip() else None


def set_spam_status(ticket: Ticket, spam_suspected: bool, *, acting_user: User) -> None:
    ticket.spam_suspected = spam_suspected
    ticket.spam_reviewed_by_id = acting_user.id
    ticket.spam_reviewed_at = datetime.now(timezone.utc)


def bulk_set_spam_status(tickets: List[Ticket], spam_suspected: bool, *, acting_user: User) -> None:
    for ticket in tickets:
        set_spam_status(ticket, spam_suspected, acting_user=acting_user)


async def add_message(
    db: AsyncSession, ticket: Ticket, content: str, direction: MessageDirection, *, acting_user: User,
) -> TicketMessage:
    content = content.strip()
    message_id = None
    if direction == MessageDirection.OUTGOING:
        message_id = await send_ticket_reply(ticket, content, db)

    message = TicketMessage(
        ticket_id=ticket.id, direction=direction, content=content,
        authored_by_id=acting_user.id, message_id=message_id,
    )
    db.add(message)
    await db.flush()
    return message
