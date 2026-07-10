"""
API-Router-Fabrik für das Zählerwesen (Wasser & Strom) – analog zur
HTML-Router-Fabrik in app/routers/zaehlerwesen.py. Eine Codebasis für
beide Medien, zweimal instanziiert (siehe main.py).
"""
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models import Zaehlpunkt, ZaehlpunktTyp, ZaehlerMedium, Zaehler, Zaehlerstand, Benutzer
from app.api_auth import get_current_api_user, require_schreibzugriff
from app.module_flags import require_modul
from app.zaehler_utils import berechne_verbrauch, pruefe_monotonie, gesamtverbrauch_fuer_typ
from app.schemas import (
    ZaehlpunktOut, ZaehlpunktDetailOut, ZaehlpunktCreate, ZaehlpunktUpdate,
    ZaehlerOut, ZaehlerTauschRequest, ZaehlerstandCreate, ZaehlerstandOut,
    VerbrauchZeileOut,
)


def erstelle_zaehler_api_router(
    medium: ZaehlerMedium, url_prefix: str, modul_name: str,
) -> APIRouter:
    router = APIRouter(
        prefix=f"/api/v1{url_prefix}",
        tags=[f"API: {modul_name.capitalize()}"],
        dependencies=[Depends(require_modul(modul_name))],
    )

    async def _lade_zaehlpunkt(db: AsyncSession, zaehlpunkt_id: str) -> Optional[Zaehlpunkt]:
        result = await db.execute(
            select(Zaehlpunkt)
            .options(selectinload(Zaehlpunkt.zaehler).selectinload(Zaehler.zaehlerstaende))
            .where(Zaehlpunkt.id == zaehlpunkt_id, Zaehlpunkt.medium == medium)
        )
        return result.scalar_one_or_none()

    @router.get("/zaehlpunkte", response_model=List[ZaehlpunktOut], summary="Zählpunkte auflisten")
    async def zaehlpunkte_auflisten(
        typ: Optional[str] = Query(None, description="HAUPTZAEHLER, PARZELLE oder VEREIN"),
        db: AsyncSession = Depends(get_db),
        benutzer: Benutzer = Depends(get_current_api_user),
    ):
        query = select(Zaehlpunkt).where(Zaehlpunkt.medium == medium)
        if typ:
            query = query.where(Zaehlpunkt.typ == ZaehlpunktTyp(typ))
        result = await db.execute(query)
        return result.scalars().all()

    @router.get(
        "/zaehlpunkte/{zaehlpunkt_id}", response_model=ZaehlpunktDetailOut,
        summary="Zählpunkt inkl. Zähler-Historie abrufen",
    )
    async def zaehlpunkt_abrufen(
        zaehlpunkt_id: str,
        db: AsyncSession = Depends(get_db),
        benutzer: Benutzer = Depends(get_current_api_user),
    ):
        zp = await _lade_zaehlpunkt(db, zaehlpunkt_id)
        if not zp:
            raise HTTPException(status_code=404, detail="Zählpunkt nicht gefunden")
        out = ZaehlpunktDetailOut.model_validate(zp)
        out.aktueller_zaehler = zp.aktueller_zaehler
        out.fruehere_zaehler = [z for z in zp.zaehler if not z.ist_aktiv]
        return out

    @router.post(
        "/zaehlpunkte", response_model=ZaehlpunktDetailOut, status_code=status.HTTP_201_CREATED,
        summary="Zählpunkt anlegen",
        description="Legt einen Zählpunkt inkl. erstem Zähler in einem Schritt an.",
    )
    async def zaehlpunkt_erstellen(
        daten: ZaehlpunktCreate,
        db: AsyncSession = Depends(get_db),
        benutzer: Benutzer = Depends(require_schreibzugriff),
    ):
        zp = Zaehlpunkt(
            medium=medium, typ=ZaehlpunktTyp(daten.typ),
            parzelle_id=daten.parzelle_id, bezeichnung=daten.bezeichnung, notizen=daten.notizen,
        )
        db.add(zp)
        await db.flush()

        zaehler = Zaehler(
            zaehlpunkt_id=zp.id, nummer=daten.nummer, ist_aktiv=True,
            geeicht_bis=daten.geeicht_bis, eingebaut_am=daten.eingebaut_am,
            anfangsstand=daten.anfangsstand,
        )
        db.add(zaehler)
        await db.commit()

        zp = await _lade_zaehlpunkt(db, zp.id)
        out = ZaehlpunktDetailOut.model_validate(zp)
        out.aktueller_zaehler = zp.aktueller_zaehler
        out.fruehere_zaehler = []
        return out

    @router.put("/zaehlpunkte/{zaehlpunkt_id}", response_model=ZaehlpunktOut, summary="Zählpunkt aktualisieren")
    async def zaehlpunkt_aktualisieren(
        zaehlpunkt_id: str,
        daten: ZaehlpunktUpdate,
        db: AsyncSession = Depends(get_db),
        benutzer: Benutzer = Depends(require_schreibzugriff),
    ):
        result = await db.execute(
            select(Zaehlpunkt).where(Zaehlpunkt.id == zaehlpunkt_id, Zaehlpunkt.medium == medium)
        )
        zp = result.scalar_one_or_none()
        if not zp:
            raise HTTPException(status_code=404, detail="Zählpunkt nicht gefunden")

        for feld, wert in daten.model_dump(exclude_unset=True).items():
            setattr(zp, feld, wert)

        await db.commit()
        await db.refresh(zp)
        return zp

    @router.delete(
        "/zaehlpunkte/{zaehlpunkt_id}", status_code=status.HTTP_204_NO_CONTENT,
        summary="Zählpunkt löschen", description="Löscht auch alle Zähler und Zählerstände (Cascade).",
    )
    async def zaehlpunkt_loeschen(
        zaehlpunkt_id: str,
        db: AsyncSession = Depends(get_db),
        benutzer: Benutzer = Depends(require_schreibzugriff),
    ):
        result = await db.execute(
            select(Zaehlpunkt).where(Zaehlpunkt.id == zaehlpunkt_id, Zaehlpunkt.medium == medium)
        )
        zp = result.scalar_one_or_none()
        if zp:
            await db.delete(zp)
            await db.commit()

    @router.post(
        "/zaehlpunkte/{zaehlpunkt_id}/tauschen", response_model=ZaehlerOut,
        summary="Zähler tauschen",
        description="Deaktiviert den aktuellen Zähler (Ausbaudatum) und legt einen neuen an.",
    )
    async def zaehler_tauschen(
        zaehlpunkt_id: str,
        daten: ZaehlerTauschRequest,
        db: AsyncSession = Depends(get_db),
        benutzer: Benutzer = Depends(require_schreibzugriff),
    ):
        zp = await _lade_zaehlpunkt(db, zaehlpunkt_id)
        if not zp:
            raise HTTPException(status_code=404, detail="Zählpunkt nicht gefunden")

        alter = zp.aktueller_zaehler
        if alter:
            alter.ist_aktiv = False
            alter.ausgebaut_am = daten.ausgebaut_am

        neuer = Zaehler(
            zaehlpunkt_id=zaehlpunkt_id, nummer=daten.neue_nummer, ist_aktiv=True,
            geeicht_bis=daten.geeicht_bis, eingebaut_am=daten.eingebaut_am,
            anfangsstand=daten.anfangsstand,
        )
        db.add(neuer)
        await db.commit()
        await db.refresh(neuer)
        return neuer

    @router.get(
        "/zaehlpunkte/{zaehlpunkt_id}/zaehlerstaende", response_model=List[ZaehlerstandOut],
        summary="Zählerstände (Ablesungen) auflisten",
    )
    async def zaehlerstaende_auflisten(
        zaehlpunkt_id: str,
        db: AsyncSession = Depends(get_db),
        benutzer: Benutzer = Depends(get_current_api_user),
    ):
        zp = await _lade_zaehlpunkt(db, zaehlpunkt_id)
        if not zp:
            raise HTTPException(status_code=404, detail="Zählpunkt nicht gefunden")
        zaehler = zp.aktueller_zaehler
        if not zaehler:
            return []
        return sorted(zaehler.zaehlerstaende, key=lambda z: z.jahr, reverse=True)

    @router.post(
        "/zaehlpunkte/{zaehlpunkt_id}/zaehlerstaende", response_model=ZaehlerstandOut,
        status_code=status.HTTP_201_CREATED, summary="Ablesung erfassen",
        description="Legt eine neue Ablesung an oder aktualisiert die bestehende für dasselbe Jahr. "
                    "Prüft Plausibilität (Zählerstand darf nicht sinken).",
    )
    async def ablesung_erstellen(
        zaehlpunkt_id: str,
        daten: ZaehlerstandCreate,
        db: AsyncSession = Depends(get_db),
        benutzer: Benutzer = Depends(require_schreibzugriff),
    ):
        zp = await _lade_zaehlpunkt(db, zaehlpunkt_id)
        if not zp:
            raise HTTPException(status_code=404, detail="Zählpunkt nicht gefunden")
        zaehler = zp.aktueller_zaehler
        if not zaehler:
            raise HTTPException(status_code=400, detail="Kein aktiver Zähler für diesen Zählpunkt")

        fehler = pruefe_monotonie(zaehler, daten.jahr, daten.stand)
        if fehler:
            raise HTTPException(status_code=422, detail=fehler)

        existing = next((z for z in zaehler.zaehlerstaende if z.jahr == daten.jahr), None)
        if existing:
            existing.stand = daten.stand
            existing.datum = daten.datum
            existing.notiz = daten.notiz
            existing.erfasst_von_id = benutzer.id
            await db.commit()
            await db.refresh(existing)
            return existing

        neuer_stand = Zaehlerstand(
            zaehler_id=zaehler.id, jahr=daten.jahr, datum=daten.datum,
            stand=daten.stand, notiz=daten.notiz, erfasst_von_id=benutzer.id,
        )
        db.add(neuer_stand)
        await db.commit()
        await db.refresh(neuer_stand)
        return neuer_stand

    @router.delete(
        "/zaehlerstaende/{zaehlerstand_id}", status_code=status.HTTP_204_NO_CONTENT,
        summary="Ablesung löschen",
    )
    async def zaehlerstand_loeschen(
        zaehlerstand_id: str,
        db: AsyncSession = Depends(get_db),
        benutzer: Benutzer = Depends(require_schreibzugriff),
    ):
        result = await db.execute(select(Zaehlerstand).where(Zaehlerstand.id == zaehlerstand_id))
        zs = result.scalar_one_or_none()
        if zs:
            await db.delete(zs)
            await db.commit()

    @router.get(
        "/auswertung/{jahr}", response_model=List[VerbrauchZeileOut],
        summary="Verbrauchsauswertung für ein Jahr",
    )
    async def auswertung(
        jahr: int,
        typ: Optional[str] = Query(None, description="Nach HAUPTZAEHLER, PARZELLE oder VEREIN filtern"),
        db: AsyncSession = Depends(get_db),
        benutzer: Benutzer = Depends(get_current_api_user),
    ):
        query = (
            select(Zaehlpunkt)
            .options(selectinload(Zaehlpunkt.zaehler).selectinload(Zaehler.zaehlerstaende))
            .where(Zaehlpunkt.medium == medium)
        )
        if typ:
            query = query.where(Zaehlpunkt.typ == ZaehlpunktTyp(typ))
        result = await db.execute(query)
        zaehlpunkte = result.scalars().all()

        zeilen = []
        for zp in zaehlpunkte:
            zaehler = zp.aktueller_zaehler
            verbrauch = berechne_verbrauch(zaehler, jahr) if zaehler else None
            zeilen.append(VerbrauchZeileOut(
                zaehlpunkt_id=zp.id, bezeichnung=zp.anzeigename,
                zaehler_nummer=zaehler.nummer if zaehler else None,
                verbrauch=verbrauch,
            ))
        return zeilen

    return router
