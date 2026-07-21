from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from app.config import settings

engine = create_async_engine(
    settings.database_url,
    echo=settings.is_development,
    pool_pre_ping=True,
    pool_recycle=1800,  # proactively refresh connections after 30 min,
                        # prevents "MissingGreenlet" errors on long-idle/
                        # stale connections
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
    """Creates all tables (development only; Alembic handles production)."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

from datetime import date as _date
from sqlalchemy import and_ as _and_

def active_member_filter():
    """
    Default filter for active members:
    - not soft-deleted (deleted_at IS NULL)
    - membership not expired (member_until IS NULL or in the future)

    Usage: .where(active_member_filter())
    """
    from app.models import Member
    return _and_(
        Member.deleted_at.is_(None),
        (Member.member_until.is_(None)) | (Member.member_until >= _date.today())
    )
