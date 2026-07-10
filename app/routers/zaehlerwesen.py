"""
Generisches Zählerwesen-Modul: deckt Wasser- UND Stromzähler über
dieselbe Codebasis ab. Ein Zaehlpunkt hat ein "medium" (WASSER/STROM);
die komplette Logik (Verbrauchsberechnung, Plausibilitätsprüfung,
Ablesung, Auswertung) ist medium-unabhängig identisch.

erstelle_zaehler_router() ist eine Fabrikfunktion: sie erzeugt einen
vollständig konfigurierten Router für EIN Medium. main.py instanziiert
sie zweimal (für /wasser und /strom) – so bleibt die Logik an einer
einzigen Stelle gepflegt, statt für jedes Medium dupliziert zu werden.
"""
import csv
import io
import urllib.parse
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Optional, List

from fastapi import APIRouter, Request, Form, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models import (
    Zaehlpunkt, ZaehlpunktTyp, ZaehlerMedium, Zaehler, Zaehlerstand,
    Parzelle, ParzelleStatus,
)
from app.auth import require_user
from app.module_flags import require_modul
from app.zaehler_utils import (
    berechne_verbrauch, pruefe_monotonie, gesamtverbrauch_fuer_typ, stand_vor_jahr
)

templates = Jinja2Templates(directory="app/templates")
templates.env.filters["fmt"] = lambda wert, stellen: f"{float(wert):.{stellen}f}"


def _parse_zahl(wert: str, dezimalstellen: int) -> Optional[Decimal]:
    wert = wert.strip().replace(",", ".")
    if not wert:
        return None
    try:
        zahl = Decimal(wert)
    except InvalidOperation:
        return None
    quant = Decimal("1") if dezimalstellen == 0 else Decimal("1." + "0" * dezimalstellen)
    return zahl.quantize(quant)


def erstelle_zaehler_router(
    medium: ZaehlerMedium,
    url_prefix: str,
    modul_name: str,
    medium_label: str,
    einheit: str,
    icon: str,
    dezimalstellen: int,
) -> APIRouter:
    """
    Erzeugt einen vollständigen Router für ein Zähler-Medium.

    Args:
        medium: ZaehlerMedium.WASSER oder ZaehlerMedium.STROM
        url_prefix: z.B. "/wasser" oder "/strom"
        modul_name: Schlüssel für das Modul-Flag, z.B. "wasser"/"strom"
        medium_label: Anzeigename, z.B. "Wasser"/"Strom"
        einheit: z.B. "m³"/"kWh"
        icon: Bootstrap-Icon-Klasse, z.B. "bi-droplet"/"bi-lightning-charge"
        dezimalstellen: Anzahl Nachkommastellen bei Anzeige/Eingabe
    """
    router = APIRouter(
        prefix=url_prefix,
        tags=[modul_name],
        dependencies=[Depends(require_modul(modul_name))],
    )

    basis_context = {
        "medium": medium.value,
        "medium_label": medium_label,
        "einheit": einheit,
        "icon": icon,
        "url_prefix": url_prefix,
        "dezimalstellen": dezimalstellen,
    }

    async def _lade_zaehlpunkt_mit_details(db: AsyncSession, zaehlpunkt_id: str) -> Optional[Zaehlpunkt]:
        result = await db.execute(
            select(Zaehlpunkt)
            .options(
                selectinload(Zaehlpunkt.parzelle),
                selectinload(Zaehlpunkt.zaehler).selectinload(Zaehler.zaehlerstaende),
            )
            .where(Zaehlpunkt.id == zaehlpunkt_id, Zaehlpunkt.medium == medium)
        )
        return result.scalar_one_or_none()

    async def _lade_alle_zaehlpunkte(db: AsyncSession) -> List[Zaehlpunkt]:
        result = await db.execute(
            select(Zaehlpunkt)
            .options(
                selectinload(Zaehlpunkt.parzelle),
                selectinload(Zaehlpunkt.zaehler).selectinload(Zaehler.zaehlerstaende),
            )
            .where(Zaehlpunkt.medium == medium)
        )
        return result.scalars().all()

    # -----------------------------------------------------------------
    # Übersicht
    # -----------------------------------------------------------------

    @router.get("/", response_class=HTMLResponse)
    async def uebersicht(
        request: Request,
        jahr: Optional[int] = None,
        db: AsyncSession = Depends(get_db),
    ):
        benutzer = await require_user(request, db)
        if not jahr:
            jahr = date.today().year

        alle = await _lade_alle_zaehlpunkte(db)
        haupt = [a for a in alle if a.typ == ZaehlpunktTyp.HAUPTZAEHLER]
        parzellen = [a for a in alle if a.typ == ZaehlpunktTyp.PARZELLE]
        verein = [a for a in alle if a.typ == ZaehlpunktTyp.VEREIN]

        v_haupt = gesamtverbrauch_fuer_typ(haupt, jahr)
        v_parzellen = gesamtverbrauch_fuer_typ(parzellen, jahr)
        v_verein = gesamtverbrauch_fuer_typ(verein, jahr)

        warnung = None
        if v_haupt > 0 and (v_parzellen + v_verein) > v_haupt:
            warnung = (
                f"Die Summe aus Parzellen- ({v_parzellen} {einheit}) und Vereinsverbrauch "
                f"({v_verein} {einheit}) übersteigt den Hauptzähler-Verbrauch ({v_haupt} {einheit}) "
                f"für {jahr}. Bitte Zählerstände prüfen."
            )

        offene = 0
        for a in alle:
            z = a.aktueller_zaehler
            if z and not any(zs.jahr == jahr for zs in z.zaehlerstaende):
                offene += 1

        verfuegbare_jahre = sorted({
            zs.jahr for a in alle for z in a.zaehler for zs in z.zaehlerstaende
        }, reverse=True)
        if jahr not in verfuegbare_jahre:
            verfuegbare_jahre.insert(0, jahr)

        return templates.TemplateResponse("zaehlerwesen/uebersicht.html", {
            **basis_context,
            "request": request, "benutzer": benutzer, "jahr": jahr,
            "verfuegbare_jahre": verfuegbare_jahre,
            "anzahl_hauptzaehler": len(haupt),
            "anzahl_parzellen": len(parzellen),
            "anzahl_verein": len(verein),
            "verbrauch_haupt": v_haupt,
            "verbrauch_parzellen": v_parzellen,
            "verbrauch_verein": v_verein,
            "warnung": warnung,
            "offene_ablesungen": offene,
        })

    # -----------------------------------------------------------------
    # Zaehlpunkte: Liste, Anlegen, Detail, Bearbeiten, Löschen
    # -----------------------------------------------------------------

    @router.get("/zaehlpunkte", response_class=HTMLResponse)
    async def zaehlpunkte_liste(request: Request, db: AsyncSession = Depends(get_db)):
        benutzer = await require_user(request, db)
        alle = await _lade_alle_zaehlpunkte(db)

        def sortkey(a):
            if a.typ == ZaehlpunktTyp.HAUPTZAEHLER:
                return (0, "")
            if a.typ == ZaehlpunktTyp.PARZELLE:
                return (1, a.parzelle.gartennummer if a.parzelle else "")
            return (2, a.bezeichnung or "")

        alle.sort(key=sortkey)

        return templates.TemplateResponse("zaehlerwesen/zaehlpunkte_liste.html", {
            **basis_context,
            "request": request, "benutzer": benutzer,
            "zaehlpunkte": alle, "ZaehlpunktTyp": ZaehlpunktTyp,
            "jahr": date.today().year,
        })

    @router.get("/zaehlpunkte/neu", response_class=HTMLResponse)
    async def zaehlpunkt_neu_seite(request: Request, db: AsyncSession = Depends(get_db)):
        benutzer = await require_user(request, db)
        result = await db.execute(
            select(Parzelle).where(Parzelle.status == ParzelleStatus.AKTIV).order_by(Parzelle.gartennummer)
        )
        alle_parzellen = result.scalars().all()

        return templates.TemplateResponse("zaehlerwesen/zaehlpunkt_formular.html", {
            **basis_context,
            "request": request, "benutzer": benutzer,
            "alle_parzellen": alle_parzellen, "heute": date.today().isoformat(),
        })

    @router.post("/zaehlpunkte/neu")
    async def zaehlpunkt_erstellen(
        request: Request,
        typ: str = Form(...),
        parzelle_id: str = Form(""),
        bezeichnung: str = Form(""),
        notizen: str = Form(""),
        nummer: str = Form(...),
        geeicht_bis: str = Form(""),
        eingebaut_am: str = Form(""),
        anfangsstand: str = Form("0"),
        db: AsyncSession = Depends(get_db),
    ):
        await require_user(request, db)

        zaehlpunkt = Zaehlpunkt(
            medium=medium,
            typ=ZaehlpunktTyp(typ),
            parzelle_id=parzelle_id.strip() or None,
            bezeichnung=bezeichnung.strip() or None,
            notizen=notizen.strip() or None,
        )
        db.add(zaehlpunkt)
        await db.flush()

        stand = _parse_zahl(anfangsstand, dezimalstellen) or Decimal("0")

        zaehler = Zaehler(
            zaehlpunkt_id=zaehlpunkt.id,
            nummer=nummer.strip(),
            ist_aktiv=True,
            geeicht_bis=int(geeicht_bis) if geeicht_bis.strip() else None,
            eingebaut_am=date.fromisoformat(eingebaut_am) if eingebaut_am.strip() else None,
            anfangsstand=stand,
        )
        db.add(zaehler)

        await db.commit()
        return RedirectResponse(f"{url_prefix}/zaehlpunkte/{zaehlpunkt.id}", status_code=302)

    @router.get("/zaehlpunkte/{zaehlpunkt_id}", response_class=HTMLResponse)
    async def zaehlpunkt_detail(
        zaehlpunkt_id: str,
        request: Request,
        db: AsyncSession = Depends(get_db),
    ):
        benutzer = await require_user(request, db)
        zaehlpunkt = await _lade_zaehlpunkt_mit_details(db, zaehlpunkt_id)
        if not zaehlpunkt:
            raise HTTPException(status_code=404, detail=f"{medium_label}-Zaehlpunkt nicht gefunden")

        aktueller_zaehler = zaehlpunkt.aktueller_zaehler
        fruehere_zaehler = sorted(
            [z for z in zaehlpunkt.zaehler if not z.ist_aktiv],
            key=lambda z: z.ausgebaut_am or date.min,
            reverse=True,
        )

        staende_mit_verbrauch = []
        if aktueller_zaehler:
            for z in sorted(aktueller_zaehler.zaehlerstaende, key=lambda z: z.jahr, reverse=True):
                staende_mit_verbrauch.append({
                    "stand": z,
                    "verbrauch": berechne_verbrauch(aktueller_zaehler, z.jahr),
                })

        return templates.TemplateResponse("zaehlerwesen/zaehlpunkt_detail.html", {
            **basis_context,
            "request": request, "benutzer": benutzer,
            "zaehlpunkt": zaehlpunkt,
            "aktueller_zaehler": aktueller_zaehler,
            "fruehere_zaehler": fruehere_zaehler,
            "staende_mit_verbrauch": staende_mit_verbrauch,
            "heute": date.today().isoformat(),
            "aktuelles_jahr": date.today().year,
            "ZaehlpunktTyp": ZaehlpunktTyp,
        })

    @router.post("/zaehlpunkte/{zaehlpunkt_id}/bearbeiten")
    async def zaehlpunkt_aktualisieren(
        zaehlpunkt_id: str,
        request: Request,
        bezeichnung: str = Form(""),
        notizen: str = Form(""),
        db: AsyncSession = Depends(get_db),
    ):
        await require_user(request, db)
        result = await db.execute(
            select(Zaehlpunkt).where(Zaehlpunkt.id == zaehlpunkt_id, Zaehlpunkt.medium == medium)
        )
        zaehlpunkt = result.scalar_one_or_none()
        if not zaehlpunkt:
            raise HTTPException(status_code=404)

        zaehlpunkt.bezeichnung = bezeichnung.strip() or None
        zaehlpunkt.notizen = notizen.strip() or None
        await db.commit()
        return RedirectResponse(f"{url_prefix}/zaehlpunkte/{zaehlpunkt_id}", status_code=302)

    @router.post("/zaehlpunkte/{zaehlpunkt_id}/loeschen")
    async def zaehlpunkt_loeschen(
        zaehlpunkt_id: str,
        request: Request,
        db: AsyncSession = Depends(get_db),
    ):
        await require_user(request, db)
        result = await db.execute(
            select(Zaehlpunkt).where(Zaehlpunkt.id == zaehlpunkt_id, Zaehlpunkt.medium == medium)
        )
        zaehlpunkt = result.scalar_one_or_none()
        if zaehlpunkt:
            await db.delete(zaehlpunkt)
            await db.commit()
        return RedirectResponse(f"{url_prefix}/zaehlpunkte", status_code=302)

    # -----------------------------------------------------------------
    # Zähler tauschen
    # -----------------------------------------------------------------

    @router.post("/zaehlpunkte/{zaehlpunkt_id}/zaehler/tauschen")
    async def zaehler_tauschen(
        zaehlpunkt_id: str,
        request: Request,
        neue_nummer: str = Form(...),
        ausgebaut_am: str = Form(...),
        eingebaut_am: str = Form(...),
        geeicht_bis: str = Form(""),
        anfangsstand: str = Form("0"),
        db: AsyncSession = Depends(get_db),
    ):
        await require_user(request, db)
        zaehlpunkt = await _lade_zaehlpunkt_mit_details(db, zaehlpunkt_id)
        if not zaehlpunkt:
            raise HTTPException(status_code=404)

        alter_zaehler = zaehlpunkt.aktueller_zaehler
        if alter_zaehler:
            alter_zaehler.ist_aktiv = False
            alter_zaehler.ausgebaut_am = date.fromisoformat(ausgebaut_am)

        neuer_zaehler = Zaehler(
            zaehlpunkt_id=zaehlpunkt_id,
            nummer=neue_nummer.strip(),
            ist_aktiv=True,
            geeicht_bis=int(geeicht_bis) if geeicht_bis.strip() else None,
            eingebaut_am=date.fromisoformat(eingebaut_am),
            anfangsstand=_parse_zahl(anfangsstand, dezimalstellen) or Decimal("0"),
        )
        db.add(neuer_zaehler)
        await db.commit()
        return RedirectResponse(f"{url_prefix}/zaehlpunkte/{zaehlpunkt_id}", status_code=302)

    # -----------------------------------------------------------------
    # Zählerstände: Anlegen, Löschen
    # -----------------------------------------------------------------

    @router.post("/zaehlpunkte/{zaehlpunkt_id}/ablesung/neu")
    async def ablesung_erstellen(
        zaehlpunkt_id: str,
        request: Request,
        jahr: int = Form(...),
        datum: str = Form(...),
        stand: str = Form(...),
        notiz: str = Form(""),
        rueck_url: str = Form(f"{url_prefix}/ablesung"),
        db: AsyncSession = Depends(get_db),
    ):
        benutzer = await require_user(request, db)
        zaehlpunkt = await _lade_zaehlpunkt_mit_details(db, zaehlpunkt_id)
        if not zaehlpunkt:
            raise HTTPException(status_code=404)

        zaehler = zaehlpunkt.aktueller_zaehler
        if not zaehler:
            raise HTTPException(status_code=400, detail="Kein aktiver Zähler für diesen Zaehlpunkt vorhanden")

        neuer_stand = _parse_zahl(stand, dezimalstellen)
        if neuer_stand is None:
            return RedirectResponse(f"{rueck_url}?fehler=Ungültiger+Zählerstand", status_code=302)

        fehler = pruefe_monotonie(zaehler, jahr, neuer_stand)
        if fehler:
            return RedirectResponse(f"{rueck_url}?fehler={urllib.parse.quote(fehler)}", status_code=302)

        existing = next((z for z in zaehler.zaehlerstaende if z.jahr == jahr), None)
        if existing:
            existing.stand = neuer_stand
            existing.datum = date.fromisoformat(datum)
            existing.notiz = notiz.strip() or None
            existing.erfasst_von_id = benutzer.id
        else:
            db.add(Zaehlerstand(
                zaehler_id=zaehler.id,
                jahr=jahr,
                datum=date.fromisoformat(datum),
                stand=neuer_stand,
                notiz=notiz.strip() or None,
                erfasst_von_id=benutzer.id,
            ))

        await db.commit()
        return RedirectResponse(rueck_url, status_code=302)

    @router.post("/zaehlerstand/{zaehlerstand_id}/loeschen")
    async def zaehlerstand_loeschen(
        zaehlerstand_id: str,
        request: Request,
        db: AsyncSession = Depends(get_db),
    ):
        await require_user(request, db)
        result = await db.execute(select(Zaehlerstand).where(Zaehlerstand.id == zaehlerstand_id))
        zaehlerstand = result.scalar_one_or_none()
        zaehlpunkt_id = None
        if zaehlerstand:
            zaehler_result = await db.execute(select(Zaehler).where(Zaehler.id == zaehlerstand.zaehler_id))
            zaehler = zaehler_result.scalar_one_or_none()
            zaehlpunkt_id = zaehler.zaehlpunkt_id if zaehler else None
            await db.delete(zaehlerstand)
            await db.commit()

        if zaehlpunkt_id:
            return RedirectResponse(f"{url_prefix}/zaehlpunkte/{zaehlpunkt_id}", status_code=302)
        return RedirectResponse(f"{url_prefix}/zaehlpunkte", status_code=302)

    # -----------------------------------------------------------------
    # Ablesung (mobile-freundliche Sammel-Erfassung)
    # -----------------------------------------------------------------

    @router.get("/ablesung", response_class=HTMLResponse)
    async def ablesung_liste(
        request: Request,
        jahr: Optional[int] = None,
        fehler: Optional[str] = None,
        db: AsyncSession = Depends(get_db),
    ):
        benutzer = await require_user(request, db)
        if not jahr:
            jahr = date.today().year

        alle = await _lade_alle_zaehlpunkte(db)

        def aufbereiten(typ):
            gefiltert = [a for a in alle if a.typ == typ]
            zeilen = []
            for a in gefiltert:
                z = a.aktueller_zaehler
                if not z:
                    continue
                aktuelle_ablesung = next((zs for zs in z.zaehlerstaende if zs.jahr == jahr), None)
                zeilen.append({
                    "zaehlpunkt": a,
                    "zaehler": z,
                    "vorjahreswert": stand_vor_jahr(
                        z, jahr, exclude_id=aktuelle_ablesung.id if aktuelle_ablesung else None
                    ),
                    "ablesung": aktuelle_ablesung,
                })
            return zeilen

        hauptzaehler_zeilen = aufbereiten(ZaehlpunktTyp.HAUPTZAEHLER)
        parzellen_zeilen = sorted(
            aufbereiten(ZaehlpunktTyp.PARZELLE),
            key=lambda z: z["zaehlpunkt"].parzelle.gartennummer if z["zaehlpunkt"].parzelle else ""
        )
        verein_zeilen = aufbereiten(ZaehlpunktTyp.VEREIN)

        return templates.TemplateResponse("zaehlerwesen/ablesung_liste.html", {
            **basis_context,
            "request": request, "benutzer": benutzer, "jahr": jahr,
            "hauptzaehler_zeilen": hauptzaehler_zeilen,
            "parzellen_zeilen": parzellen_zeilen,
            "verein_zeilen": verein_zeilen,
            "fehler": fehler,
            "heute": date.today().isoformat(),
        })

    # -----------------------------------------------------------------
    # Auswertung
    # -----------------------------------------------------------------

    @router.get("/auswertung", response_class=HTMLResponse)
    async def auswertung(
        request: Request,
        jahr: Optional[int] = None,
        db: AsyncSession = Depends(get_db),
    ):
        benutzer = await require_user(request, db)
        if not jahr:
            jahr = date.today().year

        alle = await _lade_alle_zaehlpunkte(db)

        def zeilen_fuer_typ(typ):
            gefiltert = [a for a in alle if a.typ == typ]
            zeilen = []
            for a in gefiltert:
                z = a.aktueller_zaehler
                verbrauch = berechne_verbrauch(z, jahr) if z else None
                zeilen.append({"zaehlpunkt": a, "zaehler": z, "verbrauch": verbrauch})
            return zeilen

        hauptzaehler_zeilen = zeilen_fuer_typ(ZaehlpunktTyp.HAUPTZAEHLER)
        parzellen_zeilen = sorted(
            zeilen_fuer_typ(ZaehlpunktTyp.PARZELLE),
            key=lambda z: z["zaehlpunkt"].parzelle.gartennummer if z["zaehlpunkt"].parzelle else ""
        )
        verein_zeilen = zeilen_fuer_typ(ZaehlpunktTyp.VEREIN)

        summe_haupt = sum((z["verbrauch"] for z in hauptzaehler_zeilen if z["verbrauch"] is not None), Decimal("0"))
        summe_parzellen = sum((z["verbrauch"] for z in parzellen_zeilen if z["verbrauch"] is not None), Decimal("0"))
        summe_verein = sum((z["verbrauch"] for z in verein_zeilen if z["verbrauch"] is not None), Decimal("0"))

        warnung = None
        if summe_haupt > 0 and (summe_parzellen + summe_verein) > summe_haupt:
            warnung = (
                f"Verteilverbrauch ({summe_parzellen + summe_verein} {einheit}) übersteigt "
                f"Hauptzähler-Verbrauch ({summe_haupt} {einheit})."
            )

        verfuegbare_jahre = sorted({
            zs.jahr for a in alle for z in a.zaehler for zs in z.zaehlerstaende
        }, reverse=True)
        if jahr not in verfuegbare_jahre:
            verfuegbare_jahre.insert(0, jahr)

        return templates.TemplateResponse("zaehlerwesen/auswertung.html", {
            **basis_context,
            "request": request, "benutzer": benutzer, "jahr": jahr,
            "verfuegbare_jahre": verfuegbare_jahre,
            "hauptzaehler_zeilen": hauptzaehler_zeilen,
            "parzellen_zeilen": parzellen_zeilen,
            "verein_zeilen": verein_zeilen,
            "summe_haupt": summe_haupt,
            "summe_parzellen": summe_parzellen,
            "summe_verein": summe_verein,
            "warnung": warnung,
        })

    @router.get("/auswertung/csv")
    async def auswertung_csv(
        request: Request,
        jahr: Optional[int] = None,
        db: AsyncSession = Depends(get_db),
    ):
        await require_user(request, db)
        if not jahr:
            jahr = date.today().year

        alle = await _lade_alle_zaehlpunkte(db)

        output = io.StringIO()
        writer = csv.writer(output, delimiter=";")
        writer.writerow(["Typ", "Zaehlpunkt", f"{medium_label}zähler-Nr.", "Zählerstand", f"Verbrauch ({einheit})"])

        typ_label = {
            ZaehlpunktTyp.HAUPTZAEHLER: "Hauptzähler",
            ZaehlpunktTyp.PARZELLE: "Parzelle",
            ZaehlpunktTyp.VEREIN: "Verein",
        }

        for a in sorted(alle, key=lambda a: (a.typ.value, a.anzeigename)):
            z = a.aktueller_zaehler
            if not z:
                continue
            ablesung = next((zs for zs in z.zaehlerstaende if zs.jahr == jahr), None)
            verbrauch = berechne_verbrauch(z, jahr)
            writer.writerow([
                typ_label.get(a.typ, a.typ.value),
                a.anzeigename,
                z.nummer,
                f"{ablesung.stand:.{dezimalstellen}f}".replace(".", ",") if ablesung else "",
                f"{verbrauch:.{dezimalstellen}f}".replace(".", ",") if verbrauch is not None else "",
            ])

        output.seek(0)
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename={modul_name}verbrauch_{jahr}.csv"},
        )

    return router
