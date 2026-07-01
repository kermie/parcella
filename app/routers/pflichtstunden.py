"""
Pflichtstunden-Router: Arbeitseinsätze, Patenschaften, Vereinsrollen, Konfiguration.
"""
import csv
import io
from datetime import date, datetime, timezone
from typing import Optional, List

from fastapi import APIRouter, Request, Form, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models import (
    Arbeitseinsatz, EinsatzTeilnahme, EinsatzTyp, TeilnahmeStatus,
    Patenschaft, Vereinsrolle, MitgliedVereinsrolle, BefreiungsGrund,
    PflichtstundenKonfiguration, PflichtstundenModus,
    Mitglied, MitgliedParzelle, Parzelle, ParzelleStatus,
)
from app.auth import require_user

router = APIRouter(prefix="/pflichtstunden", tags=["pflichtstunden"])
templates = Jinja2Templates(directory="app/templates")


# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------

async def _get_config_fuer_jahr(db: AsyncSession, jahr: int) -> Optional[PflichtstundenKonfiguration]:
    result = await db.execute(
        select(PflichtstundenKonfiguration).where(PflichtstundenKonfiguration.jahr == jahr)
    )
    return result.scalar_one_or_none()


async def _berechne_stunden_fuer_mitglied(
    db: AsyncSession, mitglied_id: str, jahr: int
) -> dict:
    """Berechnet den Pflichtstunden-Stand eines Mitglieds für ein Jahr."""

    # Einsatz-Teilnahmen (nur ERSCHIENEN zählen)
    einsatz_stunden = await db.scalar(
        select(func.coalesce(func.sum(EinsatzTeilnahme.stunden_geleistet), 0))
        .join(Arbeitseinsatz)
        .where(
            EinsatzTeilnahme.mitglied_id == mitglied_id,
            EinsatzTeilnahme.status == TeilnahmeStatus.ERSCHIENEN,
            func.extract("year", Arbeitseinsatz.datum) == jahr,
        )
    ) or 0

    # Patenschaft (aktiv im gesuchten Jahr)
    patenschaft_stunden = await db.scalar(
        select(func.coalesce(func.sum(Patenschaft.stunden_anrechenbar), 0))
        .where(
            Patenschaft.mitglied_id == mitglied_id,
            Patenschaft.von <= date(jahr, 12, 31),
            (Patenschaft.bis.is_(None)) | (Patenschaft.bis >= date(jahr, 1, 1)),
        )
    ) or 0

    return {
        "einsatz_stunden": float(einsatz_stunden),
        "patenschaft_stunden": float(patenschaft_stunden),
        "gesamt": float(einsatz_stunden) + float(patenschaft_stunden),
    }


async def _ist_befreit(db: AsyncSession, mitglied_id: str, jahr: int) -> bool:
    """Prüft ob ein Mitglied für ein Jahr von Pflichtstunden befreit ist."""
    result = await db.execute(
        select(MitgliedVereinsrolle)
        .join(Vereinsrolle)
        .where(
            MitgliedVereinsrolle.mitglied_id == mitglied_id,
            MitgliedVereinsrolle.jahr == jahr,
            Vereinsrolle.pflichtstunden_befreit == True,
        )
    )
    return result.scalar_one_or_none() is not None


# ---------------------------------------------------------------------------
# Dashboard / Übersicht
# ---------------------------------------------------------------------------

@router.get("/", response_class=HTMLResponse)
async def pflichtstunden_uebersicht(
    request: Request,
    jahr: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
):
    benutzer = await require_user(request, db)

    if not jahr:
        jahr = date.today().year

    config = await _get_config_fuer_jahr(db, jahr)

    # Alle verfügbaren Jahre für Dropdown
    jahre_result = await db.execute(
        select(PflichtstundenKonfiguration.jahr).order_by(PflichtstundenKonfiguration.jahr.desc())
    )
    verfuegbare_jahre = [r[0] for r in jahre_result.all()]

    # Einsätze des Jahres
    einsaetze_result = await db.execute(
        select(Arbeitseinsatz)
        .options(selectinload(Arbeitseinsatz.teilnahmen))
        .where(func.extract("year", Arbeitseinsatz.datum) == jahr)
        .order_by(Arbeitseinsatz.datum.desc())
    )
    einsaetze = einsaetze_result.scalars().all()

    return templates.TemplateResponse(
        "pflichtstunden/uebersicht.html",
        {
            "request": request,
            "benutzer": benutzer,
            "jahr": jahr,
            "config": config,
            "einsaetze": einsaetze,
            "verfuegbare_jahre": verfuegbare_jahre,
            "EinsatzTyp": EinsatzTyp,
            "TeilnahmeStatus": TeilnahmeStatus,
        },
    )


# ---------------------------------------------------------------------------
# Pflichtstunden-Konfiguration
# ---------------------------------------------------------------------------

@router.get("/konfiguration", response_class=HTMLResponse)
async def konfiguration_seite(request: Request, db: AsyncSession = Depends(get_db)):
    benutzer = await require_user(request, db)

    result = await db.execute(
        select(PflichtstundenKonfiguration).order_by(PflichtstundenKonfiguration.jahr.desc())
    )
    konfigurationen = result.scalars().all()

    return templates.TemplateResponse(
        "pflichtstunden/konfiguration.html",
        {
            "request": request,
            "benutzer": benutzer,
            "konfigurationen": konfigurationen,
            "PflichtstundenModus": PflichtstundenModus,
            "aktuelles_jahr": date.today().year,
        },
    )


@router.post("/konfiguration/neu")
async def konfiguration_erstellen(
    request: Request,
    jahr: int = Form(...),
    stunden_gesamt: str = Form(...),
    stundensatz_eur: str = Form(...),
    modus: str = Form("pro_pachtvertrag"),
    notiz: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    await require_user(request, db)

    existing = await _get_config_fuer_jahr(db, jahr)
    if existing:
        existing.stunden_gesamt = float(stunden_gesamt.replace(",", "."))
        existing.stundensatz_eur = float(stundensatz_eur.replace(",", "."))
        existing.modus = PflichtstundenModus(modus)
        existing.notiz = notiz.strip() or None
    else:
        config = PflichtstundenKonfiguration(
            jahr=jahr,
            stunden_gesamt=float(stunden_gesamt.replace(",", ".")),
            stundensatz_eur=float(stundensatz_eur.replace(",", ".")),
            modus=PflichtstundenModus(modus),
            notiz=notiz.strip() or None,
        )
        db.add(config)

    await db.commit()
    return RedirectResponse("/pflichtstunden/konfiguration", status_code=302)


# ---------------------------------------------------------------------------
# Arbeitseinsätze
# ---------------------------------------------------------------------------

@router.get("/einsaetze/neu", response_class=HTMLResponse)
async def einsatz_neu_seite(request: Request, db: AsyncSession = Depends(get_db)):
    benutzer = await require_user(request, db)
    return templates.TemplateResponse(
        "pflichtstunden/einsatz_formular.html",
        {
            "request": request,
            "benutzer": benutzer,
            "einsatz": None,
            "EinsatzTyp": EinsatzTyp,
        },
    )


@router.post("/einsaetze/neu")
async def einsatz_erstellen(
    request: Request,
    titel: str = Form(...),
    beschreibung: str = Form(""),
    typ: str = Form("standard"),
    datum: str = Form(...),
    uhrzeit_von: str = Form(""),
    uhrzeit_bis: str = Form(""),
    max_teilnehmer: str = Form(""),
    stunden_pro_teilnehmer: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    benutzer = await require_user(request, db)

    einsatz = Arbeitseinsatz(
        titel=titel.strip(),
        beschreibung=beschreibung.strip() or None,
        typ=EinsatzTyp(typ),
        datum=date.fromisoformat(datum),
        uhrzeit_von=uhrzeit_von.strip() or None,
        uhrzeit_bis=uhrzeit_bis.strip() or None,
        max_teilnehmer=int(max_teilnehmer) if max_teilnehmer.strip() else None,
        stunden_pro_teilnehmer=float(stunden_pro_teilnehmer.replace(",", ".")) if stunden_pro_teilnehmer.strip() else None,
        erstellt_von_id=benutzer.id,
    )
    db.add(einsatz)
    await db.commit()
    return RedirectResponse(f"/pflichtstunden/einsaetze/{einsatz.id}", status_code=302)


@router.get("/einsaetze/{einsatz_id}", response_class=HTMLResponse)
async def einsatz_detail(
    einsatz_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    benutzer = await require_user(request, db)

    result = await db.execute(
        select(Arbeitseinsatz)
        .options(
            selectinload(Arbeitseinsatz.teilnahmen).selectinload(EinsatzTeilnahme.mitglied)
        )
        .where(Arbeitseinsatz.id == einsatz_id)
    )
    einsatz = result.scalar_one_or_none()
    if not einsatz:
        raise HTTPException(status_code=404, detail="Einsatz nicht gefunden")

    # Alle aktiven Mitglieder für Anmelde-Dropdown
    mitglieder_result = await db.execute(
        select(Mitglied)
        .where(Mitglied.deleted_at.is_(None))
        .order_by(Mitglied.nachname, Mitglied.vorname)
    )
    alle_mitglieder = mitglieder_result.scalars().all()
    bereits_eingetragen = {t.mitglied_id for t in einsatz.teilnahmen}

    return templates.TemplateResponse(
        "pflichtstunden/einsatz_detail.html",
        {
            "request": request,
            "benutzer": benutzer,
            "einsatz": einsatz,
            "alle_mitglieder": alle_mitglieder,
            "bereits_eingetragen": bereits_eingetragen,
            "TeilnahmeStatus": TeilnahmeStatus,
            "EinsatzTyp": EinsatzTyp,
        },
    )


@router.post("/einsaetze/{einsatz_id}/teilnehmer/hinzufuegen")
async def teilnehmer_hinzufuegen(
    einsatz_id: str,
    request: Request,
    mitglied_id: str = Form(...),
    status: str = Form("erschienen"),
    stunden_geleistet: str = Form(""),
    notiz: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    await require_user(request, db)

    # Bereits eingetragen?
    existing = await db.execute(
        select(EinsatzTeilnahme).where(
            EinsatzTeilnahme.einsatz_id == einsatz_id,
            EinsatzTeilnahme.mitglied_id == mitglied_id,
        )
    )
    if existing.scalar_one_or_none():
        return RedirectResponse(f"/pflichtstunden/einsaetze/{einsatz_id}", status_code=302)

    teilnahme = EinsatzTeilnahme(
        einsatz_id=einsatz_id,
        mitglied_id=mitglied_id,
        status=TeilnahmeStatus(status),
        stunden_geleistet=float(stunden_geleistet.replace(",", ".")) if stunden_geleistet.strip() else None,
        notiz=notiz.strip() or None,
    )
    db.add(teilnahme)
    await db.commit()
    return RedirectResponse(f"/pflichtstunden/einsaetze/{einsatz_id}", status_code=302)


@router.post("/einsaetze/{einsatz_id}/teilnehmer/{teilnahme_id}/status")
async def teilnahme_status_aendern(
    einsatz_id: str,
    teilnahme_id: str,
    request: Request,
    status: str = Form(...),
    stunden_geleistet: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    await require_user(request, db)

    result = await db.execute(
        select(EinsatzTeilnahme).where(EinsatzTeilnahme.id == teilnahme_id)
    )
    teilnahme = result.scalar_one_or_none()
    if teilnahme:
        teilnahme.status = TeilnahmeStatus(status)
        if stunden_geleistet.strip():
            teilnahme.stunden_geleistet = float(stunden_geleistet.replace(",", "."))
        await db.commit()

    return RedirectResponse(f"/pflichtstunden/einsaetze/{einsatz_id}", status_code=302)


@router.post("/einsaetze/{einsatz_id}/teilnehmer/{teilnahme_id}/entfernen")
async def teilnahme_entfernen(
    einsatz_id: str,
    teilnahme_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    await require_user(request, db)

    result = await db.execute(
        select(EinsatzTeilnahme).where(EinsatzTeilnahme.id == teilnahme_id)
    )
    teilnahme = result.scalar_one_or_none()
    if teilnahme:
        await db.delete(teilnahme)
        await db.commit()

    return RedirectResponse(f"/pflichtstunden/einsaetze/{einsatz_id}", status_code=302)


# ---------------------------------------------------------------------------
# Vereinsrollen
# ---------------------------------------------------------------------------

@router.get("/vereinsrollen", response_class=HTMLResponse)
async def vereinsrollen_seite(request: Request, db: AsyncSession = Depends(get_db)):
    benutzer = await require_user(request, db)

    result = await db.execute(
        select(Vereinsrolle).order_by(Vereinsrolle.name)
    )
    rollen = result.scalars().all()

    return templates.TemplateResponse(
        "pflichtstunden/vereinsrollen.html",
        {
            "request": request,
            "benutzer": benutzer,
            "rollen": rollen,
            "BefreiungsGrund": BefreiungsGrund,
        },
    )


@router.post("/vereinsrollen/neu")
async def vereinsrolle_erstellen(
    request: Request,
    name: str = Form(...),
    beschreibung: str = Form(""),
    pflichtstunden_befreit: bool = Form(False),
    befreiungsgrund: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    await require_user(request, db)

    rolle = Vereinsrolle(
        name=name.strip(),
        beschreibung=beschreibung.strip() or None,
        pflichtstunden_befreit=pflichtstunden_befreit,
        befreiungsgrund=BefreiungsGrund(befreiungsgrund) if befreiungsgrund else None,
    )
    db.add(rolle)
    await db.commit()
    return RedirectResponse("/pflichtstunden/vereinsrollen", status_code=302)


@router.post("/vereinsrollen/{rolle_id}/loeschen")
async def vereinsrolle_loeschen(
    rolle_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    await require_user(request, db)
    result = await db.execute(select(Vereinsrolle).where(Vereinsrolle.id == rolle_id))
    rolle = result.scalar_one_or_none()
    if rolle:
        await db.delete(rolle)
        await db.commit()
    return RedirectResponse("/pflichtstunden/vereinsrollen", status_code=302)


# ---------------------------------------------------------------------------
# Patenschaften
# ---------------------------------------------------------------------------

@router.get("/patenschaften", response_class=HTMLResponse)
async def patenschaften_seite(
    request: Request,
    jahr: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
):
    benutzer = await require_user(request, db)

    if not jahr:
        jahr = date.today().year

    query = (
        select(Patenschaft)
        .options(selectinload(Patenschaft.mitglied))
        .where(
            Patenschaft.von <= date(jahr, 12, 31),
            (Patenschaft.bis.is_(None)) | (Patenschaft.bis >= date(jahr, 1, 1)),
        )
        .order_by(Patenschaft.bereich)
    )
    result = await db.execute(query)
    patenschaften = result.scalars().all()

    mitglieder_result = await db.execute(
        select(Mitglied)
        .where(Mitglied.deleted_at.is_(None))
        .order_by(Mitglied.nachname, Mitglied.vorname)
    )
    alle_mitglieder = mitglieder_result.scalars().all()

    return templates.TemplateResponse(
        "pflichtstunden/patenschaften.html",
        {
            "request": request,
            "benutzer": benutzer,
            "patenschaften": patenschaften,
            "alle_mitglieder": alle_mitglieder,
            "jahr": jahr,
        },
    )


@router.post("/patenschaften/neu")
async def patenschaft_erstellen(
    request: Request,
    mitglied_id: str = Form(...),
    bereich: str = Form(...),
    beschreibung: str = Form(""),
    stunden_anrechenbar: str = Form(...),
    von: str = Form(...),
    bis: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    await require_user(request, db)

    patenschaft = Patenschaft(
        mitglied_id=mitglied_id,
        bereich=bereich.strip(),
        beschreibung=beschreibung.strip() or None,
        stunden_anrechenbar=float(stunden_anrechenbar.replace(",", ".")),
        von=date.fromisoformat(von),
        bis=date.fromisoformat(bis) if bis.strip() else None,
    )
    db.add(patenschaft)
    await db.commit()
    return RedirectResponse("/pflichtstunden/patenschaften", status_code=302)


@router.post("/patenschaften/{patenschaft_id}/loeschen")
async def patenschaft_loeschen(
    patenschaft_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    await require_user(request, db)
    result = await db.execute(select(Patenschaft).where(Patenschaft.id == patenschaft_id))
    patenschaft = result.scalar_one_or_none()
    if patenschaft:
        await db.delete(patenschaft)
        await db.commit()
    return RedirectResponse("/pflichtstunden/patenschaften", status_code=302)


# ---------------------------------------------------------------------------
# Auswertung: Jahresstand pro Mitglied/Parzelle
# ---------------------------------------------------------------------------

@router.get("/auswertung", response_class=HTMLResponse)
async def auswertung(
    request: Request,
    jahr: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
):
    benutzer = await require_user(request, db)

    if not jahr:
        jahr = date.today().year

    config = await _get_config_fuer_jahr(db, jahr)

    jahre_result = await db.execute(
        select(PflichtstundenKonfiguration.jahr).order_by(PflichtstundenKonfiguration.jahr.desc())
    )
    verfuegbare_jahre = [r[0] for r in jahre_result.all()]

    if not config:
        return templates.TemplateResponse(
            "pflichtstunden/auswertung.html",
            {
                "request": request,
                "benutzer": benutzer,
                "jahr": jahr,
                "config": None,
                "zeilen": [],
                "verfuegbare_jahre": verfuegbare_jahre,
            },
        )

    zeilen = []

    if config.modus == PflichtstundenModus.PRO_PACHTVERTRAG:
        # Pro Parzelle auswerten – alle aktiven Parzellen mit Pächtern
        parzellen_result = await db.execute(
            select(Parzelle)
            .options(
                selectinload(Parzelle.mitglieder_zuordnungen).selectinload(MitgliedParzelle.mitglied)
            )
            .where(Parzelle.status == ParzelleStatus.AKTIV)
            .order_by(Parzelle.gartennummer)
        )
        parzellen = parzellen_result.scalars().all()

        for parzelle in parzellen:
            paechter = [z.mitglied for z in parzelle.mitglieder_zuordnungen]
            if not paechter:
                continue  # Unbesetzte Parzellen überspringen

            # Stunden aller Pächter summieren
            gesamt_stunden = 0.0
            paechter_details = []
            for m in paechter:
                stand = await _berechne_stunden_fuer_mitglied(db, m.id, jahr)
                befreit = await _ist_befreit(db, m.id, jahr)
                gesamt_stunden += stand["gesamt"]
                paechter_details.append({
                    "mitglied": m,
                    "stand": stand,
                    "befreit": befreit,
                })

            pflicht = float(config.stunden_gesamt)
            offen = max(0.0, pflicht - gesamt_stunden)
            schuldbetrag = offen * float(config.stundensatz_eur)

            # Befreit wenn ALLE Pächter befreit
            alle_befreit = all(p["befreit"] for p in paechter_details)

            zeilen.append({
                "parzelle": parzelle,
                "paechter_details": paechter_details,
                "gesamt_stunden": gesamt_stunden,
                "pflicht_stunden": pflicht,
                "offen_stunden": offen if not alle_befreit else 0.0,
                "schuldbetrag": schuldbetrag if not alle_befreit else 0.0,
                "erfuellt": alle_befreit or gesamt_stunden >= pflicht,
                "alle_befreit": alle_befreit,
            })

    else:
        # PRO_MITGLIED: jedes Mitglied mit Parzelle einzeln auswerten
        mitglieder_result = await db.execute(
            select(Mitglied)
            .options(selectinload(Mitglied.parzellen_zuordnungen))
            .where(
                Mitglied.deleted_at.is_(None),
                Mitglied.parzellen_zuordnungen.any(),
            )
            .order_by(Mitglied.nachname, Mitglied.vorname)
        )
        mitglieder = mitglieder_result.scalars().all()

        for m in mitglieder:
            stand = await _berechne_stunden_fuer_mitglied(db, m.id, jahr)
            befreit = await _ist_befreit(db, m.id, jahr)
            pflicht = float(config.stunden_gesamt)
            offen = max(0.0, pflicht - stand["gesamt"])
            schuldbetrag = offen * float(config.stundensatz_eur)

            zeilen.append({
                "mitglied": m,
                "stand": stand,
                "befreit": befreit,
                "pflicht_stunden": pflicht,
                "offen_stunden": offen if not befreit else 0.0,
                "schuldbetrag": schuldbetrag if not befreit else 0.0,
                "erfuellt": befreit or stand["gesamt"] >= pflicht,
            })

    return templates.TemplateResponse(
        "pflichtstunden/auswertung.html",
        {
            "request": request,
            "benutzer": benutzer,
            "jahr": jahr,
            "config": config,
            "zeilen": zeilen,
            "verfuegbare_jahre": verfuegbare_jahre,
            "PflichtstundenModus": PflichtstundenModus,
        },
    )


@router.get("/auswertung/csv")
async def auswertung_export_csv(
    request: Request,
    jahr: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
):
    await require_user(request, db)

    if not jahr:
        jahr = date.today().year

    config = await _get_config_fuer_jahr(db, jahr)
    if not config:
        raise HTTPException(status_code=404, detail=f"Keine Konfiguration für {jahr}")

    output = io.StringIO()
    writer = csv.writer(output, delimiter=";")
    writer.writerow([
        "Parzelle", "Pächter", "Pflicht (h)", "Geleistet (h)",
        "Patenschaft (h)", "Gesamt (h)", "Offen (h)",
        "Schuldbetrag (EUR)", "Befreit", "Erfüllt"
    ])

    if config.modus == PflichtstundenModus.PRO_PACHTVERTRAG:
        parzellen_result = await db.execute(
            select(Parzelle)
            .options(selectinload(Parzelle.mitglieder_zuordnungen).selectinload(MitgliedParzelle.mitglied))
            .where(Parzelle.status == ParzelleStatus.AKTIV)
            .order_by(Parzelle.gartennummer)
        )
        for parzelle in parzellen_result.scalars().all():
            paechter = [z.mitglied for z in parzelle.mitglieder_zuordnungen]
            if not paechter:
                continue
            gesamt = 0.0
            einsatz_h = 0.0
            paten_h = 0.0
            alle_befreit = True
            namen = []
            for m in paechter:
                stand = await _berechne_stunden_fuer_mitglied(db, m.id, jahr)
                befreit = await _ist_befreit(db, m.id, jahr)
                gesamt += stand["gesamt"]
                einsatz_h += stand["einsatz_stunden"]
                paten_h += stand["patenschaft_stunden"]
                if not befreit:
                    alle_befreit = False
                namen.append(m.vollname)
            pflicht = float(config.stunden_gesamt)
            offen = max(0.0, pflicht - gesamt) if not alle_befreit else 0.0
            schuld = offen * float(config.stundensatz_eur)
            writer.writerow([
                parzelle.gartennummer,
                "; ".join(namen),
                f"{pflicht:.1f}",
                f"{einsatz_h:.1f}",
                f"{paten_h:.1f}",
                f"{gesamt:.1f}",
                f"{offen:.1f}",
                f"{schuld:.2f}".replace(".", ","),
                "Ja" if alle_befreit else "Nein",
                "Ja" if (alle_befreit or gesamt >= pflicht) else "Nein",
            ])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=pflichtstunden_{jahr}.csv"},
    )
