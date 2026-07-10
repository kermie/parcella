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
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_
from sqlalchemy.orm import selectinload

from app.database import get_db, aktives_mitglied_filter
from app.models import (
    Ticket, TicketNachricht, TicketStatus, NachrichtRichtung, Benutzer, Mitglied,
)
from app.auth import require_user
from app.module_flags import require_modul
from app.aenderungstracker import AenderungsTracker
from app.ticket_utils import finde_mitglieder_per_email
from app.email_service import sende_email
from app.config import settings

router = APIRouter(
    prefix="/tickets",
    tags=["tickets"],
    dependencies=[Depends(require_modul("tickets"))],
)
templates = Jinja2Templates(directory="app/templates")


async def _lade_ticket_mit_details(db: AsyncSession, ticket_id: str) -> Optional[Ticket]:
    result = await db.execute(
        select(Ticket)
        .options(
            selectinload(Ticket.zugewiesen_an),
            selectinload(Ticket.mitglied),
            selectinload(Ticket.nachrichten).selectinload(TicketNachricht.verfasst_von),
        )
        .where(Ticket.id == ticket_id)
    )
    return result.scalar_one_or_none()


# ---------------------------------------------------------------------------
# Übersicht
# ---------------------------------------------------------------------------

@router.get("/", response_class=HTMLResponse)
async def tickets_uebersicht(
    request: Request,
    filter: str = "aktiv",  # aktiv | mir | geschlossen | alle
    suche: str = "",
    db: AsyncSession = Depends(get_db),
):
    benutzer = await require_user(request, db)

    query = (
        select(Ticket)
        .options(selectinload(Ticket.zugewiesen_an), selectinload(Ticket.mitglied))
        .order_by(Ticket.erstellt_am.desc())
    )

    if filter == "aktiv":
        query = query.where(Ticket.status != TicketStatus.GESCHLOSSEN)
    elif filter == "mir":
        query = query.where(
            Ticket.zugewiesen_an_id == benutzer.id, Ticket.status != TicketStatus.GESCHLOSSEN
        )
    elif filter == "geschlossen":
        query = query.where(Ticket.status == TicketStatus.GESCHLOSSEN)
    # "alle": kein zusätzlicher Filter

    if suche:
        query = query.where(
            or_(
                Ticket.betreff.ilike(f"%{suche}%"),
                Ticket.absender_email.ilike(f"%{suche}%"),
                Ticket.absender_name.ilike(f"%{suche}%"),
            )
        )

    result = await db.execute(query)
    tickets = result.scalars().all()

    # "Fällige" zurückgestellte Tickets (Datum erreicht) zählen als aktiv,
    # unabhängig vom gespeicherten Status – rein berechnet, kein Hintergrundjob.
    faellige_anzahl = sum(1 for t in tickets if t.ist_faellig)

    return templates.TemplateResponse("tickets/uebersicht.html", {
        "request": request, "benutzer": benutzer,
        "tickets": tickets, "filter": filter, "suche": suche,
        "faellige_anzahl": faellige_anzahl,
        "TicketStatus": TicketStatus,
    })


# ---------------------------------------------------------------------------
# Anlegen
# ---------------------------------------------------------------------------

@router.get("/neu", response_class=HTMLResponse)
async def ticket_neu_seite(request: Request, db: AsyncSession = Depends(get_db)):
    benutzer = await require_user(request, db)
    return templates.TemplateResponse("tickets/formular.html", {"request": request, "benutzer": benutzer})


@router.post("/neu")
async def ticket_erstellen(
    request: Request,
    betreff: str = Form(...),
    absender_email: str = Form(...),
    absender_name: str = Form(""),
    nachricht: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    benutzer = await require_user(request, db)

    absender_email = absender_email.strip().lower()
    treffer = await finde_mitglieder_per_email(db, absender_email)
    mitglied_id = treffer[0].id if len(treffer) == 1 else None

    ticket = Ticket(
        betreff=betreff.strip(),
        absender_email=absender_email,
        absender_name=absender_name.strip() or None,
        mitglied_id=mitglied_id,
    )
    db.add(ticket)
    await db.flush()

    db.add(TicketNachricht(
        ticket_id=ticket.id, richtung=NachrichtRichtung.EINGEHEND,
        inhalt=nachricht.strip(),
    ))
    await db.commit()

    return RedirectResponse(f"/tickets/{ticket.id}", status_code=302)


# ---------------------------------------------------------------------------
# Detail
# ---------------------------------------------------------------------------

@router.get("/{ticket_id}", response_class=HTMLResponse)
async def ticket_detail(
    ticket_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    benutzer = await require_user(request, db)
    ticket = await _lade_ticket_mit_details(db, ticket_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket nicht gefunden")

    # Mögliche Mitglied-Kandidaten (falls Absender-Adresse mehreren gehört
    # oder noch keinem zugeordnet ist)
    kandidaten = await finde_mitglieder_per_email(db, ticket.absender_email)

    benutzer_result = await db.execute(select(Benutzer).where(Benutzer.ist_aktiv == True).order_by(Benutzer.name))
    alle_benutzer = benutzer_result.scalars().all()

    return templates.TemplateResponse("tickets/detail.html", {
        "request": request, "benutzer": benutzer, "ticket": ticket,
        "kandidaten": kandidaten, "alle_benutzer": alle_benutzer,
        "TicketStatus": TicketStatus, "NachrichtRichtung": NachrichtRichtung,
        "heute": date.today().isoformat(),
    })


# ---------------------------------------------------------------------------
# Zuweisen
# ---------------------------------------------------------------------------

@router.post("/{ticket_id}/zuweisen")
async def ticket_zuweisen(
    ticket_id: str,
    request: Request,
    benutzer_id: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    aktueller_benutzer = await require_user(request, db)
    ticket = await _lade_ticket_mit_details(db, ticket_id)
    if not ticket:
        raise HTTPException(status_code=404)

    tracker = AenderungsTracker(ticket, "Ticket", ["status", "zugewiesen_an_id"])

    if benutzer_id.strip():
        result = await db.execute(select(Benutzer).where(Benutzer.id == benutzer_id))
        zugewiesener = result.scalar_one_or_none()
        if not zugewiesener:
            raise HTTPException(status_code=404, detail="Benutzer nicht gefunden")

        ticket.zugewiesen_an_id = zugewiesener.id
        ticket.status = TicketStatus.ZUGEWIESEN

        await tracker.commit(db, aktueller_benutzer.id)
        await db.commit()

        # Benachrichtigung per E-Mail (nutzt bestehende Vereins-SMTP-Konfiguration)
        betreff = f"Ticket zugewiesen: {ticket.betreff}"
        html = f"""
        <html><body>
        <p>Hallo {zugewiesener.name},</p>
        <p>Ihnen wurde ein Ticket im Gartenmanager zugewiesen:</p>
        <p><strong>{ticket.betreff}</strong></p>
        <p>Bitte melden Sie sich im Gartenmanager an, um es zu bearbeiten.</p>
        </body></html>
        """
        await sende_email(zugewiesener.email, betreff, html, db=db)
    else:
        ticket.zugewiesen_an_id = None
        ticket.status = TicketStatus.NICHT_ZUGEWIESEN
        await tracker.commit(db, aktueller_benutzer.id)
        await db.commit()

    return RedirectResponse(f"/tickets/{ticket_id}", status_code=302)


# ---------------------------------------------------------------------------
# Status ändern
# ---------------------------------------------------------------------------

@router.post("/{ticket_id}/status")
async def ticket_status_aendern(
    ticket_id: str,
    request: Request,
    status_neu: str = Form(...),
    zurueckgestellt_bis: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    aktueller_benutzer = await require_user(request, db)
    ticket = await _lade_ticket_mit_details(db, ticket_id)
    if not ticket:
        raise HTTPException(status_code=404)

    tracker = AenderungsTracker(ticket, "Ticket", ["status", "zurueckgestellt_bis", "geschlossen_am"])

    neuer_status = TicketStatus(status_neu)
    ticket.status = neuer_status

    if neuer_status == TicketStatus.ZURUECKGESTELLT:
        if not zurueckgestellt_bis.strip():
            raise HTTPException(status_code=400, detail="Datum für Zurückstellung erforderlich")
        ticket.zurueckgestellt_bis = date.fromisoformat(zurueckgestellt_bis)
    else:
        ticket.zurueckgestellt_bis = None

    if neuer_status == TicketStatus.GESCHLOSSEN:
        ticket.geschlossen_am = datetime.now(timezone.utc)
    else:
        ticket.geschlossen_am = None

    if neuer_status == TicketStatus.NICHT_ZUGEWIESEN:
        ticket.zugewiesen_an_id = None

    await tracker.commit(db, aktueller_benutzer.id)
    await db.commit()
    return RedirectResponse(f"/tickets/{ticket_id}", status_code=302)


# ---------------------------------------------------------------------------
# Mitglied manuell zuordnen
# ---------------------------------------------------------------------------

@router.post("/{ticket_id}/mitglied")
async def ticket_mitglied_zuordnen(
    ticket_id: str,
    request: Request,
    mitglied_id: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    await require_user(request, db)
    result = await db.execute(select(Ticket).where(Ticket.id == ticket_id))
    ticket = result.scalar_one_or_none()
    if not ticket:
        raise HTTPException(status_code=404)

    ticket.mitglied_id = mitglied_id.strip() or None
    await db.commit()
    return RedirectResponse(f"/tickets/{ticket_id}", status_code=302)


# ---------------------------------------------------------------------------
# Nachricht / interne Notiz hinzufügen
# ---------------------------------------------------------------------------

@router.post("/{ticket_id}/nachricht")
async def nachricht_hinzufuegen(
    ticket_id: str,
    request: Request,
    inhalt: str = Form(...),
    richtung: str = Form("AUSGEHEND"),
    db: AsyncSession = Depends(get_db),
):
    benutzer = await require_user(request, db)
    result = await db.execute(select(Ticket).where(Ticket.id == ticket_id))
    ticket = result.scalar_one_or_none()
    if not ticket:
        raise HTTPException(status_code=404)

    db.add(TicketNachricht(
        ticket_id=ticket_id,
        richtung=NachrichtRichtung(richtung),
        inhalt=inhalt.strip(),
        verfasst_von_id=benutzer.id,
    ))
    await db.commit()

    # Hinweis: der tatsächliche E-Mail-Versand an den Absender folgt in
    # Etappe 2 (Ticket-Postfach-Integration). Aktuell wird die Antwort nur
    # im Verlauf gespeichert.

    return RedirectResponse(f"/tickets/{ticket_id}", status_code=302)
