"""
API-Router: Vereinseinstellungen (Stammdaten des Vereins, Flächenangaben etc.).
"""
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db
from app.models import ClubSetting, User
from app.api_auth import get_current_api_user, require_admin_api
from app.schemas import ClubSettingOut, ClubSettingUpdate

router = APIRouter(prefix="/api/v1/club-settings", tags=["API: Club Settings"])


@router.get(
    "",
    response_model=List[ClubSettingOut],
    summary="Retrieve all club settings",
    description="Returns club master data such as name, address, and A/B/C area sizes as a key-value list.",
)
async def settings_list(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_api_user),
):
    result = await db.execute(select(ClubSetting))
    return result.scalars().all()


@router.get(
    "/{key}",
    response_model=ClubSettingOut,
    summary="Retrieve a single setting",
)
async def setting_get(
    key: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_api_user),
):
    result = await db.execute(
        select(ClubSetting).where(ClubSetting.key == key)
    )
    entry = result.scalar_one_or_none()
    if not entry:
        raise HTTPException(status_code=404, detail="Einstellung nicht gefunden")
    return entry


@router.put(
    "/{key}",
    response_model=ClubSettingOut,
    summary="Set or update a setting",
    description="Creates the key if it does not exist yet (upsert). Admin/board only.",
)
async def setting_set(
    key: str,
    daten: ClubSettingUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin_api),
):
    result = await db.execute(
        select(ClubSetting).where(ClubSetting.key == key)
    )
    entry = result.scalar_one_or_none()

    if entry:
        entry.value = daten.value
    else:
        entry = ClubSetting(key=key, value=daten.value)
        db.add(entry)

    await db.commit()
    await db.refresh(entry)
    return entry
