"""
Ticket system router (web UI): overview, create, detail, assign,
status changes, messages/notes.

Stage 1: manual ticket management, no email fetching yet (that comes
in stage 2). Assignment notification by email already works, since the
general SMTP infrastructure (app/email_service.py) is reused.
"""
from datetime import date, datetime, timezone
from typing import Optional

from fastapi import APIRouter, Request, Form, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_
from sqlalchemy.orm import selectinload

from app.database import get_db, active_member_filter
from app.models import (
    Ticket, TicketMessage, TicketStatus, MessageDirection, User, Member,
)
from app.permissions import require_permission
from app.module_flags import require_module
from app.change_tracker import ChangeTracker
from app.ticket_utils import find_members_by_email
from app.ticket_mailer import send_ticket_reply, process_incoming_mails
from app.avatars import avatar_url
from app.email_service import send_email
from app.i18n import t_for
from app.config import settings

router = APIRouter(
    prefix="/tickets",
    tags=["tickets"],
    dependencies=[Depends(require_module("tickets"))],
)
from app.templating import templates


async def _load_ticket_with_details(db: AsyncSession, ticket_id: str) -> Optional[Ticket]:
    result = await db.execute(
        select(Ticket)
        .options(
            selectinload(Ticket.assigned_to),
            selectinload(Ticket.member),
            selectinload(Ticket.messages).selectinload(TicketMessage.authored_by),
        )
        .where(Ticket.id == ticket_id)
    )
    return result.scalar_one_or_none()


async def _reactivate_due_tickets(db: AsyncSession) -> int:
    """
    Actually resets postponed tickets whose postponed_until has been
    reached back to ACTIVE/ASSIGNED (not just computed on the fly via
    is_due) -- not a background job, but executed lazily on the next
    load of the ticket list, since there's no scheduler infrastructure.
    Returns the number of reactivated tickets.
    """
    result = await db.execute(
        select(Ticket).where(
            Ticket.status == TicketStatus.POSTPONED,
            Ticket.postponed_until <= date.today(),
        )
    )
    due_tickets = result.scalars().all()
    for ticket in due_tickets:
        ticket.status = TicketStatus.ASSIGNED if ticket.assigned_to_id else TicketStatus.ACTIVE
        ticket.postponed_until = None
    if due_tickets:
        await db.commit()
    return len(due_tickets)


# ---------------------------------------------------------------------------
# Overview
# ---------------------------------------------------------------------------

TICKETS_PAGE_SIZE = 50


def _tickets_filtered_query(filter: str, search: str, user_id: str):
    """Shared between the HTML overview (first page) and the JSON
    endpoint the infinite scroll polls for every page after that --
    both must agree on exactly which tickets match the current filter/
    search, and on a stable order (created_at ties broken by id,
    otherwise offset-based paging can skip or repeat rows)."""
    query = select(Ticket).options(selectinload(Ticket.assigned_to), selectinload(Ticket.member))

    # "Active" and "Mine" deliberately show ONLY operationally open
    # tickets (ACTIVE/ASSIGNED/WAITING) -- POSTPONED tickets are
    # intentionally invisible until their date (see
    # _reactivate_due_tickets above, which makes them reappear
    # here automatically afterward). DELETED never appears in any view
    # (soft-delete, no trash view built).
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


@router.get("/", response_class=HTMLResponse)
async def tickets_overview(
    request: Request,
    filter: str = "active",  # active | mine | waiting | postponed | closed | spam | all
    search: str = "",
    db: AsyncSession = Depends(get_db),
):
    user = await require_permission(request, db, "tickets", "read")

    reactivated_count = await _reactivate_due_tickets(db)

    query = _tickets_filtered_query(filter, search, user.id).limit(TICKETS_PAGE_SIZE)
    result = await db.execute(query)
    tickets = result.scalars().all()

    postponed_count_result = await db.execute(
        select(Ticket).where(Ticket.status == TicketStatus.POSTPONED)
    )
    postponed_count = len(postponed_count_result.scalars().all())

    waiting_count_result = await db.execute(
        select(Ticket).where(Ticket.status == TicketStatus.WAITING)
    )
    waiting_count = len(waiting_count_result.scalars().all())

    spam_count_result = await db.execute(
        select(Ticket).where(Ticket.spam_suspected == True, Ticket.status != TicketStatus.DELETED)
    )
    spam_count = len(spam_count_result.scalars().all())

    # For the "assign" selector in bulk editing
    users_result = await db.execute(select(User).where(User.is_active == True).order_by(User.name))
    all_active_users = users_result.scalars().all()

    return templates.TemplateResponse("tickets/overview.html", {
        "request": request, "user": user,
        "tickets": tickets, "filter": filter, "search": search,
        "reactivated_count": reactivated_count,
        "postponed_count": postponed_count, "waiting_count": waiting_count,
        "spam_count": spam_count, "all_active_users": all_active_users,
        "TicketStatus": TicketStatus,
        "page_size": TICKETS_PAGE_SIZE,
        "has_more": len(tickets) == TICKETS_PAGE_SIZE,
    })


@router.get("/list.json")
async def tickets_list_json(
    request: Request,
    filter: str = "active",
    search: str = "",
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
):
    """Fetched by the ticket overview's infinite scroll for every page
    after the first (which the HTML route above already renders)."""
    user = await require_permission(request, db, "tickets", "read")

    query = _tickets_filtered_query(filter, search, user.id).limit(TICKETS_PAGE_SIZE).offset(offset)
    result = await db.execute(query)
    tickets = result.scalars().all()

    return {
        "rows": [
            {
                "id": t.id,
                "subject": t.subject,
                "spam_suspected": t.spam_suspected,
                "member_name": t.member.full_name if t.member else None,
                "sender": t.sender_name or t.sender_email,
                "status": t.status.value,
                "assigned_to_name": t.assigned_to.name if t.assigned_to else None,
                "assigned_to_avatar_url": (
                    avatar_url(t.assigned_to.avatar_filename) if t.assigned_to else None
                ),
                "created_at": t.created_at.strftime("%d.%m.%Y %H:%M"),
            }
            for t in tickets
        ],
        "has_more": len(tickets) == TICKETS_PAGE_SIZE,
    }



# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------

@router.get("/new", response_class=HTMLResponse)
async def ticket_new_page(request: Request, db: AsyncSession = Depends(get_db)):
    user = await require_permission(request, db, "tickets", "read")
    return templates.TemplateResponse("tickets/form.html", {"request": request, "user": user})


@router.post("/new")
async def ticket_create(
    request: Request,
    subject: str = Form(...),
    sender_email: str = Form(...),
    sender_name: str = Form(""),
    message: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    user = await require_permission(request, db, "tickets", "write")

    sender_email = sender_email.strip().lower()
    matches = await find_members_by_email(db, sender_email)
    member_id = matches[0].id if len(matches) == 1 else None

    ticket = Ticket(
        subject=subject.strip(),
        sender_email=sender_email,
        sender_name=sender_name.strip() or None,
        member_id=member_id,
    )
    db.add(ticket)
    await db.flush()

    db.add(TicketMessage(
        ticket_id=ticket.id, direction=MessageDirection.INCOMING,
        content=message.strip(),
    ))
    await db.commit()

    return RedirectResponse(f"/tickets/{ticket.id}", status_code=302)


# ---------------------------------------------------------------------------
# Bulk editing (multi-select in the overview)
# ---------------------------------------------------------------------------
# IMPORTANT: these must be registered before the generic
# "/{ticket_id}/..." routes, otherwise e.g. POST /bulk/status would be
# caught by "/{ticket_id}/status" with ticket_id="bulk".

@router.post("/bulk/status")
async def tickets_bulk_status(
    request: Request,
    ticket_ids: list[str] = Form(...),
    new_status_value: str = Form(...),
    postponed_until: str = Form(""),
    filter: str = Form("active"),
    db: AsyncSession = Depends(get_db),
):
    current_user = await require_permission(request, db, "tickets", "write")

    new_status = TicketStatus(new_status_value)
    result = await db.execute(select(Ticket).where(Ticket.id.in_(ticket_ids)))
    tickets = result.scalars().all()

    for ticket in tickets:
        tracker = ChangeTracker(ticket, "Ticket", ["status", "postponed_until", "closed_at"])
        _apply_status(ticket, new_status, postponed_until, request)
        await tracker.commit(db, current_user.id)

    await db.commit()
    return RedirectResponse(f"/tickets/?filter={filter}", status_code=302)


@router.post("/bulk/assign")
async def tickets_bulk_assign(
    request: Request,
    ticket_ids: list[str] = Form(...),
    user_id: str = Form(""),
    filter: str = Form("active"),
    db: AsyncSession = Depends(get_db),
):
    current_user = await require_permission(request, db, "tickets", "write")

    assignee = None
    if user_id.strip():
        result = await db.execute(select(User).where(User.id == user_id))
        assignee = result.scalar_one_or_none()
        if not assignee:
            raise HTTPException(status_code=404, detail=t_for(request, "errors.user_not_found"))

    result = await db.execute(select(Ticket).where(Ticket.id.in_(ticket_ids)))
    tickets = result.scalars().all()

    for ticket in tickets:
        tracker = ChangeTracker(ticket, "Ticket", ["status", "assigned_to_id"])
        if assignee:
            ticket.assigned_to_id = assignee.id
            ticket.status = TicketStatus.ASSIGNED
        else:
            ticket.assigned_to_id = None
            ticket.status = TicketStatus.ACTIVE
        await tracker.commit(db, current_user.id)

    await db.commit()

    if assignee:
        # A single combined email instead of one per ticket, to avoid
        # flooding the assignee's inbox.
        subject = t_for(request, "email.ticket_assigned_bulk.subject", count=len(tickets), app_name=settings.app_name)
        items = "".join(f"<li>{t.subject}</li>" for t in tickets)
        html = f"""
        <html><body>
        <p>{t_for(request, "email.ticket_assigned_bulk.greeting", name=assignee.name)}</p>
        <p>{t_for(request, "email.ticket_assigned_bulk.body", count=len(tickets), app_name=settings.app_name)}</p>
        <ul>{items}</ul>
        <p>{t_for(request, "email.ticket_assigned_bulk.instruction", app_name=settings.app_name)}</p>
        </body></html>
        """
        await send_email(assignee.email, subject, html, db=db)

    return RedirectResponse(f"/tickets/?filter={filter}", status_code=302)


@router.post("/bulk/mark-spam")
async def tickets_bulk_mark_spam(
    request: Request,
    ticket_ids: list[str] = Form(...),
    filter: str = Form("active"),
    db: AsyncSession = Depends(get_db),
):
    await require_permission(request, db, "tickets", "write")
    result = await db.execute(select(Ticket).where(Ticket.id.in_(ticket_ids)))
    for ticket in result.scalars().all():
        ticket.spam_suspected = True
    await db.commit()
    return RedirectResponse(f"/tickets/?filter={filter}", status_code=302)


@router.post("/bulk/not-spam")
async def tickets_bulk_not_spam(
    request: Request,
    ticket_ids: list[str] = Form(...),
    filter: str = Form("active"),
    db: AsyncSession = Depends(get_db),
):
    await require_permission(request, db, "tickets", "write")
    result = await db.execute(select(Ticket).where(Ticket.id.in_(ticket_ids)))
    for ticket in result.scalars().all():
        ticket.spam_suspected = False
    await db.commit()
    return RedirectResponse(f"/tickets/?filter={filter}", status_code=302)


# ---------------------------------------------------------------------------
# Detail
# ---------------------------------------------------------------------------

@router.get("/{ticket_id}", response_class=HTMLResponse)
async def ticket_detail(
    ticket_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await require_permission(request, db, "tickets", "read")
    await _reactivate_due_tickets(db)
    ticket = await _load_ticket_with_details(db, ticket_id)
    if not ticket:
        raise HTTPException(status_code=404, detail=t_for(request, "errors.ticket_not_found"))

    # Possible member candidates (if the sender address belongs to
    # multiple members, or isn't assigned to one yet)
    candidates = await find_members_by_email(db, ticket.sender_email)

    user_result = await db.execute(select(User).where(User.is_active == True).order_by(User.name))
    all_users = user_result.scalars().all()

    return templates.TemplateResponse("tickets/detail.html", {
        "request": request, "user": user, "ticket": ticket,
        "candidates": candidates, "all_users": all_users,
        "TicketStatus": TicketStatus, "MessageDirection": MessageDirection,
        "today": date.today().isoformat(),
    })


# ---------------------------------------------------------------------------
# Assign
# ---------------------------------------------------------------------------

@router.post("/{ticket_id}/assign")
async def ticket_assign(
    ticket_id: str,
    request: Request,
    user_id: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    current_user = await require_permission(request, db, "tickets", "write")
    ticket = await _load_ticket_with_details(db, ticket_id)
    if not ticket:
        raise HTTPException(status_code=404)

    tracker = ChangeTracker(ticket, "Ticket", ["status", "assigned_to_id"])

    if user_id.strip():
        result = await db.execute(select(User).where(User.id == user_id))
        assignee = result.scalar_one_or_none()
        if not assignee:
            raise HTTPException(status_code=404, detail=t_for(request, "errors.user_not_found"))

        ticket.assigned_to_id = assignee.id
        ticket.status = TicketStatus.ASSIGNED

        await tracker.commit(db, current_user.id)
        await db.commit()

        # Notification by email (uses the existing club SMTP configuration)
        subject = t_for(request, "email.ticket_assigned_single.subject", subject=ticket.subject)
        html = f"""
        <html><body>
        <p>{t_for(request, "email.ticket_assigned_single.greeting", name=assignee.name)}</p>
        <p>{t_for(request, "email.ticket_assigned_single.body", app_name=settings.app_name)}</p>
        <p><strong>{ticket.subject}</strong></p>
        <p>{t_for(request, "email.ticket_assigned_single.instruction", app_name=settings.app_name)}</p>
        </body></html>
        """
        await send_email(assignee.email, subject, html, db=db)
    else:
        ticket.assigned_to_id = None
        ticket.status = TicketStatus.ACTIVE
        await tracker.commit(db, current_user.id)
        await db.commit()

    return RedirectResponse(f"/tickets/{ticket_id}", status_code=302)


# ---------------------------------------------------------------------------
# Change status
# ---------------------------------------------------------------------------

def _apply_status(ticket: Ticket, new_status: TicketStatus, postponed_until_str: str, request: Request) -> None:
    """
    Sets the new status on a ticket including side effects
    (postponed_until, closed_at, assigned_to_id) -- shared logic for
    single-ticket and bulk status changes, so both are guaranteed to
    apply the same rules.
    """
    ticket.status = new_status

    if new_status == TicketStatus.POSTPONED:
        if not postponed_until_str.strip():
            raise HTTPException(status_code=400, detail=t_for(request, "errors.deferred_date_required"))
        ticket.postponed_until = date.fromisoformat(postponed_until_str)
    else:
        ticket.postponed_until = None

    if new_status == TicketStatus.CLOSED:
        ticket.closed_at = datetime.now(timezone.utc)
    else:
        ticket.closed_at = None

    if new_status == TicketStatus.ACTIVE:
        ticket.assigned_to_id = None


@router.post("/{ticket_id}/status")
async def ticket_status_update(
    ticket_id: str,
    request: Request,
    new_status_value: str = Form(...),
    postponed_until: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    current_user = await require_permission(request, db, "tickets", "write")
    ticket = await _load_ticket_with_details(db, ticket_id)
    if not ticket:
        raise HTTPException(status_code=404)

    tracker = ChangeTracker(ticket, "Ticket", ["status", "postponed_until", "closed_at"])

    _apply_status(ticket, TicketStatus(new_status_value), postponed_until, request)

    await tracker.commit(db, current_user.id)
    await db.commit()
    return RedirectResponse(f"/tickets/{ticket_id}", status_code=302)


# ---------------------------------------------------------------------------
# Manually assign a member
# ---------------------------------------------------------------------------

@router.post("/{ticket_id}/member")
async def ticket_member_assign(
    ticket_id: str,
    request: Request,
    member_id: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    await require_permission(request, db, "tickets", "write")
    result = await db.execute(select(Ticket).where(Ticket.id == ticket_id))
    ticket = result.scalar_one_or_none()
    if not ticket:
        raise HTTPException(status_code=404)

    ticket.member_id = member_id.strip() or None
    await db.commit()
    return RedirectResponse(f"/tickets/{ticket_id}", status_code=302)


# ---------------------------------------------------------------------------
# Spam suspicion: mark manually, or clear a false positive
# ---------------------------------------------------------------------------

@router.post("/{ticket_id}/mark-spam")
async def ticket_mark_spam(
    ticket_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Lets staff flag a ticket the automated check (heuristics + the
    optional external API, app/spam_filter.py) missed -- the filter
    only ever runs once, on arrival, so anything it doesn't catch stays
    unflagged forever without a manual escape hatch."""
    await require_permission(request, db, "tickets", "write")
    result = await db.execute(select(Ticket).where(Ticket.id == ticket_id))
    ticket = result.scalar_one_or_none()
    if not ticket:
        raise HTTPException(status_code=404)

    ticket.spam_suspected = True
    await db.commit()
    return RedirectResponse(f"/tickets/{ticket_id}", status_code=302)


@router.post("/{ticket_id}/not-spam")
async def ticket_mark_not_spam(
    ticket_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    await require_permission(request, db, "tickets", "write")
    result = await db.execute(select(Ticket).where(Ticket.id == ticket_id))
    ticket = result.scalar_one_or_none()
    if not ticket:
        raise HTTPException(status_code=404)

    ticket.spam_suspected = False
    await db.commit()
    return RedirectResponse(f"/tickets/{ticket_id}", status_code=302)


# ---------------------------------------------------------------------------
# Add message / internal note
# ---------------------------------------------------------------------------

@router.post("/{ticket_id}/message")
async def message_add(
    ticket_id: str,
    request: Request,
    content: str = Form(...),
    direction: str = Form("OUTGOING"),
    db: AsyncSession = Depends(get_db),
):
    user = await require_permission(request, db, "tickets", "write")
    ticket = await _load_ticket_with_details(db, ticket_id)
    if not ticket:
        raise HTTPException(status_code=404)

    direction_enum = MessageDirection(direction)
    message_id = None

    if direction_enum == MessageDirection.OUTGOING:
        message_id = await send_ticket_reply(ticket, content.strip(), db)

    db.add(TicketMessage(
        ticket_id=ticket_id,
        direction=direction_enum,
        content=content.strip(),
        authored_by_id=user.id,
        message_id=message_id,
    ))
    await db.commit()

    return RedirectResponse(f"/tickets/{ticket_id}", status_code=302)


# ---------------------------------------------------------------------------
# Ticket mailbox: manual fetch (in addition to background polling)
# ---------------------------------------------------------------------------

@router.post("/inbox/fetch-now")
async def inbox_fetch_now(request: Request, db: AsyncSession = Depends(get_db)):
    await require_permission(request, db, "tickets", "write")
    count = await process_incoming_mails(db)
    import urllib.parse
    message = urllib.parse.quote(f"{count} neue E-Mail(s) verarbeitet.")
    return RedirectResponse(f"/tickets/?message={message}", status_code=302)
