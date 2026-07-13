"""
Parzellen-Router: Liste, Anlegen, Bearbeiten, Zuordnungen, CSV-Import/Export.
"""
import csv
import io
from datetime import date, datetime, timezone
from typing import Optional

from fastapi import APIRouter, Request, Form, Depends, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_
from sqlalchemy.orm import selectinload

from app.database import get_db, active_member_filter
from app.models import (
    Parcel, ParcelStatus, MemberParcel, Member, ChangeHistory
)
from app.auth import require_user
from app.i18n import t_for
from app.change_tracker import ChangeTracker

router = APIRouter(prefix="/parcels", tags=["parcels"])
from app.templating import templates


async def _get_parcel_mit_details(db: AsyncSession, parcel_id: str) -> Optional[Parcel]:
    result = await db.execute(
        select(Parcel)
        .options(
            selectinload(Parcel.member_assignments).selectinload(MemberParcel.member)
        )
        .where(Parcel.id == parcel_id)
    )
    return result.scalar_one_or_none()


@router.get("/", response_class=HTMLResponse)
async def parzellen_liste(
    request: Request,
    suche: str = "",
    status_filter: str = "",
    db: AsyncSession = Depends(get_db),
):
    user = await require_user(request, db)

    query = (
        select(Parcel)
        .options(
            selectinload(Parcel.member_assignments).selectinload(MemberParcel.member)
        )
        .order_by(Parcel.plot_number)
    )

    if suche:
        query = query.where(Parcel.plot_number.ilike(f"%{suche}%"))

    if status_filter and status_filter in [s.value for s in ParcelStatus]:
        query = query.where(Parcel.status == status_filter)

    result = await db.execute(query)
    parcels = result.scalars().all()

    return templates.TemplateResponse(
        "parcels/list.html",
        {
            "request": request,
            "user": user,
            "parcels": parcels,
            "suche": suche,
            "status_filter": status_filter,
            "ParcelStatus": ParcelStatus,
        },
    )


@router.get("/new", response_class=HTMLResponse)
async def parzelle_neu_seite(request: Request, db: AsyncSession = Depends(get_db)):
    user = await require_user(request, db)
    return templates.TemplateResponse(
        "parcels/form.html",
        {"request": request, "user": user, "parcel": None},
    )


@router.post("/new")
async def parzelle_erstellen(
    request: Request,
    plot_number: str = Form(...),
    area_sqm: str = Form(""),
    notes: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    await require_user(request, db)

    # Doppelte Gartennummer prüfen
    existing = await db.execute(
        select(Parcel).where(Parcel.plot_number == plot_number.strip().upper())
    )
    if existing.scalar_one_or_none():
        user_result = await require_user(request, db)
        return templates.TemplateResponse(
            "parcels/form.html",
            {
                "request": request,
                "user": user_result,
                "parcel": None,
                "fehler": t_for(request, "parcels.form.duplicate_plot_number_error", plot_number=plot_number),
            },
            status_code=400,
        )

    flaeche = None
    if area_sqm.strip():
        try:
            flaeche = float(area_sqm.replace(",", "."))
        except ValueError:
            pass

    parcel = Parcel(
        plot_number=plot_number.strip().upper(),
        area_sqm=flaeche,
        notes=notes.strip() or None,
    )
    db.add(parcel)
    await db.commit()

    return RedirectResponse(f"/parcels/{parcel.id}", status_code=302)


@router.get("/{parcel_id}", response_class=HTMLResponse)
async def parzelle_detail(
    parcel_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await require_user(request, db)
    parcel = await _get_parcel_mit_details(db, parcel_id)

    if not parcel:
        raise HTTPException(status_code=404, detail="Parcel nicht gefunden")

    # Alle Mitglieder für Zuordnung
    mitglieder_result = await db.execute(
        select(Member)
        .where(active_member_filter())
        .order_by(Member.last_name, Member.first_name)
    )
    alle_mitglieder = mitglieder_result.scalars().all()

    # Änderungshistorie der Feldwerte
    aenderungen_result = await db.execute(
        select(ChangeHistory)
        .options(selectinload(ChangeHistory.changed_by))
        .where(
            ChangeHistory.entity_type == "Parcel",
            ChangeHistory.entity_id == parcel_id,
        )
        .order_by(ChangeHistory.changed_at.desc())
    )
    aenderungen = aenderungen_result.scalars().all()

    return templates.TemplateResponse(
        "parcels/detail.html",
        {
            "request": request,
            "user": user,
            "parcel": parcel,
            "alle_mitglieder": alle_mitglieder,
            "aenderungen": aenderungen,
            "ParcelStatus": ParcelStatus,
        },
    )


@router.get("/{parcel_id}/edit", response_class=HTMLResponse)
async def parzelle_bearbeiten_seite(
    parcel_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await require_user(request, db)
    parcel = await _get_parcel_mit_details(db, parcel_id)

    if not parcel:
        raise HTTPException(status_code=404)

    return templates.TemplateResponse(
        "parcels/form.html",
        {"request": request, "user": user, "parcel": parcel},
    )


@router.post("/{parcel_id}/edit")
async def parzelle_aktualisieren(
    parcel_id: str,
    request: Request,
    plot_number: str = Form(...),
    area_sqm: str = Form(""),
    status: str = Form("ACTIVE"),
    termination_note: str = Form(""),
    notes: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    user = await require_user(request, db)
    parcel = await _get_parcel_mit_details(db, parcel_id)

    if not parcel:
        raise HTTPException(status_code=404)

    tracker = ChangeTracker(
        parcel, "Parcel",
        ["plot_number", "area_sqm", "status", "termination_note", "notes"]
    )

    flaeche = None
    if area_sqm.strip():
        try:
            flaeche = float(area_sqm.replace(",", "."))
        except ValueError:
            pass

    parcel.plot_number = plot_number.strip().upper()
    parcel.area_sqm = flaeche
    parcel.notes = notes.strip() or None

    if status in [s.value for s in ParcelStatus]:
        parcel.status = ParcelStatus(status)

    parcel.termination_note = termination_note.strip() or None

    await tracker.commit(db, user.id)
    await db.commit()
    return RedirectResponse(f"/parcels/{parcel_id}", status_code=302)


@router.post("/{parcel_id}/permanently-delete")
async def parzelle_endgueltig_loeschen(
    parcel_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Löscht eine Parcel unwiderruflich aus der Datenbank – anders als der
    Status "Gelöscht" (Soft-Delete), der die Historie erhält. Gedacht für
    versehentlich angelegte Test-/Demo-Datensätze, nicht für den
    Normalbetrieb.
    """
    await require_user(request, db)

    parcel = await _get_parcel_mit_details(db, parcel_id)
    if not parcel:
        raise HTTPException(status_code=404)

    await db.delete(parcel)
    await db.commit()
    import urllib.parse
    meldung = urllib.parse.quote(
        t_for(request, "parcels.list.delete_permanently_message", plot_number=parcel.plot_number)
    )
    return RedirectResponse(f"/parcels/?meldung={meldung}", status_code=302)


# ---------------------------------------------------------------------------
# Mitglieder-Zuordnung
# ---------------------------------------------------------------------------

@router.post("/{parcel_id}/member/assign")
async def mitglied_zuordnen(
    parcel_id: str,
    request: Request,
    member_id: str = Form(...),
    is_primary_tenant: bool = Form(False),
    assigned_from: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    await require_user(request, db)

    # Bereits (auch historisch) zugeordnet?
    existing = await db.execute(
        select(MemberParcel).where(
            MemberParcel.parcel_id == parcel_id,
            MemberParcel.member_id == member_id,
        )
    )
    zuordnung = existing.scalar_one_or_none()

    if zuordnung:
        if zuordnung.assigned_until is None:
            # Bereits aktiv zugeordnet, nichts zu tun
            return RedirectResponse(f"/parcels/{parcel_id}", status_code=302)
        # Frühere (beendete) Zuordnung reaktivieren statt Duplikat anzulegen
        zuordnung.assigned_until = None
        zuordnung.assigned_from = date.fromisoformat(assigned_from) if assigned_from else date.today()
        zuordnung.is_primary_tenant = is_primary_tenant
    else:
        zuordnung = MemberParcel(
            parcel_id=parcel_id,
            member_id=member_id,
            is_primary_tenant=is_primary_tenant,
            assigned_from=date.fromisoformat(assigned_from) if assigned_from else None,
        )
        db.add(zuordnung)

    await db.commit()
    return RedirectResponse(f"/parcels/{parcel_id}", status_code=302)


@router.get("/{parcel_id}/member/{assignment_id}/edit", response_class=HTMLResponse)
async def mitglied_zuordnung_bearbeiten_seite(
    parcel_id: str,
    assignment_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await require_user(request, db)

    result = await db.execute(
        select(MemberParcel)
        .options(selectinload(MemberParcel.member))
        .where(MemberParcel.id == assignment_id, MemberParcel.parcel_id == parcel_id)
    )
    zuordnung = result.scalar_one_or_none()
    if not zuordnung:
        raise HTTPException(status_code=404, detail="Zuordnung nicht gefunden")

    parcel = await _get_parcel_mit_details(db, parcel_id)

    return templates.TemplateResponse(
        "parcels/assignment_form.html",
        {
            "request": request,
            "user": user,
            "zuordnung": zuordnung,
            "parcel": parcel,
        },
    )


@router.post("/{parcel_id}/member/{assignment_id}/edit")
async def mitglied_zuordnung_aktualisieren(
    parcel_id: str,
    assignment_id: str,
    request: Request,
    is_primary_tenant: bool = Form(False),
    assigned_from: str = Form(""),
    assigned_until: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    await require_user(request, db)

    result = await db.execute(
        select(MemberParcel).where(
            MemberParcel.id == assignment_id, MemberParcel.parcel_id == parcel_id
        )
    )
    zuordnung = result.scalar_one_or_none()
    if not zuordnung:
        raise HTTPException(status_code=404, detail="Zuordnung nicht gefunden")

    zuordnung.is_primary_tenant = is_primary_tenant
    zuordnung.assigned_from = date.fromisoformat(assigned_from) if assigned_from.strip() else None
    zuordnung.assigned_until = date.fromisoformat(assigned_until) if assigned_until.strip() else None

    await db.commit()
    return RedirectResponse(f"/parcels/{parcel_id}", status_code=302)


@router.post("/{parcel_id}/member/{assignment_id}/remove")
async def mitglied_entfernen(
    parcel_id: str,
    assignment_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Beendet eine Pächter-Zuordnung (setzt assigned_until), löscht sie aber
    NICHT aus der Datenbank – so bleibt die Historie erhalten (wer war
    von wann bis wann Pächter dieser Parcel).
    """
    await require_user(request, db)
    result = await db.execute(
        select(MemberParcel).where(
            MemberParcel.id == assignment_id,
            MemberParcel.parcel_id == parcel_id,
        )
    )
    zuordnung = result.scalar_one_or_none()
    if zuordnung and zuordnung.assigned_until is None:
        zuordnung.assigned_until = date.today()
        await db.commit()
    return RedirectResponse(f"/parcels/{parcel_id}", status_code=302)


# ---------------------------------------------------------------------------
# CSV-Export
# ---------------------------------------------------------------------------

@router.get("/export/csv")
async def parzellen_export_csv(request: Request, db: AsyncSession = Depends(get_db)):
    await require_user(request, db)

    result = await db.execute(
        select(Parcel)
        .options(
            selectinload(Parcel.member_assignments).selectinload(MemberParcel.member)
        )
        .order_by(Parcel.plot_number)
    )
    parcels = result.scalars().all()

    output = io.StringIO()
    writer = csv.writer(output, delimiter=";")
    writer.writerow([
        "Gartennummer", "Fläche (qm)", "Status",
        "Kündigungsnotiz",
        "Mitglieder (Hauptpächter zuerst)", "Notizen"
    ])

    for p in parcels:
        mitglieder_str = "; ".join(
            f"{z.member.full_name}{'*' if z.is_primary_tenant else ''}"
            for z in sorted(p.member_assignments, key=lambda z: not z.is_primary_tenant)
        )
        writer.writerow([
            p.plot_number,
            str(p.area_sqm).replace(".", ",") if p.area_sqm else "",
            p.status.value,
            p.termination_note or "",
            mitglieder_str,
            p.notes or "",
        ])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=parcels.csv"},
    )


# ---------------------------------------------------------------------------
# CSV-Import
# ---------------------------------------------------------------------------

@router.post("/import/csv")
async def parzellen_import_csv(
    request: Request,
    datei: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    await require_user(request, db)

    inhalt = await datei.read()
    try:
        text = inhalt.decode("utf-8-sig")  # BOM-safe
    except UnicodeDecodeError:
        text = inhalt.decode("latin-1")

    try:
        delimiter = csv.Sniffer().sniff(text[:2048], delimiters=";,").delimiter
    except csv.Error:
        delimiter = ";"

    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    if reader.fieldnames:
        reader.fieldnames = [f.strip() if f else f for f in reader.fieldnames]

    erstellt = 0
    uebersprungen = 0
    fehlende_gartennummer = 0

    for zeile in reader:
        plot_number = (zeile.get("Gartennummer") or "").strip().upper()
        if not plot_number:
            fehlende_gartennummer += 1
            continue

        existing = await db.execute(
            select(Parcel).where(Parcel.plot_number == plot_number)
        )
        if existing.scalar_one_or_none():
            uebersprungen += 1
            continue

        flaeche = None
        flaeche_str = (zeile.get("Fläche (qm)") or "").replace(",", ".").strip()
        if flaeche_str:
            try:
                flaeche = float(flaeche_str)
            except ValueError:
                pass

        status_str = (zeile.get("Status") or "ACTIVE").strip().upper()
        status = ParcelStatus.ACTIVE
        if status_str in [s.value for s in ParcelStatus]:
            status = ParcelStatus(status_str)

        parcel = Parcel(
            plot_number=plot_number,
            area_sqm=flaeche,
            status=status,
            notes=(zeile.get("Notizen") or "").strip() or None,
        )
        db.add(parcel)
        erstellt += 1

    await db.commit()

    meldung = t_for(request, "parcels.list.csv_import_summary", created=erstellt, skipped=uebersprungen)
    if fehlende_gartennummer:
        meldung += t_for(request, "parcels.list.csv_import_missing_plot_number", count=fehlende_gartennummer)

    import urllib.parse
    return RedirectResponse(
        f"/parcels/?meldung={urllib.parse.quote(meldung)}",
        status_code=302,
    )
