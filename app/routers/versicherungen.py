"""
Versicherungsmodul-Router: Konfiguration (Pakete, Beträge), Parzellen-Verwaltung,
Auswertung.
"""
import csv
import io
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Optional

from fastapi import APIRouter, Request, Form, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models import (
    SachversicherungPaket, VersicherungsKonfiguration, ParzelleVersicherung,
    UnfallversicherungZusatzperson, Parzelle, ParzelleStatus, MitgliedParzelle, Mitglied,
)
from app.auth import require_user
from app.module_flags import require_modul
from app.versicherung_utils import haushalts_gruppierung, berechne_versicherungskosten

router = APIRouter(
    prefix="/versicherungen",
    tags=["versicherungen"],
    dependencies=[Depends(require_modul("versicherungen"))],
)
templates = Jinja2Templates(directory="app/templates")


def _parse_dezimal(wert: str) -> Optional[Decimal]:
    wert = wert.strip().replace(",", ".")
    if not wert:
        return None
    try:
        return Decimal(wert)
    except InvalidOperation:
        return None


async def _get_konfiguration(db: AsyncSession, jahr: int) -> Optional[VersicherungsKonfiguration]:
    result = await db.execute(
        select(VersicherungsKonfiguration).where(VersicherungsKonfiguration.jahr == jahr)
    )
    return result.scalar_one_or_none()


async def _get_pakete(db: AsyncSession, jahr: int) -> list:
    result = await db.execute(
        select(SachversicherungPaket)
        .where(SachversicherungPaket.jahr == jahr)
        .order_by(SachversicherungPaket.reihenfolge, SachversicherungPaket.betrag_eur)
    )
    return result.scalars().all()


async def _get_or_create_pv(db: AsyncSession, parzelle_id: str, jahr: int) -> ParzelleVersicherung:
    result = await db.execute(
        select(ParzelleVersicherung)
        .options(
            selectinload(ParzelleVersicherung.sach_paket),
            selectinload(ParzelleVersicherung.zusatzpersonen),
        )
        .where(ParzelleVersicherung.parzelle_id == parzelle_id, ParzelleVersicherung.jahr == jahr)
    )
    pv = result.scalar_one_or_none()
    if not pv:
        pv = ParzelleVersicherung(parzelle_id=parzelle_id, jahr=jahr)
        db.add(pv)
        await db.commit()
        # Frisch angelegte Zeile mit eager-geladenen Beziehungen neu laden.
        # Ohne das würde ein späterer Zugriff auf pv.sach_paket/pv.zusatzpersonen
        # einen synchronen Lazy-Load auslösen, der mit dem asynchronen
        # Datenbanktreiber zu "MissingGreenlet" führt.
        result = await db.execute(
            select(ParzelleVersicherung)
            .options(
                selectinload(ParzelleVersicherung.sach_paket),
                selectinload(ParzelleVersicherung.zusatzpersonen),
            )
            .where(ParzelleVersicherung.id == pv.id)
        )
        pv = result.scalar_one()
    return pv


# ---------------------------------------------------------------------------
# Übersicht
# ---------------------------------------------------------------------------

@router.get("/", response_class=HTMLResponse)
async def versicherungen_uebersicht(
    request: Request,
    jahr: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
):
    benutzer = await require_user(request, db)
    if not jahr:
        jahr = date.today().year

    konfiguration = await _get_konfiguration(db, jahr)
    pakete = await _get_pakete(db, jahr)

    pv_result = await db.execute(
        select(ParzelleVersicherung)
        .options(selectinload(ParzelleVersicherung.sach_paket), selectinload(ParzelleVersicherung.zusatzpersonen))
        .where(ParzelleVersicherung.jahr == jahr)
    )
    alle_pv = pv_result.scalars().all()

    anzahl_sach = sum(1 for pv in alle_pv if pv.hat_sachversicherung)
    anzahl_unfall = sum(1 for pv in alle_pv if pv.hat_unfallversicherung)

    summe_sach = Decimal("0")
    summe_unfall = Decimal("0")
    for pv in alle_pv:
        kosten = berechne_versicherungskosten(pv, konfiguration)
        summe_sach += kosten["sach_kosten"]
        summe_unfall += kosten["unfall_kosten"]

    jahre_result = await db.execute(
        select(VersicherungsKonfiguration.jahr).order_by(VersicherungsKonfiguration.jahr.desc())
    )
    verfuegbare_jahre = [r[0] for r in jahre_result.all()]
    if jahr not in verfuegbare_jahre:
        verfuegbare_jahre.insert(0, jahr)

    return templates.TemplateResponse("versicherungen/uebersicht.html", {
        "request": request, "benutzer": benutzer, "jahr": jahr,
        "verfuegbare_jahre": verfuegbare_jahre,
        "konfiguration": konfiguration, "pakete": pakete,
        "anzahl_sach": anzahl_sach, "anzahl_unfall": anzahl_unfall,
        "summe_sach": summe_sach, "summe_unfall": summe_unfall,
        "summe_gesamt": summe_sach + summe_unfall,
    })


# ---------------------------------------------------------------------------
# Konfiguration: Unfallbeträge + Sachversicherungs-Pakete
# ---------------------------------------------------------------------------

@router.get("/konfiguration", response_class=HTMLResponse)
async def konfiguration_seite(
    request: Request,
    jahr: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
):
    benutzer = await require_user(request, db)
    if not jahr:
        jahr = date.today().year

    konfiguration = await _get_konfiguration(db, jahr)
    pakete = await _get_pakete(db, jahr)

    alle_jahre_result = await db.execute(
        select(VersicherungsKonfiguration.jahr).order_by(VersicherungsKonfiguration.jahr.desc())
    )
    verfuegbare_jahre = [r[0] for r in alle_jahre_result.all()]
    if jahr not in verfuegbare_jahre:
        verfuegbare_jahre.insert(0, jahr)

    return templates.TemplateResponse("versicherungen/konfiguration.html", {
        "request": request, "benutzer": benutzer, "jahr": jahr,
        "verfuegbare_jahre": verfuegbare_jahre,
        "konfiguration": konfiguration, "pakete": pakete,
        "aktuelles_jahr": date.today().year,
    })


@router.post("/konfiguration/speichern")
async def konfiguration_speichern(
    request: Request,
    jahr: int = Form(...),
    unfall_grundbetrag_eur: str = Form(...),
    unfall_zusatzbetrag_eur: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    await require_user(request, db)

    konfiguration = await _get_konfiguration(db, jahr)
    grund = _parse_dezimal(unfall_grundbetrag_eur) or Decimal("0")
    zusatz = _parse_dezimal(unfall_zusatzbetrag_eur) or Decimal("0")

    if konfiguration:
        konfiguration.unfall_grundbetrag_eur = grund
        konfiguration.unfall_zusatzbetrag_eur = zusatz
    else:
        db.add(VersicherungsKonfiguration(
            jahr=jahr, unfall_grundbetrag_eur=grund, unfall_zusatzbetrag_eur=zusatz,
        ))

    await db.commit()
    return RedirectResponse(f"/versicherungen/konfiguration?jahr={jahr}", status_code=302)


@router.post("/konfiguration/pakete/neu")
async def paket_erstellen(
    request: Request,
    jahr: int = Form(...),
    bezeichnung: str = Form(...),
    betrag_eur: str = Form(...),
    reihenfolge: int = Form(0),
    db: AsyncSession = Depends(get_db),
):
    await require_user(request, db)

    betrag = _parse_dezimal(betrag_eur) or Decimal("0")
    db.add(SachversicherungPaket(
        jahr=jahr, bezeichnung=bezeichnung.strip(), betrag_eur=betrag, reihenfolge=reihenfolge,
    ))
    await db.commit()
    return RedirectResponse(f"/versicherungen/konfiguration?jahr={jahr}", status_code=302)


@router.post("/konfiguration/pakete/{paket_id}/bearbeiten")
async def paket_aktualisieren(
    paket_id: str,
    request: Request,
    bezeichnung: str = Form(...),
    betrag_eur: str = Form(...),
    reihenfolge: int = Form(0),
    db: AsyncSession = Depends(get_db),
):
    await require_user(request, db)

    result = await db.execute(select(SachversicherungPaket).where(SachversicherungPaket.id == paket_id))
    paket = result.scalar_one_or_none()
    if not paket:
        raise HTTPException(status_code=404)

    paket.bezeichnung = bezeichnung.strip()
    paket.betrag_eur = _parse_dezimal(betrag_eur) or paket.betrag_eur
    paket.reihenfolge = reihenfolge

    await db.commit()
    return RedirectResponse(f"/versicherungen/konfiguration?jahr={paket.jahr}", status_code=302)


@router.post("/konfiguration/pakete/{paket_id}/loeschen")
async def paket_loeschen(
    paket_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    await require_user(request, db)

    result = await db.execute(select(SachversicherungPaket).where(SachversicherungPaket.id == paket_id))
    paket = result.scalar_one_or_none()
    jahr = paket.jahr if paket else date.today().year
    if paket:
        await db.delete(paket)
        await db.commit()

    return RedirectResponse(f"/versicherungen/konfiguration?jahr={jahr}", status_code=302)


# ---------------------------------------------------------------------------
# Parzellen: Liste, Detail/Bearbeiten
# ---------------------------------------------------------------------------

@router.get("/parzellen", response_class=HTMLResponse)
async def versicherungen_parzellen_liste(
    request: Request,
    jahr: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
):
    benutzer = await require_user(request, db)
    if not jahr:
        jahr = date.today().year

    konfiguration = await _get_konfiguration(db, jahr)

    parzellen_result = await db.execute(
        select(Parzelle)
        .where(Parzelle.status == ParzelleStatus.AKTIV)
        .order_by(Parzelle.gartennummer)
    )
    parzellen = parzellen_result.scalars().all()

    pv_result = await db.execute(
        select(ParzelleVersicherung)
        .options(selectinload(ParzelleVersicherung.sach_paket), selectinload(ParzelleVersicherung.zusatzpersonen))
        .where(ParzelleVersicherung.jahr == jahr)
    )
    pv_by_parzelle = {pv.parzelle_id: pv for pv in pv_result.scalars().all()}

    zeilen = []
    for p in parzellen:
        pv = pv_by_parzelle.get(p.id)
        kosten = berechne_versicherungskosten(pv, konfiguration) if pv else {
            "sach_kosten": Decimal("0"), "unfall_kosten": Decimal("0"), "gesamt": Decimal("0")
        }
        zeilen.append({"parzelle": p, "pv": pv, "kosten": kosten})

    return templates.TemplateResponse("versicherungen/parzellen_liste.html", {
        "request": request, "benutzer": benutzer, "jahr": jahr,
        "zeilen": zeilen,
    })


@router.get("/parzellen/{parzelle_id}", response_class=HTMLResponse)
async def versicherung_detail(
    parzelle_id: str,
    request: Request,
    jahr: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
):
    benutzer = await require_user(request, db)
    if not jahr:
        jahr = date.today().year

    parzelle_result = await db.execute(
        select(Parzelle)
        .options(selectinload(Parzelle.mitglieder_zuordnungen).selectinload(MitgliedParzelle.mitglied))
        .where(Parzelle.id == parzelle_id)
    )
    parzelle = parzelle_result.scalar_one_or_none()
    if not parzelle:
        raise HTTPException(status_code=404, detail="Parzelle nicht gefunden")

    konfiguration = await _get_konfiguration(db, jahr)
    pakete = await _get_pakete(db, jahr)
    pv = await _get_or_create_pv(db, parzelle_id, jahr)

    gruppierung = haushalts_gruppierung(parzelle.mitglieder_zuordnungen)
    zusatz_ids = {z.mitglied_id for z in pv.zusatzpersonen}
    kosten = berechne_versicherungskosten(pv, konfiguration)

    return templates.TemplateResponse("versicherungen/detail.html", {
        "request": request, "benutzer": benutzer, "jahr": jahr,
        "parzelle": parzelle, "pv": pv, "konfiguration": konfiguration, "pakete": pakete,
        "haushalt": gruppierung["haushalt"], "extern": gruppierung["extern"],
        "zusatz_ids": zusatz_ids, "kosten": kosten,
    })


@router.post("/parzellen/{parzelle_id}/speichern")
async def versicherung_speichern(
    parzelle_id: str,
    request: Request,
    jahr: int = Form(...),
    hat_sachversicherung: bool = Form(False),
    sach_paket_id: str = Form(""),
    hat_unfallversicherung: bool = Form(False),
    zusatzpersonen: list[str] = Form([]),
    db: AsyncSession = Depends(get_db),
):
    await require_user(request, db)

    pv = await _get_or_create_pv(db, parzelle_id, jahr)

    pv.hat_sachversicherung = hat_sachversicherung
    pv.sach_paket_id = sach_paket_id.strip() or None if hat_sachversicherung else None

    pv.hat_unfallversicherung = hat_unfallversicherung

    # Zusatzpersonen komplett neu setzen (einfacher als Diff, Datenmenge ist klein)
    for zp in list(pv.zusatzpersonen):
        await db.delete(zp)
    await db.flush()

    if hat_unfallversicherung:
        for mitglied_id in zusatzpersonen:
            db.add(UnfallversicherungZusatzperson(
                parzelle_versicherung_id=pv.id, mitglied_id=mitglied_id,
            ))

    await db.commit()
    return RedirectResponse(f"/versicherungen/parzellen/{parzelle_id}?jahr={jahr}", status_code=302)


# ---------------------------------------------------------------------------
# Auswertung
# ---------------------------------------------------------------------------

@router.get("/auswertung", response_class=HTMLResponse)
async def versicherungen_auswertung(
    request: Request,
    jahr: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
):
    benutzer = await require_user(request, db)
    if not jahr:
        jahr = date.today().year

    konfiguration = await _get_konfiguration(db, jahr)

    pv_result = await db.execute(
        select(ParzelleVersicherung)
        .options(
            selectinload(ParzelleVersicherung.parzelle),
            selectinload(ParzelleVersicherung.sach_paket),
            selectinload(ParzelleVersicherung.zusatzpersonen),
        )
        .where(
            ParzelleVersicherung.jahr == jahr,
            (ParzelleVersicherung.hat_sachversicherung == True) |
            (ParzelleVersicherung.hat_unfallversicherung == True)
        )
    )
    alle_pv = pv_result.scalars().all()
    alle_pv.sort(key=lambda pv: pv.parzelle.gartennummer if pv.parzelle else "")

    zeilen = []
    summe_gesamt = Decimal("0")
    for pv in alle_pv:
        kosten = berechne_versicherungskosten(pv, konfiguration)
        summe_gesamt += kosten["gesamt"]
        zeilen.append({"pv": pv, "kosten": kosten})

    verfuegbare_jahre_result = await db.execute(
        select(VersicherungsKonfiguration.jahr).order_by(VersicherungsKonfiguration.jahr.desc())
    )
    verfuegbare_jahre = [r[0] for r in verfuegbare_jahre_result.all()]
    if jahr not in verfuegbare_jahre:
        verfuegbare_jahre.insert(0, jahr)

    return templates.TemplateResponse("versicherungen/auswertung.html", {
        "request": request, "benutzer": benutzer, "jahr": jahr,
        "verfuegbare_jahre": verfuegbare_jahre,
        "zeilen": zeilen, "summe_gesamt": summe_gesamt,
    })


@router.get("/auswertung/csv")
async def versicherungen_auswertung_csv(
    request: Request,
    jahr: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
):
    await require_user(request, db)
    if not jahr:
        jahr = date.today().year

    konfiguration = await _get_konfiguration(db, jahr)

    pv_result = await db.execute(
        select(ParzelleVersicherung)
        .options(
            selectinload(ParzelleVersicherung.parzelle),
            selectinload(ParzelleVersicherung.sach_paket),
            selectinload(ParzelleVersicherung.zusatzpersonen),
        )
        .where(
            ParzelleVersicherung.jahr == jahr,
            (ParzelleVersicherung.hat_sachversicherung == True) |
            (ParzelleVersicherung.hat_unfallversicherung == True)
        )
    )
    alle_pv = pv_result.scalars().all()
    alle_pv.sort(key=lambda pv: pv.parzelle.gartennummer if pv.parzelle else "")

    output = io.StringIO()
    writer = csv.writer(output, delimiter=";")
    writer.writerow([
        "Parzelle", "Sachversicherung", "Sach-Paket", "Sach-Kosten (EUR)",
        "Unfallversicherung", "Zusatzpersonen", "Unfall-Kosten (EUR)", "Gesamt (EUR)"
    ])

    for eintrag in alle_pv:
        pv = eintrag
        kosten = berechne_versicherungskosten(pv, konfiguration)
        writer.writerow([
            pv.parzelle.gartennummer if pv.parzelle else "",
            "Ja" if pv.hat_sachversicherung else "Nein",
            pv.sach_paket.bezeichnung if pv.sach_paket else "",
            f"{kosten['sach_kosten']:.2f}".replace(".", ","),
            "Ja" if pv.hat_unfallversicherung else "Nein",
            len(pv.zusatzpersonen),
            f"{kosten['unfall_kosten']:.2f}".replace(".", ","),
            f"{kosten['gesamt']:.2f}".replace(".", ","),
        ])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=versicherungen_{jahr}.csv"},
    )
