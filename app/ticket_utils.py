"""
Hilfsfunktionen für das Ticketsystem: automatischer Member-Abgleich
per Absender-E-Mail-Adresse.
"""
from typing import List

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.models import Member, MemberEmail


async def finde_mitglieder_per_email(db: AsyncSession, email: str) -> List[Member]:
    """
    Sucht Mitglieder, deren hinterlegte E-Mail-Adresse mit der übergebenen
    übereinstimmt (case-insensitive). Gibt eine Liste zurück, da dieselbe
    Adresse mehreren Mitgliedern gehören kann (z.B. Ehepaare) – in diesem
    Fall trifft die Automatik bewusst KEINE Entscheidung, sondern überlässt
    die Auswahl der Oberfläche (analog zur Unfallversicherung-Logik).
    """
    result = await db.execute(
        select(Member)
        .join(MemberEmail, MemberEmail.member_id == Member.id)
        .where(
            func.lower(MemberEmail.address) == email.strip().lower(),
            Member.deleted_at.is_(None),
        )
    )
    return result.scalars().all()
