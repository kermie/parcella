"""
API-Router: Einkaufswünsche – Vier-Augen-Prinzip für Vereinsausgaben.
"""
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models import Einkaufswunsch, EinkaufswunschFreigabe, EinkaufswunschStatus, Benutzer, BenutzerRolle
from app.api_auth import get_current_api_user, require_api_rolle
from app.module_flags import require_modul
from app.schemas import (
    EinkaufswunschCreate, EinkaufswunschOut, EinkaufswunschDetailOut, EinkaufswunschAblehnenRequest,
)

router = APIRouter(
    prefix="/api/v1/einkaufswuensche",
    tags=["API: Einkaufswünsche"],
    dependencies=[Depends(require_modul("einkaufswuensche"))],
)

_NOETIGE_FREIGABEN = 2
require_vorstand_api = require_api_rolle(BenutzerRolle.ADMIN, BenutzerRolle.VORSTAND)


async def _lade_mit_details(db: AsyncSession, ew_id: str) -> Optional[Einkaufswunsch]:
    result = await db.execute(
        select(Einkaufswunsch)
        .options(selectinload(Einkaufswunsch.freigaben))
        .where(Einkaufswunsch.id == ew_id)
    )
    return result.scalar_one_or_none()


@router.get("", response_model=List[EinkaufswunschOut], summary="Einkaufswünsche auflisten")
async def auflisten(
    status_filter: Optional[str] = Query(None, alias="status"),
    db: AsyncSession = Depends(get_db),
    benutzer: Benutzer = Depends(get_current_api_user),
):
    query = select(Einkaufswunsch).order_by(Einkaufswunsch.erstellt_am.desc())
    if status_filter:
        query = query.where(Einkaufswunsch.status == EinkaufswunschStatus(status_filter))
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/{ew_id}", response_model=EinkaufswunschDetailOut, summary="Einkaufswunsch inkl. Freigaben abrufen")
async def abrufen(
    ew_id: str,
    db: AsyncSession = Depends(get_db),
    benutzer: Benutzer = Depends(get_current_api_user),
):
    ew = await _lade_mit_details(db, ew_id)
    if not ew:
        raise HTTPException(status_code=404, detail="Einkaufswunsch nicht gefunden")
    return ew


@router.post(
    "", response_model=EinkaufswunschOut, status_code=status.HTTP_201_CREATED,
    summary="Einkaufswunsch anlegen",
    description="Ohne anfragender_email wird der aufrufende Benutzer selbst als Antragsteller "
                "eingetragen. Mit anfragender_email wird ein Bestätigungslink per E-Mail "
                "verschickt (Deep-Link-Bestätigung ohne Login).",
)
async def erstellen(
    daten: EinkaufswunschCreate,
    db: AsyncSession = Depends(get_db),
    benutzer: Benutzer = Depends(get_current_api_user),
):
    ew = Einkaufswunsch(
        titel=daten.titel, begruendung=daten.begruendung, link=daten.link,
        geschaetzte_kosten_eur=daten.geschaetzte_kosten_eur,
        erstellt_von_id=benutzer.id,
    )

    if daten.anfragender_email:
        from app.auth import serializer
        ew.anfragender_name = daten.anfragender_name
        ew.anfragender_email = str(daten.anfragender_email).lower()
        ew.bestaetigungs_token = serializer.dumps(str(daten.anfragender_email).lower(), salt="einkaufswunsch")
    else:
        ew.angefragt_von_id = benutzer.id

    db.add(ew)
    await db.commit()
    await db.refresh(ew)

    if ew.bestaetigungs_token:
        from app.email_service import sende_email
        from app.config import settings
        betreff = f"Bitte bestätigen: Einkaufswunsch „{ew.titel}“"
        html = (
            f"<html><body><p>Hallo {ew.anfragender_name or ''},</p>"
            f"<p>{benutzer.name} hat in Ihrem Namen einen Einkaufswunsch im {settings.app_name} erfasst: "
            f"<strong>{ew.titel}</strong></p>"
            f"<p>Bitte melden Sie sich, um die Angaben zu prüfen.</p></body></html>"
        )
        await sende_email(ew.anfragender_email, betreff, html, db=db)

    return ew


@router.post(
    "/{ew_id}/freigeben", response_model=EinkaufswunschDetailOut,
    summary="Freigabe erteilen",
    description="Nur Vorstand/Admin. Der Antragsteller darf nicht selbst freigeben. "
                "Bei Erreichen von 2 unterschiedlichen Freigaben wechselt der Status auf GENEHMIGT.",
)
async def freigeben(
    ew_id: str,
    db: AsyncSession = Depends(get_db),
    benutzer: Benutzer = Depends(require_vorstand_api),
):
    ew = await _lade_mit_details(db, ew_id)
    if not ew:
        raise HTTPException(status_code=404, detail="Einkaufswunsch nicht gefunden")

    if ew.status != EinkaufswunschStatus.OFFEN:
        raise HTTPException(status_code=409, detail=f"Einkaufswunsch ist bereits {ew.status.value}")

    if benutzer.id in (ew.angefragt_von_id, ew.erstellt_von_id):
        raise HTTPException(
            status_code=403,
            detail="Der Antragsteller darf seinen eigenen Einkaufswunsch nicht mitfreigeben (Vier-Augen-Prinzip)."
        )

    if any(f.benutzer_id == benutzer.id for f in ew.freigaben):
        raise HTTPException(status_code=409, detail="Sie haben bereits freigegeben.")

    db.add(EinkaufswunschFreigabe(einkaufswunsch_id=ew_id, benutzer_id=benutzer.id))
    await db.flush()

    if len(ew.freigaben) + 1 >= _NOETIGE_FREIGABEN:
        ew.status = EinkaufswunschStatus.GENEHMIGT
        ew.genehmigt_am = datetime.now(timezone.utc)

    await db.commit()
    return await _lade_mit_details(db, ew_id)


@router.post(
    "/{ew_id}/ablehnen", response_model=EinkaufswunschOut,
    summary="Einkaufswunsch ablehnen",
    description="Nur Vorstand/Admin. Eine einzelne Ablehnung genügt (Veto-Prinzip).",
)
async def ablehnen(
    ew_id: str,
    daten: EinkaufswunschAblehnenRequest,
    db: AsyncSession = Depends(get_db),
    benutzer: Benutzer = Depends(require_vorstand_api),
):
    ew = await _lade_mit_details(db, ew_id)
    if not ew:
        raise HTTPException(status_code=404, detail="Einkaufswunsch nicht gefunden")

    if ew.status != EinkaufswunschStatus.OFFEN:
        raise HTTPException(status_code=409, detail=f"Einkaufswunsch ist bereits {ew.status.value}")

    ew.status = EinkaufswunschStatus.ABGELEHNT
    ew.ablehnungsgrund = daten.ablehnungsgrund
    ew.abgelehnt_von_id = benutzer.id
    ew.abgelehnt_am = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(ew)
    return ew
