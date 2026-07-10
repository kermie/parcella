"""
Mitglieder-Router: Liste, Anlegen, Bearbeiten, CSV-Import/Export.
"""
import csv
import io
from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Request, Form, Depends, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_
from sqlalchemy.orm import selectinload

from app.database import get_db, aktives_mitglied_filter
from app.models import Mitglied, MitgliedTelefon, MitgliedEmail, MitgliedParzelle, Parzelle
from app.auth import get_current_user, require_user

router = APIRouter(prefix="/mitglieder", tags=["mitglieder"])
templates = Jinja2Templates(directory="app/templates")


async def _get_mitglied_mit_details(db: AsyncSession, mitglied_id: str) -> Optional[Mitglied]:
    result = await db.execute(
        select(Mitglied)
        .options(
            selectinload(Mitglied.telefonnummern),
            selectinload(Mitglied.email_adressen),
            selectinload(Mitglied.parzellen_zuordnungen).selectinload(MitgliedParzelle.parzelle),
        )
        .where(Mitglied.id == mitglied_id, Mitglied.deleted_at.is_(None))
    )
    return result.scalar_one_or_none()


@router.get("/", response_class=HTMLResponse)
async def mitglieder_liste(
    request: Request,
    suche: str = "",
    auch_inaktive: bool = False,
    db: AsyncSession = Depends(get_db),
):
    benutzer = await require_user(request, db)

    query = (
        select(Mitglied)
        .options(
            selectinload(Mitglied.email_adressen),
            selectinload(Mitglied.parzellen_zuordnungen).selectinload(MitgliedParzelle.parzelle),
        )
        .order_by(Mitglied.nachname, Mitglied.vorname)
    )

    if auch_inaktive:
        # Alle nicht-gelöschten Mitglieder (inkl. abgelaufene Mitgliedschaften)
        query = query.where(Mitglied.deleted_at.is_(None))
    else:
        query = query.where(aktives_mitglied_filter())

    if suche:
        query = query.where(
            or_(
                Mitglied.vorname.ilike(f"%{suche}%"),
                Mitglied.nachname.ilike(f"%{suche}%"),
                Mitglied.ort.ilike(f"%{suche}%"),
            )
        )

    result = await db.execute(query)
    mitglieder = result.scalars().all()

    return templates.TemplateResponse(
        "members/liste.html",
        {
            "request": request,
            "benutzer": benutzer,
            "mitglieder": mitglieder,
            "suche": suche,
            "auch_inaktive": auch_inaktive,
        },
    )


@router.get("/neu", response_class=HTMLResponse)
async def mitglied_neu_seite(request: Request, db: AsyncSession = Depends(get_db)):
    benutzer = await require_user(request, db)
    return templates.TemplateResponse(
        "members/formular.html",
        {"request": request, "benutzer": benutzer, "mitglied": None},
    )


@router.post("/neu")
async def mitglied_erstellen(
    request: Request,
    vorname: str = Form(...),
    nachname: str = Form(...),
    strasse: str = Form(""),
    plz: str = Form(""),
    ort: str = Form(""),
    geburtsdatum: str = Form(""),
    iban: str = Form(""),
    mitglied_seit: str = Form(""),
    mitglied_bis: str = Form(""),
    email_benachrichtigungen: bool = Form(False),
    notizen: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    benutzer = await require_user(request, db)

    def parse_datum(s: str) -> Optional[date]:
        if s:
            try:
                return date.fromisoformat(s)
            except ValueError:
                pass
        return None

    mitglied = Mitglied(
        vorname=vorname.strip(),
        nachname=nachname.strip(),
        strasse=strasse.strip() or None,
        plz=plz.strip() or None,
        ort=ort.strip() or None,
        geburtsdatum=parse_datum(geburtsdatum),
        iban=iban.strip() or None,
        mitglied_seit=parse_datum(mitglied_seit),
        mitglied_bis=parse_datum(mitglied_bis),
        email_benachrichtigungen=email_benachrichtigungen,
        notizen=notizen.strip() or None,
    )
    db.add(mitglied)
    await db.commit()

    return RedirectResponse(f"/mitglieder/{mitglied.id}", status_code=302)


@router.get("/{mitglied_id}", response_class=HTMLResponse)
async def mitglied_detail(
    mitglied_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    benutzer = await require_user(request, db)
    mitglied = await _get_mitglied_mit_details(db, mitglied_id)

    if not mitglied:
        raise HTTPException(status_code=404, detail="Mitglied nicht gefunden")

    # Alle aktiven Parzellen für Zuordnung
    parzellen_result = await db.execute(
        select(Parzelle).order_by(Parzelle.gartennummer)
    )
    alle_parzellen = parzellen_result.scalars().all()

    return templates.TemplateResponse(
        "members/detail.html",
        {
            "request": request,
            "benutzer": benutzer,
            "mitglied": mitglied,
            "alle_parzellen": alle_parzellen,
        },
    )


@router.get("/{mitglied_id}/bearbeiten", response_class=HTMLResponse)
async def mitglied_bearbeiten_seite(
    mitglied_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    benutzer = await require_user(request, db)
    mitglied = await _get_mitglied_mit_details(db, mitglied_id)

    if not mitglied:
        raise HTTPException(status_code=404, detail="Mitglied nicht gefunden")

    return templates.TemplateResponse(
        "members/formular.html",
        {"request": request, "benutzer": benutzer, "mitglied": mitglied},
    )


@router.post("/{mitglied_id}/bearbeiten")
async def mitglied_aktualisieren(
    mitglied_id: str,
    request: Request,
    vorname: str = Form(...),
    nachname: str = Form(...),
    strasse: str = Form(""),
    plz: str = Form(""),
    ort: str = Form(""),
    geburtsdatum: str = Form(""),
    iban: str = Form(""),
    mitglied_seit: str = Form(""),
    mitglied_bis: str = Form(""),
    email_benachrichtigungen: bool = Form(False),
    notizen: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    await require_user(request, db)
    mitglied = await _get_mitglied_mit_details(db, mitglied_id)

    if not mitglied:
        raise HTTPException(status_code=404)

    def parse_datum(s: str) -> Optional[date]:
        if s:
            try:
                return date.fromisoformat(s)
            except ValueError:
                pass
        return None

    mitglied.vorname = vorname.strip()
    mitglied.nachname = nachname.strip()
    mitglied.strasse = strasse.strip() or None
    mitglied.plz = plz.strip() or None
    mitglied.ort = ort.strip() or None
    mitglied.geburtsdatum = parse_datum(geburtsdatum)
    mitglied.iban = iban.strip() or None
    mitglied.mitglied_seit = parse_datum(mitglied_seit)
    mitglied.mitglied_bis = parse_datum(mitglied_bis)
    mitglied.email_benachrichtigungen = email_benachrichtigungen
    mitglied.notizen = notizen.strip() or None

    await db.commit()
    return RedirectResponse(f"/mitglieder/{mitglied_id}", status_code=302)


# ---------------------------------------------------------------------------
# Telefon / E-Mail-Verwaltung
# ---------------------------------------------------------------------------

@router.post("/{mitglied_id}/telefon/hinzufuegen")
async def telefon_hinzufuegen(
    mitglied_id: str,
    request: Request,
    nummer: str = Form(...),
    bezeichnung: str = Form(""),
    ist_primaer: bool = Form(False),
    db: AsyncSession = Depends(get_db),
):
    await require_user(request, db)
    telefon = MitgliedTelefon(
        mitglied_id=mitglied_id,
        nummer=nummer.strip(),
        bezeichnung=bezeichnung.strip() or None,
        ist_primaer=ist_primaer,
    )
    db.add(telefon)
    await db.commit()
    return RedirectResponse(f"/mitglieder/{mitglied_id}", status_code=302)


@router.post("/{mitglied_id}/telefon/{telefon_id}/loeschen")
async def telefon_loeschen(
    mitglied_id: str,
    telefon_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    await require_user(request, db)
    result = await db.execute(
        select(MitgliedTelefon).where(
            MitgliedTelefon.id == telefon_id,
            MitgliedTelefon.mitglied_id == mitglied_id,
        )
    )
    telefon = result.scalar_one_or_none()
    if telefon:
        await db.delete(telefon)
        await db.commit()
    return RedirectResponse(f"/mitglieder/{mitglied_id}", status_code=302)


@router.post("/{mitglied_id}/email/hinzufuegen")
async def email_hinzufuegen(
    mitglied_id: str,
    request: Request,
    adresse: str = Form(...),
    bezeichnung: str = Form(""),
    ist_primaer: bool = Form(False),
    db: AsyncSession = Depends(get_db),
):
    await require_user(request, db)
    email_obj = MitgliedEmail(
        mitglied_id=mitglied_id,
        adresse=adresse.strip().lower(),
        bezeichnung=bezeichnung.strip() or None,
        ist_primaer=ist_primaer,
    )
    db.add(email_obj)
    await db.commit()
    return RedirectResponse(f"/mitglieder/{mitglied_id}", status_code=302)


@router.post("/{mitglied_id}/email/{email_id}/loeschen")
async def email_loeschen(
    mitglied_id: str,
    email_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    await require_user(request, db)
    result = await db.execute(
        select(MitgliedEmail).where(
            MitgliedEmail.id == email_id,
            MitgliedEmail.mitglied_id == mitglied_id,
        )
    )
    email_obj = result.scalar_one_or_none()
    if email_obj:
        await db.delete(email_obj)
        await db.commit()
    return RedirectResponse(f"/mitglieder/{mitglied_id}", status_code=302)


# ---------------------------------------------------------------------------
# CSV-Export
# ---------------------------------------------------------------------------

@router.get("/export/csv")
async def mitglieder_export_csv(request: Request, db: AsyncSession = Depends(get_db)):
    await require_user(request, db)

    result = await db.execute(
        select(Mitglied)
        .options(
            selectinload(Mitglied.email_adressen),
            selectinload(Mitglied.telefonnummern),
            selectinload(Mitglied.parzellen_zuordnungen).selectinload(MitgliedParzelle.parzelle),
        )
        .where(aktives_mitglied_filter())
        .order_by(Mitglied.nachname, Mitglied.vorname)
    )
    mitglieder = result.scalars().all()

    output = io.StringIO()
    writer = csv.writer(output, delimiter=";")
    writer.writerow([
        "Vorname", "Nachname", "Strasse", "PLZ", "Ort",
        "Geburtsdatum", "IBAN", "Mitglied seit", "Mitglied bis",
        "E-Mail-Benachrichtigungen", "E-Mail-Adressen", "Telefonnummern",
        "Parzellen", "Notizen"
    ])

    for m in mitglieder:
        emails = "; ".join(e.adresse for e in m.email_adressen)
        telefone = "; ".join(t.nummer for t in m.telefonnummern)
        parzellen = "; ".join(z.parzelle.gartennummer for z in m.parzellen_zuordnungen)
        writer.writerow([
            m.vorname, m.nachname, m.strasse or "", m.plz or "", m.ort or "",
            m.geburtsdatum.isoformat() if m.geburtsdatum else "",
            m.iban or "",
            m.mitglied_seit.isoformat() if m.mitglied_seit else "",
            m.mitglied_bis.isoformat() if m.mitglied_bis else "",
            "Ja" if m.email_benachrichtigungen else "Nein",
            emails, telefone, parzellen,
            m.notizen or "",
        ])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=mitglieder.csv"},
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
        vorname = (zeile.get("Vorname") or "").strip()
        nachname = (zeile.get("Nachname") or "").strip()

        if not vorname or not nachname:
            fehler.append(f"Zeile {zeilennr}: Vor- oder Nachname fehlt – übersprungen.")
            continue

        # Duplikat-Erkennung: gleicher Vor- + Nachname + Geburtsdatum
        geburtsdatum = parse_datum(zeile.get("Geburtsdatum") or "")
        existing_query = select(Mitglied).where(
            Mitglied.vorname == vorname,
            Mitglied.nachname == nachname,
            Mitglied.deleted_at.is_(None),
        )
        if geburtsdatum:
            existing_query = existing_query.where(Mitglied.geburtsdatum == geburtsdatum)

        existing_result = await db.execute(existing_query)
        existing = existing_result.scalars().first()

        email_ben_str = (zeile.get("E-Mail-Benachrichtigungen") or "Ja").strip().lower()
        email_benachrichtigungen = email_ben_str not in ("nein", "no", "false", "0")

        felder = dict(
            vorname=vorname,
            nachname=nachname,
            strasse=(zeile.get("Strasse") or "").strip() or None,
            plz=(zeile.get("PLZ") or "").strip() or None,
            ort=(zeile.get("Ort") or "").strip() or None,
            geburtsdatum=geburtsdatum,
            iban=(zeile.get("IBAN") or "").strip() or None,
            mitglied_seit=parse_datum(zeile.get("Mitglied seit") or ""),
            mitglied_bis=parse_datum(zeile.get("Mitglied bis") or ""),
            email_benachrichtigungen=email_benachrichtigungen,
            notizen=(zeile.get("Notizen") or "").strip() or None,
        )

        if existing:
            # Vorhandenes Mitglied aktualisieren
            for k, v in felder.items():
                setattr(existing, k, v)
            mitglied = existing
            aktualisiert += 1
        else:
            mitglied = Mitglied(**felder)
            db.add(mitglied)
            await db.flush()  # ID generieren für Untereinträge
            erstellt += 1

        # E-Mail-Adressen (Semikolon-getrennt in einer Zelle)
        emails_str = (zeile.get("E-Mail-Adressen") or "").strip()
        if emails_str and not existing:
            for i, adresse in enumerate(emails_str.split(";")):
                adresse = adresse.strip().lower()
                if adresse:
                    db.add(MitgliedEmail(
                        mitglied_id=mitglied.id,
                        adresse=adresse,
                        ist_primaer=(i == 0),
                    ))

        # Telefonnummern (Semikolon-getrennt in einer Zelle)
        telefone_str = (zeile.get("Telefonnummern") or "").strip()
        if telefone_str and not existing:
            for i, nummer in enumerate(telefone_str.split(";")):
                nummer = nummer.strip()
                if nummer:
                    db.add(MitgliedTelefon(
                        mitglied_id=mitglied.id,
                        nummer=nummer,
                        ist_primaer=(i == 0),
                    ))

    await db.commit()

    meldung = f"{erstellt} neu importiert, {aktualisiert} aktualisiert"
    if fehler:
        meldung += f", {len(fehler)} Fehler"
        # Erste paar Fehlerdetails anzeigen, damit man die Ursache sofort sieht
        meldung += " – " + " | ".join(fehler[:3])
        if len(fehler) > 3:
            meldung += f" (und {len(fehler) - 3} weitere)"

    import urllib.parse
    return RedirectResponse(
        f"/mitglieder/?meldung={urllib.parse.quote(meldung)}",
        status_code=302,
    )
