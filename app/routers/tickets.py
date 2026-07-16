"""
Ticketsystem-Router (Web-Oberfläche): Übersicht, Anlegen, Detail,
Zuweisen, Status ändern, Nachrichten/Notizen.

Etappe 1: manuelle Ticketverwaltung, noch kein E-Mail-Abruf (kommt in
Etappe 2). Zuweisungs-Benachrichtigung per E-Mail funktioniert bereits,
da die allgemeine SMTP-Infrastruktur (app/email_service.py) wiederverwendet wird.
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
from app.auth import require_user
from app.module_flags import require_modul
from app.change_tracker import ChangeTracker
from app.ticket_utils import find_members_by_email
from app.ticket_mailer import send_ticket_reply, process_incoming_mails
from app.email_service import sende_email
from app.i18n import t_for
from app.config import settings

router = APIRouter(
    prefix="/tickets",
    tags=["tickets"],
    dependencies=[Depends(require_modul("tickets"))],
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


async def _reaktiviere_faellige_tickets(db: AsyncSession) -> int:
    """
    Setzt zurückgestellte Tickets, deren postponed_until erreicht ist,
    tatsächlich auf ACTIVE/ASSIGNED zurück (nicht nur rein berechnet über
    is_due) -- kein Hintergrundjob, sondern lazy beim nächsten Laden der
    Ticketliste ausgeführt, da es keine Scheduler-Infrastruktur gibt.
    Gibt die Anzahl der reaktivierten Tickets zurück.
    """
    result = await db.execute(
        select(Ticket).where(
            Ticket.status == TicketStatus.POSTPONED,
            Ticket.postponed_until <= date.today(),
        )
    )
    faellige = result.scalars().all()
    for ticket in faellige:
        ticket.status = TicketStatus.ASSIGNED if ticket.assigned_to_id else TicketStatus.ACTIVE
        ticket.postponed_until = None
    if faellige:
        await db.commit()
    return len(faellige)


# ---------------------------------------------------------------------------
# Übersicht
# ---------------------------------------------------------------------------

@router.get("/", response_class=HTMLResponse)
async def tickets_overview(
    request: Request,
    filter: str = "aktiv",  # aktiv | mir | wartend | zurueckgestellt | geschlossen | spam | alle
    search: str = "",
    db: AsyncSession = Depends(get_db),
):
    user = await require_user(request, db)

    reaktiviert_count = await _reaktiviere_faellige_tickets(db)

    query = (
        select(Ticket)
        .options(selectinload(Ticket.assigned_to), selectinload(Ticket.member))
        .order_by(Ticket.created_at.desc())
    )

    # "Aktiv" und "Mir" zeigen bewusst NUR operativ offene Tickets (ACTIVE/
    # ASSIGNED/WAITING) -- POSTPONED-Tickets sind bis zu ihrem Datum absichtlich
    # unsichtbar (siehe _reaktiviere_faellige_tickets oben, das sie danach
    # automatisch wieder hier auftauchen lässt). DELETED taucht nirgends
    # außer in keiner Ansicht auf (Soft-Delete, kein Papierkorb gebaut).
    offene_status = [TicketStatus.ACTIVE, TicketStatus.ASSIGNED, TicketStatus.WAITING]

    if filter == "aktiv":
        query = query.where(Ticket.status.in_(offene_status), Ticket.spam_suspected == False)
    elif filter == "mir":
        query = query.where(
            Ticket.assigned_to_id == user.id, Ticket.status.in_(offene_status)
        )
    elif filter == "wartend":
        query = query.where(Ticket.status == TicketStatus.WAITING)
    elif filter == "zurueckgestellt":
        query = query.where(Ticket.status == TicketStatus.POSTPONED)
    elif filter == "geschlossen":
        query = query.where(Ticket.status == TicketStatus.CLOSED)
    elif filter == "spam":
        query = query.where(Ticket.spam_suspected == True, Ticket.status != TicketStatus.DELETED)
    elif filter == "alle":
        query = query.where(Ticket.status != TicketStatus.DELETED)

    if search:
        query = query.where(
            or_(
                Ticket.subject.ilike(f"%{search}%"),
                Ticket.sender_email.ilike(f"%{search}%"),
                Ticket.sender_name.ilike(f"%{search}%"),
            )
        )

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

    # Für die "Zuweisen"-Auswahl in der Massenbearbeitung
    users_result = await db.execute(select(User).where(User.is_active == True).order_by(User.name))
    alle_benutzer = users_result.scalars().all()

    return templates.TemplateResponse("tickets/overview.html", {
        "request": request, "user": user,
        "tickets": tickets, "filter": filter, "search": search,
        "reaktiviert_count": reaktiviert_count,
        "postponed_count": postponed_count, "waiting_count": waiting_count,
        "spam_count": spam_count, "alle_benutzer": alle_benutzer,
        "TicketStatus": TicketStatus,
    })



# ---------------------------------------------------------------------------
# Anlegen
# ---------------------------------------------------------------------------

@router.get("/new", response_class=HTMLResponse)
async def ticket_new_page(request: Request, db: AsyncSession = Depends(get_db)):
    user = await require_user(request, db)
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
    user = await require_user(request, db)

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
# Massenbearbeitung (Mehrfachauswahl in der Übersicht)
# ---------------------------------------------------------------------------
# WICHTIG: müssen vor den generischen "/{ticket_id}/..."-Routen registriert
# sein, sonst würde z.B. POST /bulk/status von "/{ticket_id}/status" mit
# ticket_id="bulk" abgefangen werden.

@router.post("/bulk/status")
async def tickets_bulk_status(
    request: Request,
    ticket_ids: list[str] = Form(...),
    status_neu: str = Form(...),
    postponed_until: str = Form(""),
    filter: str = Form("aktiv"),
    db: AsyncSession = Depends(get_db),
):
    current_user = await require_user(request, db)

    neuer_status = TicketStatus(status_neu)
    result = await db.execute(select(Ticket).where(Ticket.id.in_(ticket_ids)))
    tickets = result.scalars().all()

    for ticket in tickets:
        tracker = ChangeTracker(ticket, "Ticket", ["status", "postponed_until", "closed_at"])
        _wende_status_an(ticket, neuer_status, postponed_until, request)
        await tracker.commit(db, current_user.id)

    await db.commit()
    return RedirectResponse(f"/tickets/?filter={filter}", status_code=302)


@router.post("/bulk/assign")
async def tickets_bulk_assign(
    request: Request,
    ticket_ids: list[str] = Form(...),
    user_id: str = Form(""),
    filter: str = Form("aktiv"),
    db: AsyncSession = Depends(get_db),
):
    current_user = await require_user(request, db)

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
        # Eine einzelne Sammel-E-Mail statt einer pro Ticket, um das
        # Postfach des Zugewiesenen nicht zu fluten.
        subject = f"{len(tickets)} Ticket(s) im Gartenmanager zugewiesen"
        items = "".join(f"<li>{t.subject}</li>" for t in tickets)
        html = f"""
        <html><body>
        <p>Hallo {assignee.name},</p>
        <p>Ihnen wurden {len(tickets)} Ticket(s) im Gartenmanager zugewiesen:</p>
        <ul>{items}</ul>
        <p>Bitte melden Sie sich im Gartenmanager an, um sie zu bearbeiten.</p>
        </body></html>
        """
        await sende_email(assignee.email, subject, html, db=db)

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
    user = await require_user(request, db)
    await _reaktiviere_faellige_tickets(db)
    ticket = await _load_ticket_with_details(db, ticket_id)
    if not ticket:
        raise HTTPException(status_code=404, detail=t_for(request, "errors.ticket_not_found"))

    # Mögliche Member-Kandidaten (falls Absender-Adresse mehreren gehört
    # oder noch keinem zugeordnet ist)
    candidates = await find_members_by_email(db, ticket.sender_email)

    user_result = await db.execute(select(User).where(User.is_active == True).order_by(User.name))
    all_users = user_result.scalars().all()

    return templates.TemplateResponse("tickets/detail.html", {
        "request": request, "user": user, "ticket": ticket,
        "candidates": candidates, "all_users": all_users,
        "TicketStatus": TicketStatus, "MessageDirection": MessageDirection,
        "heute": date.today().isoformat(),
    })


# ---------------------------------------------------------------------------
# Zuweisen
# ---------------------------------------------------------------------------

@router.post("/{ticket_id}/assign")
async def ticket_assign(
    ticket_id: str,
    request: Request,
    user_id: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    current_user = await require_user(request, db)
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

        # Benachrichtigung per E-Mail (nutzt bestehende Vereins-SMTP-Konfiguration)
        subject = f"Ticket zugewiesen: {ticket.subject}"
        html = f"""
        <html><body>
        <p>Hallo {assignee.name},</p>
        <p>Ihnen wurde ein Ticket im Gartenmanager zugewiesen:</p>
        <p><strong>{ticket.subject}</strong></p>
        <p>Bitte melden Sie sich im Gartenmanager an, um es zu bearbeiten.</p>
        </body></html>
        """
        await sende_email(assignee.email, subject, html, db=db)
    else:
        ticket.assigned_to_id = None
        ticket.status = TicketStatus.ACTIVE
        await tracker.commit(db, current_user.id)
        await db.commit()

    return RedirectResponse(f"/tickets/{ticket_id}", status_code=302)


# ---------------------------------------------------------------------------
# Status ändern
# ---------------------------------------------------------------------------

def _wende_status_an(ticket: Ticket, neuer_status: TicketStatus, postponed_until_str: str, request: Request) -> None:
    """
    Setzt den neuen Status auf einem Ticket inkl. Nebenwirkungen
    (postponed_until, closed_at, assigned_to_id) -- geteilte Logik für
    Einzelticket- und Massen-Statusänderung, damit beide garantiert
    dieselben Regeln anwenden.
    """
    ticket.status = neuer_status

    if neuer_status == TicketStatus.POSTPONED:
        if not postponed_until_str.strip():
            raise HTTPException(status_code=400, detail=t_for(request, "errors.deferred_date_required"))
        ticket.postponed_until = date.fromisoformat(postponed_until_str)
    else:
        ticket.postponed_until = None

    if neuer_status == TicketStatus.CLOSED:
        ticket.closed_at = datetime.now(timezone.utc)
    else:
        ticket.closed_at = None

    if neuer_status == TicketStatus.ACTIVE:
        ticket.assigned_to_id = None


@router.post("/{ticket_id}/status")
async def ticket_status_update(
    ticket_id: str,
    request: Request,
    status_neu: str = Form(...),
    postponed_until: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    current_user = await require_user(request, db)
    ticket = await _load_ticket_with_details(db, ticket_id)
    if not ticket:
        raise HTTPException(status_code=404)

    tracker = ChangeTracker(ticket, "Ticket", ["status", "postponed_until", "closed_at"])

    _wende_status_an(ticket, TicketStatus(status_neu), postponed_until, request)

    await tracker.commit(db, current_user.id)
    await db.commit()
    return RedirectResponse(f"/tickets/{ticket_id}", status_code=302)


# ---------------------------------------------------------------------------
# Member manuell zuordnen
# ---------------------------------------------------------------------------

@router.post("/{ticket_id}/member")
async def ticket_member_assign(
    ticket_id: str,
    request: Request,
    member_id: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    await require_user(request, db)
    result = await db.execute(select(Ticket).where(Ticket.id == ticket_id))
    ticket = result.scalar_one_or_none()
    if not ticket:
        raise HTTPException(status_code=404)

    ticket.member_id = member_id.strip() or None
    await db.commit()
    return RedirectResponse(f"/tickets/{ticket_id}", status_code=302)


# ---------------------------------------------------------------------------
# Spam-Verdacht aufheben (falsch-positiv)
# ---------------------------------------------------------------------------

@router.post("/{ticket_id}/not-spam")
async def ticket_mark_not_spam(
    ticket_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    await require_user(request, db)
    result = await db.execute(select(Ticket).where(Ticket.id == ticket_id))
    ticket = result.scalar_one_or_none()
    if not ticket:
        raise HTTPException(status_code=404)

    ticket.spam_suspected = False
    await db.commit()
    return RedirectResponse(f"/tickets/{ticket_id}", status_code=302)


# ---------------------------------------------------------------------------
# Nachricht / interne Notiz hinzufügen
# ---------------------------------------------------------------------------

@router.post("/{ticket_id}/message")
async def message_add(
    ticket_id: str,
    request: Request,
    content: str = Form(...),
    direction: str = Form("OUTGOING"),
    db: AsyncSession = Depends(get_db),
):
    user = await require_user(request, db)
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
# Ticket-Postfach: manueller Abruf (zusätzlich zum Hintergrund-Polling)
# ---------------------------------------------------------------------------

@router.post("/inbox/fetch-now")
async def inbox_fetch_now(request: Request, db: AsyncSession = Depends(get_db)):
    await require_user(request, db)
    anzahl = await process_incoming_mails(db)
    import urllib.parse
    meldung = urllib.parse.quote(f"{anzahl} neue E-Mail(s) verarbeitet.")
    return RedirectResponse(f"/tickets/?meldung={meldung}", status_code=302)
