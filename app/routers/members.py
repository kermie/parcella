"""
Mitglieder-Router: Liste, Anlegen, Bearbeiten, CSV-Import/Export.
"""
import csv
import io
import itertools
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Request, Form, Depends, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_, and_
from sqlalchemy.orm import selectinload

from app.database import get_db, active_member_filter
from app.models import Member, MemberPhone, MemberEmail, MemberParcel, Parcel
from app.auth import get_current_user, require_user, require_admin
from app.i18n import t_for
from app.branding import load_branding
from app.meeting_signin_sheet import render_meeting_signin_sheet_pdf

router = APIRouter(prefix="/members", tags=["members"])
from app.templating import templates


async def _get_member_mit_details(db: AsyncSession, member_id: str) -> Optional[Member]:
    result = await db.execute(
        select(Member)
        .options(
            selectinload(Member.phone_numbers),
            selectinload(Member.email_addresses),
            selectinload(Member.parcel_assignments).selectinload(MemberParcel.parcel),
        )
        .where(Member.id == member_id, Member.deleted_at.is_(None))
    )
    return result.scalar_one_or_none()


@router.get("/", response_class=HTMLResponse)
async def mitglieder_liste(
    request: Request,
    suche: str = "",
    auch_inaktive: bool = False,
    db: AsyncSession = Depends(get_db),
):
    user = await require_user(request, db)

    query = (
        select(Member)
        .options(
            selectinload(Member.email_addresses),
            selectinload(Member.parcel_assignments).selectinload(MemberParcel.parcel),
        )
        .order_by(Member.last_name, Member.first_name)
    )

    if auch_inaktive:
        # Alle nicht-gelöschten Mitglieder (inkl. abgelaufene Mitgliedschaften)
        query = query.where(Member.deleted_at.is_(None))
    else:
        query = query.where(active_member_filter())

    if suche:
        # Sucht nach Vor-/Nachname ODER Parzellennummer. Bei der
        # Parzellensuche: standardmäßig nur aktuelle Zuordnungen (wer
        # JETZT dort wohnt); mit "auch_inaktive" zusätzlich auch bereits
        # beendete Zuordnungen (wer FRÜHER dort gewohnt hat) -- dieselbe
        # Kippschalter-Logik wie bei aktiven/inaktiven Mitgliedern, nur
        # auf die Pächter-Historie der Parzelle übertragen.
        # "Ort" wurde bewusst entfernt, da die Suche danach in der Praxis
        # nicht genutzt wurde.
        parzellen_bedingung = Parcel.plot_number.ilike(f"%{suche}%")
        if not auch_inaktive:
            parzellen_bedingung = and_(
                parzellen_bedingung, MemberParcel.assigned_until.is_(None)
            )
        parzellen_treffer = (
            select(MemberParcel.member_id)
            .join(Parcel, MemberParcel.parcel_id == Parcel.id)
            .where(parzellen_bedingung)
        )
        query = query.where(
            or_(
                Member.first_name.ilike(f"%{suche}%"),
                Member.last_name.ilike(f"%{suche}%"),
                Member.id.in_(parzellen_treffer),
            )
        )

    result = await db.execute(query)
    members = result.scalars().all()

    return templates.TemplateResponse(
        "members/list.html",
        {
            "request": request,
            "user": user,
            "members": members,
            "suche": suche,
            "auch_inaktive": auch_inaktive,
        },
    )


# ---------------------------------------------------------------------------
# General-meeting sign-in sheet (PDF): current members, grouped by
# parcel, one signature line each. Not gated by a module flag -- same
# permission level as the member list itself (require_user), since
# it's just another view onto the same data, not a separate feature
# area with its own security surface.
# ---------------------------------------------------------------------------

@router.get("/signin-sheet", response_class=HTMLResponse)
async def signin_sheet_form(request: Request, db: AsyncSession = Depends(get_db)):
    user = await require_user(request, db)
    default_headline = f"General meeting on {date.today().isoformat()}"
    return templates.TemplateResponse("members/signin_sheet.html", {
        "request": request, "user": user, "default_headline": default_headline,
    })


@router.post("/signin-sheet")
async def signin_sheet_generate(request: Request, db: AsyncSession = Depends(get_db)):
    await require_user(request, db)
    form = await request.form()
    headline = (form.get("headline") or "").strip()
    if not headline:
        headline = f"General meeting on {date.today().isoformat()}"

    # Current residents only (assigned_until IS NULL -- same "who lives
    # here right now" definition used elsewhere, e.g. the announcement
    # email channel), same active-membership filter as the member list
    # itself. Already sorted by parcel then name, so grouping
    # consecutive rows below doesn't need to re-sort.
    result = await db.execute(
        select(Parcel.plot_number, Member.first_name, Member.last_name)
        .join(MemberParcel, MemberParcel.parcel_id == Parcel.id)
        .join(Member, Member.id == MemberParcel.member_id)
        .where(MemberParcel.assigned_until.is_(None), active_member_filter())
        .order_by(Parcel.plot_number, Member.last_name, Member.first_name)
    )
    rows = result.all()

    parcel_members = [
        (plot_number, [f"{first_name} {last_name}" for _, first_name, last_name in group])
        for plot_number, group in itertools.groupby(rows, key=lambda row: row[0])
    ]

    branding = await load_branding(db)
    logo_path = Path("app" + branding["logo_url"]) if branding["logo_url"] else None

    pdf_bytes = render_meeting_signin_sheet_pdf(headline, branding["club_name"], logo_path, parcel_members)

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="signin-sheet.pdf"'},
    )


@router.get("/new", response_class=HTMLResponse)
async def mitglied_neu_seite(request: Request, db: AsyncSession = Depends(get_db)):
    user = await require_user(request, db)
    return templates.TemplateResponse(
        "members/form.html",
        {"request": request, "user": user, "member": None},
    )


@router.post("/new")
async def mitglied_erstellen(
    request: Request,
    first_name: str = Form(...),
    last_name: str = Form(...),
    street: str = Form(""),
    postal_code: str = Form(""),
    city: str = Form(""),
    date_of_birth: str = Form(""),
    iban: str = Form(""),
    member_since: str = Form(""),
    member_until: str = Form(""),
    email_notifications: bool = Form(False),
    notes: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    user = await require_user(request, db)

    def parse_datum(s: str) -> Optional[date]:
        if s:
            try:
                return date.fromisoformat(s)
            except ValueError:
                pass
        return None

    member = Member(
        first_name=first_name.strip(),
        last_name=last_name.strip(),
        street=street.strip() or None,
        postal_code=postal_code.strip() or None,
        city=city.strip() or None,
        date_of_birth=parse_datum(date_of_birth),
        iban=iban.strip() or None,
        member_since=parse_datum(member_since),
        member_until=parse_datum(member_until),
        email_notifications=email_notifications,
        notes=notes.strip() or None,
    )
    db.add(member)
    await db.commit()

    return RedirectResponse(f"/members/{member.id}", status_code=302)


@router.get("/{member_id}", response_class=HTMLResponse)
async def mitglied_detail(
    member_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await require_user(request, db)
    member = await _get_member_mit_details(db, member_id)

    if not member:
        raise HTTPException(status_code=404, detail=t_for(request, "members.errors.member_not_found"))

    # Alle aktiven Parzellen für Zuordnung
    parzellen_result = await db.execute(
        select(Parcel).order_by(Parcel.plot_number)
    )
    alle_parzellen = parzellen_result.scalars().all()

    return templates.TemplateResponse(
        "members/detail.html",
        {
            "request": request,
            "user": user,
            "member": member,
            "alle_parzellen": alle_parzellen,
        },
    )


@router.get("/{member_id}/edit", response_class=HTMLResponse)
async def mitglied_bearbeiten_seite(
    member_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await require_user(request, db)
    member = await _get_member_mit_details(db, member_id)

    if not member:
        raise HTTPException(status_code=404, detail=t_for(request, "members.errors.member_not_found"))

    return templates.TemplateResponse(
        "members/form.html",
        {"request": request, "user": user, "member": member},
    )


@router.post("/{member_id}/edit")
async def mitglied_aktualisieren(
    member_id: str,
    request: Request,
    first_name: str = Form(...),
    last_name: str = Form(...),
    street: str = Form(""),
    postal_code: str = Form(""),
    city: str = Form(""),
    date_of_birth: str = Form(""),
    iban: str = Form(""),
    member_since: str = Form(""),
    member_until: str = Form(""),
    email_notifications: bool = Form(False),
    notes: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    await require_user(request, db)
    member = await _get_member_mit_details(db, member_id)

    if not member:
        raise HTTPException(status_code=404)

    def parse_datum(s: str) -> Optional[date]:
        if s:
            try:
                return date.fromisoformat(s)
            except ValueError:
                pass
        return None

    member.first_name = first_name.strip()
    member.last_name = last_name.strip()
    member.street = street.strip() or None
    member.postal_code = postal_code.strip() or None
    member.city = city.strip() or None
    member.date_of_birth = parse_datum(date_of_birth)
    member.iban = iban.strip() or None
    member.member_since = parse_datum(member_since)
    member.member_until = parse_datum(member_until)
    member.email_notifications = email_notifications
    member.notes = notes.strip() or None

    await db.commit()
    return RedirectResponse(f"/members/{member_id}", status_code=302)


@router.post("/{member_id}/delete")
async def mitglied_loeschen(
    member_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Soft-delete: setzt deleted_at, entfernt das Member aus Listen/Suche.
    Bereits erfasste Parzellenzuordnungen, Tickets, Arbeitseinsätze usw.
    bleiben unverändert -- kein FK-Cascade, da kein echtes DELETE.
    Admin/Board only (gleiche Berechtigungsstufe wie require_admin
    anderswo im Projekt, siehe app/auth.py)."""
    await require_admin(request, db)
    member = await _get_member_mit_details(db, member_id)

    if not member:
        raise HTTPException(status_code=404, detail=t_for(request, "members.errors.member_not_found"))

    member.deleted_at = datetime.now(timezone.utc)
    await db.commit()

    meldung = t_for(request, "members.detail.deleted_message", name=member.full_name)
    import urllib.parse
    return RedirectResponse(
        f"/members/?meldung={urllib.parse.quote(meldung)}", status_code=302
    )


# ---------------------------------------------------------------------------
# Telefon / E-Mail-Verwaltung
# ---------------------------------------------------------------------------

@router.post("/{member_id}/phone/add")
async def telefon_hinzufuegen(
    member_id: str,
    request: Request,
    number: str = Form(...),
    label: str = Form(""),
    is_primary: bool = Form(False),
    db: AsyncSession = Depends(get_db),
):
    await require_user(request, db)
    telefon = MemberPhone(
        member_id=member_id,
        number=number.strip(),
        label=label.strip() or None,
        is_primary=is_primary,
    )
    db.add(telefon)
    await db.commit()
    return RedirectResponse(f"/members/{member_id}", status_code=302)


@router.post("/{member_id}/phone/{phone_id}/delete")
async def telefon_loeschen(
    member_id: str,
    phone_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    await require_user(request, db)
    result = await db.execute(
        select(MemberPhone).where(
            MemberPhone.id == phone_id,
            MemberPhone.member_id == member_id,
        )
    )
    telefon = result.scalar_one_or_none()
    if telefon:
        await db.delete(telefon)
        await db.commit()
    return RedirectResponse(f"/members/{member_id}", status_code=302)


@router.post("/{member_id}/email/add")
async def email_hinzufuegen(
    member_id: str,
    request: Request,
    address: str = Form(...),
    label: str = Form(""),
    is_primary: bool = Form(False),
    db: AsyncSession = Depends(get_db),
):
    await require_user(request, db)
    email_obj = MemberEmail(
        member_id=member_id,
        address=address.strip().lower(),
        label=label.strip() or None,
        is_primary=is_primary,
    )
    db.add(email_obj)
    await db.commit()
    return RedirectResponse(f"/members/{member_id}", status_code=302)


@router.post("/{member_id}/email/{email_id}/delete")
async def email_loeschen(
    member_id: str,
    email_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    await require_user(request, db)
    result = await db.execute(
        select(MemberEmail).where(
            MemberEmail.id == email_id,
            MemberEmail.member_id == member_id,
        )
    )
    email_obj = result.scalar_one_or_none()
    if email_obj:
        await db.delete(email_obj)
        await db.commit()
    return RedirectResponse(f"/members/{member_id}", status_code=302)


# ---------------------------------------------------------------------------
# CSV-Export
# ---------------------------------------------------------------------------

@router.get("/export/csv")
async def mitglieder_export_csv(request: Request, db: AsyncSession = Depends(get_db)):
    await require_user(request, db)

    result = await db.execute(
        select(Member)
        .options(
            selectinload(Member.email_addresses),
            selectinload(Member.phone_numbers),
            selectinload(Member.parcel_assignments).selectinload(MemberParcel.parcel),
        )
        .where(active_member_filter())
        .order_by(Member.last_name, Member.first_name)
    )
    members = result.scalars().all()

    output = io.StringIO()
    writer = csv.writer(output, delimiter=";")
    writer.writerow([
        "Vorname", "Nachname", "Strasse", "PLZ", "Ort",
        "Geburtsdatum", "IBAN", "Member seit", "Member bis",
        "E-Mail-Benachrichtigungen", "E-Mail-Adressen", "Telefonnummern",
        "Parzellen", "Notizen"
    ])

    for m in members:
        emails = "; ".join(e.address for e in m.email_addresses)
        telefone = "; ".join(t.number for t in m.phone_numbers)
        parcels = "; ".join(z.parcel.plot_number for z in m.parcel_assignments)
        writer.writerow([
            m.first_name, m.last_name, m.street or "", m.postal_code or "", m.city or "",
            m.date_of_birth.isoformat() if m.date_of_birth else "",
            m.iban or "",
            m.member_since.isoformat() if m.member_since else "",
            m.member_until.isoformat() if m.member_until else "",
            "Ja" if m.email_notifications else "Nein",
            emails, telefone, parcels,
            m.notes or "",
        ])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=members.csv"},
    )


# ---------------------------------------------------------------------------
# CSV-Import
# ---------------------------------------------------------------------------

@router.post("/import/csv")
async def mitglieder_import_csv(
    request: Request,
    datei: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    await require_user(request, db)

    inhalt = await datei.read()
    try:
        text = inhalt.decode("utf-8-sig")  # BOM-safe (Excel)
    except UnicodeDecodeError:
        text = inhalt.decode("latin-1")    # Fallback für ältere Windows-Exporte

    # Trennzeichen automatisch erkennen (Semikolon oder Komma) – viele
    # Tabellenprogramme speichern CSVs je nach Spracheinstellung
    # unterschiedlich, auch wenn die Datei ursprünglich mit Semikolon
    # exportiert wurde.
    try:
        delimiter = csv.Sniffer().sniff(text[:2048], delimiters=";,").delimiter
    except csv.Error:
        delimiter = ";"

    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    # Spaltennamen von führenden/nachgestellten Leerzeichen befreien,
    # falls die Tabellenkalkulation beim Speichern welche eingefügt hat.
    if reader.fieldnames:
        reader.fieldnames = [f.strip() if f else f for f in reader.fieldnames]

    erstellt = 0
    aktualisiert = 0
    fehler = []

    def parse_datum(s: str) -> Optional[date]:
        s = s.strip()
        for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d.%m.%y"):
            try:
                return date.fromisoformat(s) if fmt == "%Y-%m-%d" else datetime.strptime(s, fmt).date()
            except ValueError:
                continue
        return None

    for zeilennr, zeile in enumerate(reader, start=2):
        first_name = (zeile.get("Vorname") or "").strip()
        last_name = (zeile.get("Nachname") or "").strip()

        if not first_name or not last_name:
            fehler.append(f"Zeile {zeilennr}: Vor- oder Nachname fehlt – übersprungen.")
            continue

        # Duplikat-Erkennung: gleicher Vor- + Nachname + Geburtsdatum
        date_of_birth = parse_datum(zeile.get("Geburtsdatum") or "")
        existing_query = select(Member).where(
            Member.first_name == first_name,
            Member.last_name == last_name,
            Member.deleted_at.is_(None),
        )
        if date_of_birth:
            existing_query = existing_query.where(Member.date_of_birth == date_of_birth)

        existing_result = await db.execute(existing_query)
        existing = existing_result.scalars().first()

        email_ben_str = (zeile.get("E-Mail-Benachrichtigungen") or "Ja").strip().lower()
        email_notifications = email_ben_str not in ("nein", "no", "false", "0")

        felder = dict(
            first_name=first_name,
            last_name=last_name,
            street=(zeile.get("Strasse") or "").strip() or None,
            postal_code=(zeile.get("PLZ") or "").strip() or None,
            city=(zeile.get("Ort") or "").strip() or None,
            date_of_birth=date_of_birth,
            iban=(zeile.get("IBAN") or "").strip() or None,
            member_since=parse_datum(zeile.get("Member seit") or ""),
            member_until=parse_datum(zeile.get("Member bis") or ""),
            email_notifications=email_notifications,
            notes=(zeile.get("Notizen") or "").strip() or None,
        )

        if existing:
            # Vorhandenes Member aktualisieren
            for k, v in felder.items():
                setattr(existing, k, v)
            member = existing
            aktualisiert += 1
        else:
            member = Member(**felder)
            db.add(member)
            await db.flush()  # ID generieren für Untereinträge
            erstellt += 1

        # E-Mail-Adressen (Semikolon-getrennt in einer Zelle)
        emails_str = (zeile.get("E-Mail-Adressen") or "").strip()
        if emails_str and not existing:
            for i, adresse in enumerate(emails_str.split(";")):
                adresse = adresse.strip().lower()
                if adresse:
                    db.add(MemberEmail(
                        member_id=member.id,
                        address=adresse,
                        is_primary=(i == 0),
                    ))

        # Telefonnummern (Semikolon-getrennt in einer Zelle)
        telefone_str = (zeile.get("Telefonnummern") or "").strip()
        if telefone_str and not existing:
            for i, nummer in enumerate(telefone_str.split(";")):
                nummer = nummer.strip()
                if nummer:
                    db.add(MemberPhone(
                        member_id=member.id,
                        number=nummer,
                        is_primary=(i == 0),
                    ))

    await db.commit()

    meldung = t_for(request, "members.list.csv_import_summary", created=erstellt, updated=aktualisiert)
    if fehler:
        meldung += t_for(request, "members.list.csv_import_errors_suffix", count=len(fehler))
        # Erste paar Fehlerdetails anzeigen, damit man die Ursache sofort sieht
        meldung += " – " + " | ".join(fehler[:3])
        if len(fehler) > 3:
            meldung += t_for(request, "members.list.csv_import_more_errors", count=len(fehler) - 3)

    import urllib.parse
    return RedirectResponse(
        f"/members/?meldung={urllib.parse.quote(meldung)}",
        status_code=302,
    )
