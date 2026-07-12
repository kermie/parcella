"""
API-Router: Pflichtstunden – Konfiguration, Vereinsrollen, Arbeitseinsätze,
Patenschaften, Auswertung.
"""
from datetime import date
from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models import (
    PflichtstundenKonfiguration, PflichtstundenModus,
    Vereinsrolle, MitgliedVereinsrolle, BefreiungsGrund,
    Arbeitseinsatz, EinsatzTeilnahme, EinsatzTyp, TeilnahmeStatus,
    Patenschaft, Member, Parcel, ParcelStatus, MemberParcel, Benutzer,
)
from app.api_auth import get_current_api_user, require_schreibzugriff
from app.module_flags import require_modul
from app.schemas import (
    PflichtstundenKonfigurationOut, PflichtstundenKonfigurationCreate,
    VereinsrolleOut, VereinsrolleCreate,
    MitgliedVereinsrolleOut, MitgliedVereinsrolleCreate,
    ArbeitseinsatzOut, ArbeitseinsatzCreate, ArbeitseinsatzUpdate,
    EinsatzTeilnahmeOut, EinsatzTeilnahmeCreate, EinsatzTeilnahmeUpdate,
    PatenschaftOut, PatenschaftCreate, PatenschaftUpdate,
    AuswertungZeileOut,
)

router = APIRouter(
    prefix="/api/v1/pflichtstunden",
    tags=["API: Pflichtstunden"],
    dependencies=[Depends(require_modul("pflichtstunden"))],
)


# ---------------------------------------------------------------------------
# Konfiguration
# ---------------------------------------------------------------------------

@router.get("/konfiguration", response_model=List[PflichtstundenKonfigurationOut], summary="Konfigurationen auflisten")
async def konfigurationen_auflisten(
    db: AsyncSession = Depends(get_db),
    benutzer: Benutzer = Depends(get_current_api_user),
):
    result = await db.execute(
        select(PflichtstundenKonfiguration).order_by(PflichtstundenKonfiguration.jahr.desc())
    )
    return result.scalars().all()


@router.get("/konfiguration/{jahr}", response_model=PflichtstundenKonfigurationOut, summary="Konfiguration für ein Jahr abrufen")
async def konfiguration_abrufen(
    jahr: int,
    db: AsyncSession = Depends(get_db),
    benutzer: Benutzer = Depends(get_current_api_user),
):
    result = await db.execute(
        select(PflichtstundenKonfiguration).where(PflichtstundenKonfiguration.jahr == jahr)
    )
    konfig = result.scalar_one_or_none()
    if not konfig:
        raise HTTPException(status_code=404, detail=f"Keine Konfiguration für {jahr}")
    return konfig


@router.put(
    "/konfiguration/{jahr}", response_model=PflichtstundenKonfigurationOut,
    summary="Konfiguration setzen (Upsert)",
    description="Legt die Konfiguration für ein Jahr an oder aktualisiert sie, falls bereits vorhanden.",
)
async def konfiguration_setzen(
    jahr: int,
    daten: PflichtstundenKonfigurationCreate,
    db: AsyncSession = Depends(get_db),
    benutzer: Benutzer = Depends(require_schreibzugriff),
):
    result = await db.execute(
        select(PflichtstundenKonfiguration).where(PflichtstundenKonfiguration.jahr == jahr)
    )
    konfig = result.scalar_one_or_none()

    if konfig:
        konfig.stunden_gesamt = daten.stunden_gesamt
        konfig.stundensatz_eur = daten.stundensatz_eur
        konfig.modus = PflichtstundenModus(daten.modus)
        konfig.notiz = daten.notiz
    else:
        konfig = PflichtstundenKonfiguration(
            jahr=jahr,
            stunden_gesamt=daten.stunden_gesamt,
            stundensatz_eur=daten.stundensatz_eur,
            modus=PflichtstundenModus(daten.modus),
            notiz=daten.notiz,
        )
        db.add(konfig)

    await db.commit()
    await db.refresh(konfig)
    return konfig


# ---------------------------------------------------------------------------
# Vereinsrollen
# ---------------------------------------------------------------------------

@router.get("/vereinsrollen", response_model=List[VereinsrolleOut], summary="Vereinsrollen auflisten")
async def vereinsrollen_auflisten(
    db: AsyncSession = Depends(get_db),
    benutzer: Benutzer = Depends(get_current_api_user),
):
    result = await db.execute(select(Vereinsrolle).order_by(Vereinsrolle.name))
    return result.scalars().all()


@router.post(
    "/vereinsrollen", response_model=VereinsrolleOut, status_code=status.HTTP_201_CREATED,
    summary="Vereinsrolle anlegen",
)
async def vereinsrolle_erstellen(
    daten: VereinsrolleCreate,
    db: AsyncSession = Depends(get_db),
    benutzer: Benutzer = Depends(require_schreibzugriff),
):
    rolle = Vereinsrolle(
        name=daten.name,
        beschreibung=daten.beschreibung,
        pflichtstunden_befreit=daten.pflichtstunden_befreit,
        befreiungsgrund=BefreiungsGrund(daten.befreiungsgrund) if daten.befreiungsgrund else None,
    )
    db.add(rolle)
    await db.commit()
    await db.refresh(rolle)
    return rolle


@router.put("/vereinsrollen/{rolle_id}", response_model=VereinsrolleOut, summary="Vereinsrolle aktualisieren")
async def vereinsrolle_aktualisieren(
    rolle_id: str,
    daten: VereinsrolleCreate,
    db: AsyncSession = Depends(get_db),
    benutzer: Benutzer = Depends(require_schreibzugriff),
):
    result = await db.execute(select(Vereinsrolle).where(Vereinsrolle.id == rolle_id))
    rolle = result.scalar_one_or_none()
    if not rolle:
        raise HTTPException(status_code=404, detail="Vereinsrolle nicht gefunden")

    rolle.name = daten.name
    rolle.beschreibung = daten.beschreibung
    rolle.pflichtstunden_befreit = daten.pflichtstunden_befreit
    rolle.befreiungsgrund = BefreiungsGrund(daten.befreiungsgrund) if daten.befreiungsgrund else None

    await db.commit()
    await db.refresh(rolle)
    return rolle


@router.delete(
    "/vereinsrollen/{rolle_id}", status_code=status.HTTP_204_NO_CONTENT,
    summary="Vereinsrolle löschen",
    description="Löscht die Rolle inkl. aller Member-Zuordnungen (Cascade).",
)
async def vereinsrolle_loeschen(
    rolle_id: str,
    db: AsyncSession = Depends(get_db),
    benutzer: Benutzer = Depends(require_schreibzugriff),
):
    result = await db.execute(select(Vereinsrolle).where(Vereinsrolle.id == rolle_id))
    rolle = result.scalar_one_or_none()
    if rolle:
        await db.delete(rolle)
        await db.commit()


@router.get(
    "/vereinsrollen/zuordnungen", response_model=List[MitgliedVereinsrolleOut],
    summary="Member-Vereinsrolle-Zuordnungen auflisten",
)
async def zuordnungen_auflisten(
    jahr: Optional[int] = Query(None),
    mitglied_id: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    benutzer: Benutzer = Depends(get_current_api_user),
):
    query = select(MitgliedVereinsrolle)
    if jahr:
        query = query.where(MitgliedVereinsrolle.jahr == jahr)
    if mitglied_id:
        query = query.where(MitgliedVereinsrolle.mitglied_id == mitglied_id)
    result = await db.execute(query)
    return result.scalars().all()


@router.post(
    "/vereinsrollen/zuordnungen", response_model=MitgliedVereinsrolleOut,
    status_code=status.HTTP_201_CREATED, summary="Member einer Vereinsrolle zuordnen",
)
async def zuordnung_erstellen(
    daten: MitgliedVereinsrolleCreate,
    db: AsyncSession = Depends(get_db),
    benutzer: Benutzer = Depends(require_schreibzugriff),
):
    zuordnung = MitgliedVereinsrolle(**daten.model_dump())
    db.add(zuordnung)
    await db.commit()
    await db.refresh(zuordnung)
    return zuordnung


@router.delete(
    "/vereinsrollen/zuordnungen/{zuordnung_id}", status_code=status.HTTP_204_NO_CONTENT,
    summary="Zuordnung entfernen",
)
async def zuordnung_loeschen(
    zuordnung_id: str,
    db: AsyncSession = Depends(get_db),
    benutzer: Benutzer = Depends(require_schreibzugriff),
):
    result = await db.execute(select(MitgliedVereinsrolle).where(MitgliedVereinsrolle.id == zuordnung_id))
    zuordnung = result.scalar_one_or_none()
    if zuordnung:
        await db.delete(zuordnung)
        await db.commit()


# ---------------------------------------------------------------------------
# Arbeitseinsätze
# ---------------------------------------------------------------------------

@router.get("/einsaetze", response_model=List[ArbeitseinsatzOut], summary="Arbeitseinsätze auflisten")
async def einsaetze_auflisten(
    jahr: Optional[int] = Query(None, description="Nach Jahr filtern"),
    typ: Optional[str] = Query(None, description="STANDARD oder BESONDERS"),
    db: AsyncSession = Depends(get_db),
    benutzer: Benutzer = Depends(get_current_api_user),
):
    query = select(Arbeitseinsatz).order_by(Arbeitseinsatz.datum.desc())
    if jahr:
        from sqlalchemy import extract
        query = query.where(extract("year", Arbeitseinsatz.datum) == jahr)
    if typ:
        query = query.where(Arbeitseinsatz.typ == EinsatzTyp(typ))
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/einsaetze/{einsatz_id}", response_model=ArbeitseinsatzOut, summary="Einsatz abrufen")
async def einsatz_abrufen(
    einsatz_id: str,
    db: AsyncSession = Depends(get_db),
    benutzer: Benutzer = Depends(get_current_api_user),
):
    result = await db.execute(select(Arbeitseinsatz).where(Arbeitseinsatz.id == einsatz_id))
    einsatz = result.scalar_one_or_none()
    if not einsatz:
        raise HTTPException(status_code=404, detail="Einsatz nicht gefunden")
    return einsatz


@router.post(
    "/einsaetze", response_model=ArbeitseinsatzOut, status_code=status.HTTP_201_CREATED,
    summary="Arbeitseinsatz anlegen",
)
async def einsatz_erstellen(
    daten: ArbeitseinsatzCreate,
    db: AsyncSession = Depends(get_db),
    benutzer: Benutzer = Depends(require_schreibzugriff),
):
    einsatz = Arbeitseinsatz(
        titel=daten.titel, beschreibung=daten.beschreibung, typ=EinsatzTyp(daten.typ),
        datum=daten.datum, uhrzeit_von=daten.uhrzeit_von, uhrzeit_bis=daten.uhrzeit_bis,
        max_teilnehmer=daten.max_teilnehmer, stunden_pro_teilnehmer=daten.stunden_pro_teilnehmer,
        erstellt_von_id=benutzer.id,
    )
    db.add(einsatz)
    await db.commit()
    await db.refresh(einsatz)
    return einsatz


@router.put("/einsaetze/{einsatz_id}", response_model=ArbeitseinsatzOut, summary="Einsatz aktualisieren")
async def einsatz_aktualisieren(
    einsatz_id: str,
    daten: ArbeitseinsatzUpdate,
    db: AsyncSession = Depends(get_db),
    benutzer: Benutzer = Depends(require_schreibzugriff),
):
    result = await db.execute(select(Arbeitseinsatz).where(Arbeitseinsatz.id == einsatz_id))
    einsatz = result.scalar_one_or_none()
    if not einsatz:
        raise HTTPException(status_code=404, detail="Einsatz nicht gefunden")

    update_daten = daten.model_dump(exclude_unset=True)
    if "typ" in update_daten:
        update_daten["typ"] = EinsatzTyp(update_daten["typ"])
    for feld, wert in update_daten.items():
        setattr(einsatz, feld, wert)

    await db.commit()
    await db.refresh(einsatz)
    return einsatz


@router.delete(
    "/einsaetze/{einsatz_id}", status_code=status.HTTP_204_NO_CONTENT,
    summary="Einsatz löschen", description="Löscht auch alle Teilnahmen (Cascade).",
)
async def einsatz_loeschen(
    einsatz_id: str,
    db: AsyncSession = Depends(get_db),
    benutzer: Benutzer = Depends(require_schreibzugriff),
):
    result = await db.execute(select(Arbeitseinsatz).where(Arbeitseinsatz.id == einsatz_id))
    einsatz = result.scalar_one_or_none()
    if einsatz:
        await db.delete(einsatz)
        await db.commit()


# ---------------------------------------------------------------------------
# Teilnahmen (Unterressource von Einsätzen)
# ---------------------------------------------------------------------------

@router.get(
    "/einsaetze/{einsatz_id}/teilnahmen", response_model=List[EinsatzTeilnahmeOut],
    summary="Teilnahmen eines Einsatzes auflisten",
)
async def teilnahmen_auflisten(
    einsatz_id: str,
    db: AsyncSession = Depends(get_db),
    benutzer: Benutzer = Depends(get_current_api_user),
):
    result = await db.execute(
        select(EinsatzTeilnahme).where(EinsatzTeilnahme.einsatz_id == einsatz_id)
    )
    return result.scalars().all()


@router.post(
    "/einsaetze/{einsatz_id}/teilnahmen", response_model=EinsatzTeilnahmeOut,
    status_code=status.HTTP_201_CREATED, summary="Teilnahme eintragen",
)
async def teilnahme_erstellen(
    einsatz_id: str,
    daten: EinsatzTeilnahmeCreate,
    db: AsyncSession = Depends(get_db),
    benutzer: Benutzer = Depends(require_schreibzugriff),
):
    existing = await db.execute(
        select(EinsatzTeilnahme).where(
            EinsatzTeilnahme.einsatz_id == einsatz_id, EinsatzTeilnahme.mitglied_id == daten.mitglied_id
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Member ist bereits eingetragen")

    teilnahme = EinsatzTeilnahme(
        einsatz_id=einsatz_id, mitglied_id=daten.mitglied_id,
        status=TeilnahmeStatus(daten.status), stunden_geleistet=daten.stunden_geleistet,
        notiz=daten.notiz,
    )
    db.add(teilnahme)
    await db.commit()
    await db.refresh(teilnahme)
    return teilnahme


@router.put(
    "/einsaetze/{einsatz_id}/teilnahmen/{teilnahme_id}", response_model=EinsatzTeilnahmeOut,
    summary="Teilnahme aktualisieren",
)
async def teilnahme_aktualisieren(
    einsatz_id: str,
    teilnahme_id: str,
    daten: EinsatzTeilnahmeUpdate,
    db: AsyncSession = Depends(get_db),
    benutzer: Benutzer = Depends(require_schreibzugriff),
):
    result = await db.execute(
        select(EinsatzTeilnahme).where(
            EinsatzTeilnahme.id == teilnahme_id, EinsatzTeilnahme.einsatz_id == einsatz_id
        )
    )
    teilnahme = result.scalar_one_or_none()
    if not teilnahme:
        raise HTTPException(status_code=404, detail="Teilnahme nicht gefunden")

    update_daten = daten.model_dump(exclude_unset=True)
    if "status" in update_daten:
        update_daten["status"] = TeilnahmeStatus(update_daten["status"])
    for feld, wert in update_daten.items():
        setattr(teilnahme, feld, wert)

    await db.commit()
    await db.refresh(teilnahme)
    return teilnahme


@router.delete(
    "/einsaetze/{einsatz_id}/teilnahmen/{teilnahme_id}", status_code=status.HTTP_204_NO_CONTENT,
    summary="Teilnahme entfernen",
)
async def teilnahme_loeschen(
    einsatz_id: str,
    teilnahme_id: str,
    db: AsyncSession = Depends(get_db),
    benutzer: Benutzer = Depends(require_schreibzugriff),
):
    result = await db.execute(
        select(EinsatzTeilnahme).where(
            EinsatzTeilnahme.id == teilnahme_id, EinsatzTeilnahme.einsatz_id == einsatz_id
        )
    )
    teilnahme = result.scalar_one_or_none()
    if teilnahme:
        await db.delete(teilnahme)
        await db.commit()


# ---------------------------------------------------------------------------
# Patenschaften
# ---------------------------------------------------------------------------

@router.get("/patenschaften", response_model=List[PatenschaftOut], summary="Patenschaften auflisten")
async def patenschaften_auflisten(
    jahr: Optional[int] = Query(None, description="Nur Patenschaften, die in diesem Jahr aktiv waren"),
    db: AsyncSession = Depends(get_db),
    benutzer: Benutzer = Depends(get_current_api_user),
):
    query = select(Patenschaft).order_by(Patenschaft.bereich)
    if jahr:
        query = query.where(
            Patenschaft.von <= date(jahr, 12, 31),
            (Patenschaft.bis.is_(None)) | (Patenschaft.bis >= date(jahr, 1, 1)),
        )
    result = await db.execute(query)
    return result.scalars().all()


@router.post(
    "/patenschaften", response_model=PatenschaftOut, status_code=status.HTTP_201_CREATED,
    summary="Patenschaft anlegen",
    description="mitglied_id ist optional – eine Patenschaft kann angelegt werden, bevor sie vergeben ist.",
)
async def patenschaft_erstellen(
    daten: PatenschaftCreate,
    db: AsyncSession = Depends(get_db),
    benutzer: Benutzer = Depends(require_schreibzugriff),
):
    patenschaft = Patenschaft(**daten.model_dump())
    db.add(patenschaft)
    await db.commit()
    await db.refresh(patenschaft)
    return patenschaft


@router.put("/patenschaften/{patenschaft_id}", response_model=PatenschaftOut, summary="Patenschaft aktualisieren")
async def patenschaft_aktualisieren(
    patenschaft_id: str,
    daten: PatenschaftUpdate,
    db: AsyncSession = Depends(get_db),
    benutzer: Benutzer = Depends(require_schreibzugriff),
):
    result = await db.execute(select(Patenschaft).where(Patenschaft.id == patenschaft_id))
    patenschaft = result.scalar_one_or_none()
    if not patenschaft:
        raise HTTPException(status_code=404, detail="Patenschaft nicht gefunden")

    for feld, wert in daten.model_dump(exclude_unset=True).items():
        setattr(patenschaft, feld, wert)

    await db.commit()
    await db.refresh(patenschaft)
    return patenschaft


@router.delete(
    "/patenschaften/{patenschaft_id}", status_code=status.HTTP_204_NO_CONTENT,
    summary="Patenschaft löschen",
)
async def patenschaft_loeschen(
    patenschaft_id: str,
    db: AsyncSession = Depends(get_db),
    benutzer: Benutzer = Depends(require_schreibzugriff),
):
    result = await db.execute(select(Patenschaft).where(Patenschaft.id == patenschaft_id))
    patenschaft = result.scalar_one_or_none()
    if patenschaft:
        await db.delete(patenschaft)
        await db.commit()


# ---------------------------------------------------------------------------
# Auswertung
# ---------------------------------------------------------------------------

@router.get(
    "/auswertung/{jahr}", response_model=List[AuswertungZeileOut],
    summary="Jahresauswertung abrufen",
    description=(
        "Berechnet je nach konfiguriertem Modus (PRO_PACHTVERTRAG oder PRO_MITGLIED) "
        "den Pflichtstunden-Stand: geleistete Stunden, offene Stunden, Schuldbetrag, "
        "Befreiungsstatus."
    ),
)
async def auswertung_abrufen(
    jahr: int,
    db: AsyncSession = Depends(get_db),
    benutzer: Benutzer = Depends(get_current_api_user),
):
    from app.routers.pflichtstunden import (
        _get_config_fuer_jahr, _berechne_stunden_fuer_mitglied, _ist_befreit
    )

    config = await _get_config_fuer_jahr(db, jahr)
    if not config:
        raise HTTPException(status_code=404, detail=f"Keine Konfiguration für {jahr}")

    zeilen: List[AuswertungZeileOut] = []
    pflicht = Decimal(str(config.stunden_gesamt))

    if config.modus == PflichtstundenModus.PRO_PACHTVERTRAG:
        result = await db.execute(
            select(Parcel)
            .options(selectinload(Parcel.member_assignments).selectinload(MemberParcel.member))
            .where(Parcel.status == ParcelStatus.ACTIVE)
            .order_by(Parcel.plot_number)
        )
        for parzelle in result.scalars().all():
            paechter = [z.member for z in parzelle.member_assignments]
            if not paechter:
                continue
            gesamt = Decimal("0")
            # Vier-Augen-freundliche Regel: EIN befreiter Pächter genügt, um
            # die gesamte Parcel zu befreien (any(), nicht all() – siehe
            # docs/architektur-entscheidungen.md).
            ist_befreit = False
            for m in paechter:
                stand = await _berechne_stunden_fuer_mitglied(db, m.id, jahr)
                gesamt += Decimal(str(stand["gesamt"]))
                if await _ist_befreit(db, m.id, jahr):
                    ist_befreit = True
            offen = max(Decimal("0"), pflicht - gesamt) if not ist_befreit else Decimal("0")
            zeilen.append(AuswertungZeileOut(
                bezeichnung=parzelle.plot_number,
                pflicht_stunden=pflicht, geleistete_stunden=gesamt, offen_stunden=offen,
                schuldbetrag_eur=offen * Decimal(str(config.stundensatz_eur)),
                befreit=ist_befreit, erfuellt=ist_befreit or gesamt >= pflicht,
            ))
    else:
        result = await db.execute(
            select(Member)
            .options(selectinload(Member.parcel_assignments))
            .where(Member.deleted_at.is_(None), Member.parcel_assignments.any())
            .order_by(Member.last_name, Member.first_name)
        )
        for m in result.scalars().all():
            stand = await _berechne_stunden_fuer_mitglied(db, m.id, jahr)
            befreit = await _ist_befreit(db, m.id, jahr)
            gesamt = Decimal(str(stand["gesamt"]))
            offen = max(Decimal("0"), pflicht - gesamt) if not befreit else Decimal("0")
            zeilen.append(AuswertungZeileOut(
                bezeichnung=m.full_name,
                pflicht_stunden=pflicht, geleistete_stunden=gesamt, offen_stunden=offen,
                schuldbetrag_eur=offen * Decimal(str(config.stundensatz_eur)),
                befreit=befreit, erfuellt=befreit or gesamt >= pflicht,
            ))

    return zeilen
