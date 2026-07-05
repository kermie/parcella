"""
Parzellen-Router: Liste, Anlegen, Bearbeiten, Zuordnungen, CSV-Import/Export.
"""
import csv
import io
from datetime import date, datetime, timezone
from typing import Optional

from fastapi import APIRouter, Request, Form, Depends, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_
from sqlalchemy.orm import selectinload

from app.database import get_db, aktives_mitglied_filter
from app.models import (
    Parzelle, ParzelleStatus, MitgliedParzelle, Mitglied, Aenderungshistorie
)
from app.auth import require_user
from app.aenderungstracker import AenderungsTracker

router = APIRouter(prefix="/parzellen", tags=["parzellen"])
templates = Jinja2Templates(directory="app/templates")


async def _get_parzelle_mit_details(db: AsyncSession, parzelle_id: str) -> Optional[Parzelle]:
    result = await db.execute(
        select(Parzelle)
        .options(
            selectinload(Parzelle.mitglieder_zuordnungen).selectinload(MitgliedParzelle.mitglied)
        )
        .where(Parzelle.id == parzelle_id)
    )
    return result.scalar_one_or_none()


@router.get("/", response_class=HTMLResponse)
async def parzellen_liste(
    request: Request,
    suche: str = "",
    status_filter: str = "",
    db: AsyncSession = Depends(get_db),
):
    benutzer = await require_user(request, db)

    query = (
        select(Parzelle)
        .options(
            selectinload(Parzelle.mitglieder_zuordnungen).selectinload(MitgliedParzelle.mitglied)
        )
        .order_by(Parzelle.gartennummer)
    )

    if suche:
        query = query.where(Parzelle.gartennummer.ilike(f"%{suche}%"))

    if status_filter and status_filter in [s.value for s in ParzelleStatus]:
        query = query.where(Parzelle.status == status_filter)

    result = await db.execute(query)
    parzellen = result.scalars().all()

    return templates.TemplateResponse(
        "parcels/liste.html",
        {
            "request": request,
            "benutzer": benutzer,
            "parzellen": parzellen,
            "suche": suche,
            "status_filter": status_filter,
            "ParzelleStatus": ParzelleStatus,
        },
    )


@router.get("/neu", response_class=HTMLResponse)
async def parzelle_neu_seite(request: Request, db: AsyncSession = Depends(get_db)):
    benutzer = await require_user(request, db)
    return templates.TemplateResponse(
        "parcels/formular.html",
        {"request": request, "benutzer": benutzer, "parzelle": None},
    )


@router.post("/neu")
async def parzelle_erstellen(
    request: Request,
    gartennummer: str = Form(...),
    flaeche_qm: str = Form(""),
    notizen: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    await require_user(request, db)

    # Doppelte Gartennummer prüfen
    existing = await db.execute(
        select(Parzelle).where(Parzelle.gartennummer == gartennummer.strip().upper())
    )
    if existing.scalar_one_or_none():
        benutzer_result = await require_user(request, db)
        return templates.TemplateResponse(
            "parcels/formular.html",
            {
                "request": request,
                "benutzer": benutzer_result,
                "parzelle": None,
                "fehler": f"Gartennummer '{gartennummer}' existiert bereits.",
            },
            status_code=400,
        )

    flaeche = None
    if flaeche_qm.strip():
        try:
            flaeche = float(flaeche_qm.replace(",", "."))
        except ValueError:
            pass

    parzelle = Parzelle(
        gartennummer=gartennummer.strip().upper(),
        flaeche_qm=flaeche,
        notizen=notizen.strip() or None,
    )
    db.add(parzelle)
    await db.commit()

    return RedirectResponse(f"/parzellen/{parzelle.id}", status_code=302)


@router.get("/{parzelle_id}", response_class=HTMLResponse)
async def parzelle_detail(
    parzelle_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    benutzer = await require_user(request, db)
    parzelle = await _get_parzelle_mit_details(db, parzelle_id)

    if not parzelle:
        raise HTTPException(status_code=404, detail="Parzelle nicht gefunden")

    # Alle Mitglieder für Zuordnung
    mitglieder_result = await db.execute(
        select(Mitglied)
        .where(aktives_mitglied_filter())
        .order_by(Mitglied.nachname, Mitglied.vorname)
    )
    alle_mitglieder = mitglieder_result.scalars().all()

    # Änderungshistorie der Feldwerte
    aenderungen_result = await db.execute(
        select(Aenderungshistorie)
        .options(selectinload(Aenderungshistorie.geaendert_von))
        .where(
            Aenderungshistorie.entitaet_typ == "Parzelle",
            Aenderungshistorie.entitaet_id == parzelle_id,
        )
        .order_by(Aenderungshistorie.geaendert_am.desc())
    )
    aenderungen = aenderungen_result.scalars().all()

    return templates.TemplateResponse(
        "parcels/detail.html",
        {
            "request": request,
            "benutzer": benutzer,
            "parzelle": parzelle,
            "alle_mitglieder": alle_mitglieder,
            "aenderungen": aenderungen,
            "ParzelleStatus": ParzelleStatus,
        },
    )


@router.get("/{parzelle_id}/bearbeiten", response_class=HTMLResponse)
async def parzelle_bearbeiten_seite(
    parzelle_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    benutzer = await require_user(request, db)
    parzelle = await _get_parzelle_mit_details(db, parzelle_id)

    if not parzelle:
        raise HTTPException(status_code=404)

    return templates.TemplateResponse(
        "parcels/formular.html",
        {"request": request, "benutzer": benutzer, "parzelle": parzelle},
    )


@router.post("/{parzelle_id}/bearbeiten")
async def parzelle_aktualisieren(
    parzelle_id: str,
    request: Request,
    gartennummer: str = Form(...),
    flaeche_qm: str = Form(""),
    status: str = Form("AKTIV"),
    kuendigung_notiz: str = Form(""),
    notizen: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    benutzer = await require_user(request, db)
    parzelle = await _get_parzelle_mit_details(db, parzelle_id)

    if not parzelle:
        raise HTTPException(status_code=404)

    tracker = AenderungsTracker(
        parzelle, "Parzelle",
        ["gartennummer", "flaeche_qm", "status", "kuendigung_notiz", "notizen"]
    )

    flaeche = None
    if flaeche_qm.strip():
        try:
            flaeche = float(flaeche_qm.replace(",", "."))
        except ValueError:
            pass

    parzelle.gartennummer = gartennummer.strip().upper()
    parzelle.flaeche_qm = flaeche
    parzelle.notizen = notizen.strip() or None

    if status in [s.value for s in ParzelleStatus]:
        parzelle.status = ParzelleStatus(status)

    parzelle.kuendigung_notiz = kuendigung_notiz.strip() or None

    await tracker.commit(db, benutzer.id)
    await db.commit()
    return RedirectResponse(f"/parzellen/{parzelle_id}", status_code=302)


# ---------------------------------------------------------------------------
# Mitglieder-Zuordnung
# ---------------------------------------------------------------------------

@router.post("/{parzelle_id}/mitglied/zuordnen")
async def mitglied_zuordnen(
    parzelle_id: str,
    request: Request,
    mitglied_id: str = Form(...),
    ist_hauptpaechter: bool = Form(False),
    zuordnung_von: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    await require_user(request, db)

    # Bereits (auch historisch) zugeordnet?
    existing = await db.execute(
        select(MitgliedParzelle).where(
            MitgliedParzelle.parzelle_id == parzelle_id,
            MitgliedParzelle.mitglied_id == mitglied_id,
        )
    )
    zuordnung = existing.scalar_one_or_none()

    if zuordnung:
        if zuordnung.zuordnung_bis is None:
            # Bereits aktiv zugeordnet, nichts zu tun
            return RedirectResponse(f"/parzellen/{parzelle_id}", status_code=302)
        # Frühere (beendete) Zuordnung reaktivieren statt Duplikat anzulegen
        zuordnung.zuordnung_bis = None
        zuordnung.zuordnung_von = date.fromisoformat(zuordnung_von) if zuordnung_von else date.today()
        zuordnung.ist_hauptpaechter = ist_hauptpaechter
    else:
        zuordnung = MitgliedParzelle(
            parzelle_id=parzelle_id,
            mitglied_id=mitglied_id,
            ist_hauptpaechter=ist_hauptpaechter,
            zuordnung_von=date.fromisoformat(zuordnung_von) if zuordnung_von else None,
        )
        db.add(zuordnung)

    await db.commit()
    return RedirectResponse(f"/parzellen/{parzelle_id}", status_code=302)


@router.get("/{parzelle_id}/mitglied/{zuordnung_id}/bearbeiten", response_class=HTMLResponse)
async def mitglied_zuordnung_bearbeiten_seite(
    parzelle_id: str,
    zuordnung_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    benutzer = await require_user(request, db)

    result = await db.execute(
        select(MitgliedParzelle)
        .options(selectinload(MitgliedParzelle.mitglied))
        .where(MitgliedParzelle.id == zuordnung_id, MitgliedParzelle.parzelle_id == parzelle_id)
    )
    zuordnung = result.scalar_one_or_none()
    if not zuordnung:
        raise HTTPException(status_code=404, detail="Zuordnung nicht gefunden")

    parzelle = await _get_parzelle_mit_details(db, parzelle_id)

    return templates.TemplateResponse(
        "parcels/zuordnung_formular.html",
        {
            "request": request,
            "benutzer": benutzer,
            "zuordnung": zuordnung,
            "parzelle": parzelle,
        },
    )


@router.post("/{parzelle_id}/mitglied/{zuordnung_id}/bearbeiten")
async def mitglied_zuordnung_aktualisieren(
    parzelle_id: str,
    zuordnung_id: str,
    request: Request,
    ist_hauptpaechter: bool = Form(False),
    zuordnung_von: str = Form(""),
    zuordnung_bis: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    await require_user(request, db)

    result = await db.execute(
        select(MitgliedParzelle).where(
            MitgliedParzelle.id == zuordnung_id, MitgliedParzelle.parzelle_id == parzelle_id
        )
    )
    zuordnung = result.scalar_one_or_none()
    if not zuordnung:
        raise HTTPException(status_code=404, detail="Zuordnung nicht gefunden")

    zuordnung.ist_hauptpaechter = ist_hauptpaechter
    zuordnung.zuordnung_von = date.fromisoformat(zuordnung_von) if zuordnung_von.strip() else None
    zuordnung.zuordnung_bis = date.fromisoformat(zuordnung_bis) if zuordnung_bis.strip() else None

    await db.commit()
    return RedirectResponse(f"/parzellen/{parzelle_id}", status_code=302)


@router.post("/{parzelle_id}/mitglied/{zuordnung_id}/entfernen")
async def mitglied_entfernen(
    parzelle_id: str,
    zuordnung_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Beendet eine Pächter-Zuordnung (setzt zuordnung_bis), löscht sie aber
    NICHT aus der Datenbank – so bleibt die Historie erhalten (wer war
    von wann bis wann Pächter dieser Parzelle).
    """
    await require_user(request, db)
    result = await db.execute(
        select(MitgliedParzelle).where(
            MitgliedParzelle.id == zuordnung_id,
            MitgliedParzelle.parzelle_id == parzelle_id,
        )
    )
    zuordnung = result.scalar_one_or_none()
    if zuordnung and zuordnung.zuordnung_bis is None:
        zuordnung.zuordnung_bis = date.today()
        await db.commit()
    return RedirectResponse(f"/parzellen/{parzelle_id}", status_code=302)


# ---------------------------------------------------------------------------
# CSV-Export
# ---------------------------------------------------------------------------

@router.get("/export/csv")
async def parzellen_export_csv(request: Request, db: AsyncSession = Depends(get_db)):
    await require_user(request, db)

    result = await db.execute(
        select(Parzelle)
        .options(
            selectinload(Parzelle.mitglieder_zuordnungen).selectinload(MitgliedParzelle.mitglied)
        )
        .order_by(Parzelle.gartennummer)
    )
    parzellen = result.scalars().all()

    output = io.StringIO()
    writer = csv.writer(output, delimiter=";")
    writer.writerow([
        "Gartennummer", "Fläche (qm)", "Status",
        "Kündigungsnotiz",
        "Mitglieder (Hauptpächter zuerst)", "Notizen"
    ])

    for p in parzellen:
        mitglieder_str = "; ".join(
            f"{z.mitglied.vollname}{'*' if z.ist_hauptpaechter else ''}"
            for z in sorted(p.mitglieder_zuordnungen, key=lambda z: not z.ist_hauptpaechter)
        )
        writer.writerow([
            p.gartennummer,
            str(p.flaeche_qm).replace(".", ",") if p.flaeche_qm else "",
            p.status.value,
            p.kuendigung_notiz or "",
            mitglieder_str,
            p.notizen or "",
        ])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=parzellen.csv"},
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
    text = inhalt.decode("utf-8-sig")  # BOM-safe
    reader = csv.DictReader(io.StringIO(text), delimiter=";")

    erstellt = 0
    uebersprungen = 0

    for zeile in reader:
        gartennummer = zeile.get("Gartennummer", "").strip().upper()
        if not gartennummer:
            continue

        existing = await db.execute(
            select(Parzelle).where(Parzelle.gartennummer == gartennummer)
        )
        if existing.scalar_one_or_none():
            uebersprungen += 1
            continue

        flaeche = None
        flaeche_str = zeile.get("Fläche (qm)", "").replace(",", ".").strip()
        if flaeche_str:
            try:
                flaeche = float(flaeche_str)
            except ValueError:
                pass

        status_str = zeile.get("Status", "AKTIV").strip().upper()
        status = ParzelleStatus.AKTIV
        if status_str in [s.value for s in ParzelleStatus]:
            status = ParzelleStatus(status_str)

        parzelle = Parzelle(
            gartennummer=gartennummer,
            flaeche_qm=flaeche,
            status=status,
            notizen=zeile.get("Notizen", "").strip() or None,
        )
        db.add(parzelle)
        erstellt += 1

    await db.commit()
    return RedirectResponse(
        f"/parzellen/?meldung={erstellt} importiert, {uebersprungen} übersprungen",
        status_code=302,
    )
