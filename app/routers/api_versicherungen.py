"""
API-Router: Versicherungen – Sachversicherungs-Pakete, Konfiguration,
Parzellen-Versicherungsstatus, Auswertung.
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models import (
    SachversicherungPaket, VersicherungsKonfiguration, ParzelleVersicherung,
    UnfallversicherungZusatzperson, Parzelle, Benutzer,
)
from app.api_auth import get_current_api_user, require_schreibzugriff
from app.module_flags import require_modul
from app.versicherung_utils import berechne_versicherungskosten
from app.schemas import (
    SachversicherungPaketOut, SachversicherungPaketCreate,
    VersicherungsKonfigurationOut, VersicherungsKonfigurationCreate,
    ParzelleVersicherungOut, ParzelleVersicherungUpdate, ParzelleVersicherungKostenOut,
)

router = APIRouter(
    prefix="/api/v1/versicherungen",
    tags=["API: Versicherungen"],
    dependencies=[Depends(require_modul("versicherungen"))],
)


# ---------------------------------------------------------------------------
# Sachversicherungs-Pakete
# ---------------------------------------------------------------------------

@router.get("/pakete", response_model=List[SachversicherungPaketOut], summary="Pakete auflisten")
async def pakete_auflisten(
    jahr: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db),
    benutzer: Benutzer = Depends(get_current_api_user),
):
    query = select(SachversicherungPaket).order_by(SachversicherungPaket.jahr.desc(), SachversicherungPaket.reihenfolge)
    if jahr:
        query = query.where(SachversicherungPaket.jahr == jahr)
    result = await db.execute(query)
    return result.scalars().all()


@router.post(
    "/pakete", response_model=SachversicherungPaketOut, status_code=status.HTTP_201_CREATED,
    summary="Paket anlegen",
)
async def paket_erstellen(
    daten: SachversicherungPaketCreate,
    db: AsyncSession = Depends(get_db),
    benutzer: Benutzer = Depends(require_schreibzugriff),
):
    paket = SachversicherungPaket(**daten.model_dump())
    db.add(paket)
    await db.commit()
    await db.refresh(paket)
    return paket


@router.put("/pakete/{paket_id}", response_model=SachversicherungPaketOut, summary="Paket aktualisieren")
async def paket_aktualisieren(
    paket_id: str,
    daten: SachversicherungPaketCreate,
    db: AsyncSession = Depends(get_db),
    benutzer: Benutzer = Depends(require_schreibzugriff),
):
    result = await db.execute(select(SachversicherungPaket).where(SachversicherungPaket.id == paket_id))
    paket = result.scalar_one_or_none()
    if not paket:
        raise HTTPException(status_code=404, detail="Paket nicht gefunden")

    for feld, wert in daten.model_dump().items():
        setattr(paket, feld, wert)

    await db.commit()
    await db.refresh(paket)
    return paket


@router.delete("/pakete/{paket_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Paket löschen")
async def paket_loeschen(
    paket_id: str,
    db: AsyncSession = Depends(get_db),
    benutzer: Benutzer = Depends(require_schreibzugriff),
):
    result = await db.execute(select(SachversicherungPaket).where(SachversicherungPaket.id == paket_id))
    paket = result.scalar_one_or_none()
    if paket:
        await db.delete(paket)
        await db.commit()


# ---------------------------------------------------------------------------
# Konfiguration (Unfallversicherungs-Beträge)
# ---------------------------------------------------------------------------

@router.get(
    "/konfiguration/{jahr}", response_model=VersicherungsKonfigurationOut,
    summary="Konfiguration für ein Jahr abrufen",
)
async def konfiguration_abrufen(
    jahr: int,
    db: AsyncSession = Depends(get_db),
    benutzer: Benutzer = Depends(get_current_api_user),
):
    result = await db.execute(select(VersicherungsKonfiguration).where(VersicherungsKonfiguration.jahr == jahr))
    konfig = result.scalar_one_or_none()
    if not konfig:
        raise HTTPException(status_code=404, detail=f"Keine Konfiguration für {jahr}")
    return konfig


@router.put(
    "/konfiguration/{jahr}", response_model=VersicherungsKonfigurationOut,
    summary="Konfiguration setzen (Upsert)",
)
async def konfiguration_setzen(
    jahr: int,
    daten: VersicherungsKonfigurationCreate,
    db: AsyncSession = Depends(get_db),
    benutzer: Benutzer = Depends(require_schreibzugriff),
):
    result = await db.execute(select(VersicherungsKonfiguration).where(VersicherungsKonfiguration.jahr == jahr))
    konfig = result.scalar_one_or_none()

    if konfig:
        konfig.unfall_grundbetrag_eur = daten.unfall_grundbetrag_eur
        konfig.unfall_zusatzbetrag_eur = daten.unfall_zusatzbetrag_eur
    else:
        konfig = VersicherungsKonfiguration(
            jahr=jahr, unfall_grundbetrag_eur=daten.unfall_grundbetrag_eur,
            unfall_zusatzbetrag_eur=daten.unfall_zusatzbetrag_eur,
        )
        db.add(konfig)

    await db.commit()
    await db.refresh(konfig)
    return konfig


# ---------------------------------------------------------------------------
# Parzellen-Versicherungsstatus
# ---------------------------------------------------------------------------

async def _lade_pv(db: AsyncSession, parzelle_id: str, jahr: int) -> Optional[ParzelleVersicherung]:
    result = await db.execute(
        select(ParzelleVersicherung)
        .options(
            selectinload(ParzelleVersicherung.sach_paket),
            selectinload(ParzelleVersicherung.zusatzpersonen),
        )
        .where(ParzelleVersicherung.parzelle_id == parzelle_id, ParzelleVersicherung.jahr == jahr)
    )
    return result.scalar_one_or_none()


def _zu_kosten_schema(pv: ParzelleVersicherung, konfig: Optional[VersicherungsKonfiguration]) -> ParzelleVersicherungKostenOut:
    kosten = berechne_versicherungskosten(pv, konfig)
    # Erst das Basis-Schema (nur echte ORM-Spalten) validieren, dann die
    # berechneten Felder ergänzen – model_validate(pv) direkt auf das
    # Zielschema würde fehlschlagen, da sach_kosten_eur/unfall_kosten_eur/
    # gesamt_kosten_eur keine echten Attribute auf pv sind, sondern erst
    # berechnet werden müssen.
    basis = ParzelleVersicherungOut.model_validate(pv)
    return ParzelleVersicherungKostenOut(
        **basis.model_dump(),
        zusatzpersonen_mitglied_ids=[z.mitglied_id for z in pv.zusatzpersonen],
        sach_kosten_eur=kosten["sach_kosten"],
        unfall_kosten_eur=kosten["unfall_kosten"],
        gesamt_kosten_eur=kosten["gesamt"],
    )


@router.get(
    "/parzellen/{parzelle_id}/{jahr}", response_model=ParzelleVersicherungKostenOut,
    summary="Versicherungsstatus einer Parzelle abrufen",
    description="Gibt 404 zurück, wenn für diese Parzelle/Jahr noch kein Status existiert "
                "(anders als die Web-UI wird er über die API nicht automatisch angelegt).",
)
async def versicherung_abrufen(
    parzelle_id: str,
    jahr: int,
    db: AsyncSession = Depends(get_db),
    benutzer: Benutzer = Depends(get_current_api_user),
):
    pv = await _lade_pv(db, parzelle_id, jahr)
    if not pv:
        raise HTTPException(status_code=404, detail="Kein Versicherungsstatus für diese Parzelle/Jahr")

    konfig_result = await db.execute(select(VersicherungsKonfiguration).where(VersicherungsKonfiguration.jahr == jahr))
    konfig = konfig_result.scalar_one_or_none()
    return _zu_kosten_schema(pv, konfig)


@router.put(
    "/parzellen/{parzelle_id}/{jahr}", response_model=ParzelleVersicherungKostenOut,
    summary="Versicherungsstatus setzen (Upsert)",
    description="Legt den Status an, falls er nicht existiert, und ersetzt die Liste der Zusatzpersonen komplett.",
)
async def versicherung_setzen(
    parzelle_id: str,
    jahr: int,
    daten: ParzelleVersicherungUpdate,
    db: AsyncSession = Depends(get_db),
    benutzer: Benutzer = Depends(require_schreibzugriff),
):
    parzelle_result = await db.execute(select(Parzelle).where(Parzelle.id == parzelle_id))
    if not parzelle_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Parzelle nicht gefunden")

    pv = await _lade_pv(db, parzelle_id, jahr)
    if not pv:
        pv = ParzelleVersicherung(parzelle_id=parzelle_id, jahr=jahr)
        db.add(pv)
        await db.commit()
        # Frisch angelegte Zeile mit eager-geladenen Beziehungen neu laden –
        # sonst löst der Zugriff auf pv.zusatzpersonen weiter unten einen
        # synchronen Lazy-Load aus, der mit dem asynchronen Datenbanktreiber
        # zu "MissingGreenlet" führt (siehe docs/module-tickets.md für das
        # gleiche Muster im Ticketsystem).
        pv = await _lade_pv(db, parzelle_id, jahr)

    pv.hat_sachversicherung = daten.hat_sachversicherung
    pv.sach_paket_id = daten.sach_paket_id if daten.hat_sachversicherung else None
    pv.hat_unfallversicherung = daten.hat_unfallversicherung

    for zp in list(pv.zusatzpersonen):
        await db.delete(zp)
    await db.flush()

    if daten.hat_unfallversicherung:
        for mitglied_id in daten.zusatzpersonen_mitglied_ids:
            db.add(UnfallversicherungZusatzperson(parzelle_versicherung_id=pv.id, mitglied_id=mitglied_id))

    await db.commit()

    # Wichtig: pv.sach_paket wurde ggf. schon VOR dem Setzen von
    # sach_paket_id geladen (z.B. beim Neuanlegen weiter oben, als der Wert
    # noch None war). Ein erneutes Abfragen über _lade_pv würde wegen
    # SQLAlchemys Identity Map dasselbe (bereits als "geladen" markierte,
    # aber inzwischen veraltete) Objekt zurückgeben, OHNE die Beziehung
    # neu zu holen – da expire_on_commit=False gesetzt ist. db.refresh()
    # erzwingt das gezielte Neuladen genau dieser Beziehungen.
    await db.refresh(pv, attribute_names=["sach_paket", "zusatzpersonen"])

    konfig_result = await db.execute(select(VersicherungsKonfiguration).where(VersicherungsKonfiguration.jahr == jahr))
    konfig = konfig_result.scalar_one_or_none()
    return _zu_kosten_schema(pv, konfig)


# ---------------------------------------------------------------------------
# Auswertung
# ---------------------------------------------------------------------------

@router.get(
    "/auswertung/{jahr}", response_model=List[ParzelleVersicherungKostenOut],
    summary="Jahresauswertung: alle versicherten Parzellen mit Kosten",
)
async def auswertung(
    jahr: int,
    db: AsyncSession = Depends(get_db),
    benutzer: Benutzer = Depends(get_current_api_user),
):
    konfig_result = await db.execute(select(VersicherungsKonfiguration).where(VersicherungsKonfiguration.jahr == jahr))
    konfig = konfig_result.scalar_one_or_none()

    result = await db.execute(
        select(ParzelleVersicherung)
        .options(selectinload(ParzelleVersicherung.sach_paket), selectinload(ParzelleVersicherung.zusatzpersonen))
        .where(
            ParzelleVersicherung.jahr == jahr,
            (ParzelleVersicherung.hat_sachversicherung == True) | (ParzelleVersicherung.hat_unfallversicherung == True)
        )
    )
    return [_zu_kosten_schema(pv, konfig) for pv in result.scalars().all()]
