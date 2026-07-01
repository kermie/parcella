"""
API-Router: Statistiken für das Dashboard.
"""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_
from pydantic import BaseModel
from typing import Optional
from datetime import date

from app.database import get_db
from app.models import Mitglied, Parzelle, ParzelleStatus, MitgliedParzelle
from app.auth import get_current_user
from fastapi import Request

router = APIRouter(prefix="/api/v1/stats", tags=["API: Statistiken"])


class DashboardStats(BaseModel):
    mitglieder_gesamt: int
    mitglieder_aktiv: int
    parzellen_gesamt: int
    parzellen_aktiv: int
    parzellen_gekuendigt: int
    parzellen_unbesetzt: int
    flaeche_gesamt_qm: float
    flaeche_gekuendigt_qm: float


@router.get("", response_model=DashboardStats, summary="Dashboard-Statistiken")
async def dashboard_stats(db: AsyncSession = Depends(get_db)):
    # Mitglieder
    mitglieder_gesamt = await db.scalar(
        select(func.count()).where(Mitglied.deleted_at.is_(None))
    )
    mitglieder_aktiv = await db.scalar(
        select(func.count()).where(
            Mitglied.deleted_at.is_(None),
            Mitglied.mitglied_bis.is_(None) | (Mitglied.mitglied_bis >= date.today())
        )
    )

    # Parzellen nach Status
    parzellen_gesamt = await db.scalar(
        select(func.count()).select_from(Parzelle).where(
            Parzelle.status != ParzelleStatus.GELOESCHT
        )
    )
    parzellen_aktiv = await db.scalar(
        select(func.count()).select_from(Parzelle).where(
            Parzelle.status == ParzelleStatus.AKTIV
        )
    )
    parzellen_gekuendigt = await db.scalar(
        select(func.count()).select_from(Parzelle).where(
            Parzelle.status == ParzelleStatus.GEKUENDIGT
        )
    )

    # Unbesetzte Parzellen (aktiv, aber kein Mitglied zugeordnet)
    besetzte_ids = select(MitgliedParzelle.parzelle_id).distinct()
    parzellen_unbesetzt = await db.scalar(
        select(func.count()).select_from(Parzelle).where(
            Parzelle.status == ParzelleStatus.AKTIV,
            Parzelle.id.not_in(besetzte_ids)
        )
    )

    # Flächen
    flaeche_gesamt = await db.scalar(
        select(func.coalesce(func.sum(Parzelle.flaeche_qm), 0)).where(
            Parzelle.status == ParzelleStatus.AKTIV
        )
    )
    flaeche_gekuendigt = await db.scalar(
        select(func.coalesce(func.sum(Parzelle.flaeche_qm), 0)).where(
            Parzelle.status == ParzelleStatus.GEKUENDIGT
        )
    )

    return DashboardStats(
        mitglieder_gesamt=mitglieder_gesamt or 0,
        mitglieder_aktiv=mitglieder_aktiv or 0,
        parzellen_gesamt=parzellen_gesamt or 0,
        parzellen_aktiv=parzellen_aktiv or 0,
        parzellen_gekuendigt=parzellen_gekuendigt or 0,
        parzellen_unbesetzt=parzellen_unbesetzt or 0,
        flaeche_gesamt_qm=float(flaeche_gesamt or 0),
        flaeche_gekuendigt_qm=float(flaeche_gekuendigt or 0),
    )
