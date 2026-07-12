from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from app.config import settings

engine = create_async_engine(
    settings.database_url,
    echo=settings.is_development,
    pool_pre_ping=True,
    pool_recycle=1800,  # Verbindungen nach 30 Min. proaktiv erneuern,
                        # verhindert "MissingGreenlet"-Fehler bei
                        # lange ungenutzten/stale Verbindungen
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    pass


async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


async def init_db():
    """Erstellt alle Tabellen (nur für Entwicklung; in Produktion: Alembic)."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

from datetime import date as _date
from sqlalchemy import and_ as _and_

def active_member_filter():
    """
    Standardfilter für aktive Mitglieder:
    - nicht soft-deleted (deleted_at IS NULL)
    - Mitgliedschaft nicht abgelaufen (member_until IS NULL oder in der Zukunft)

    Verwendung: .where(active_member_filter())
    """
    from app.models import Member
    return _and_(
        Member.deleted_at.is_(None),
        (Member.member_until.is_(None)) | (Member.member_until >= _date.today())
    )
