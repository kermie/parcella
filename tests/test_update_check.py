from datetime import datetime, timezone

from app.database import AsyncSessionLocal
from app.models import ClubSetting
from app.update_check import (
    get_update_status,
    is_newer,
    refresh_update_check_cache,
)


def test_is_newer_compares_dotted_versions():
    assert is_newer("0.2.0", "0.1.0") is True
    assert is_newer("0.1.0", "0.1.0") is False
    assert is_newer("0.1.0", "0.2.0") is False
    assert is_newer("1.0.0", "0.9.9") is True


def test_is_newer_returns_false_for_unparseable_versions():
    assert is_newer("not-a-version", "0.1.0") is False
    assert is_newer("0.1.0", "not-a-version") is False


async def test_get_update_status_defaults_when_nothing_cached_yet():
    async with AsyncSessionLocal() as db:
        status = await get_update_status(db)

    assert status.enabled is True  # default: on
    assert status.latest_version is None
    assert status.checked_at is None
    assert status.update_available is False


async def test_refresh_skips_the_network_call_when_disabled():
    async with AsyncSessionLocal() as db:
        db.add(ClubSetting(key="update_check_enabled", value="false"))
        await db.commit()

        await refresh_update_check_cache(db)

        status = await get_update_status(db)
        assert status.enabled is False
        # Nothing was ever written, since the check short-circuits.
        assert status.latest_version is None
        assert status.checked_at is None


async def test_get_update_status_reports_update_available_from_cache():
    """
    Doesn't exercise the real GitHub call (see app/update_check.py --
    same "no live external call in tests" convention as the other
    integrations) -- seeds the cache the background loop would
    normally populate, and checks the comparison against app_version
    (currently "0.1.0", see app/config.py) reads it correctly.
    """
    async with AsyncSessionLocal() as db:
        db.add(ClubSetting(key="update_check_latest_version", value="9.9.9"))
        db.add(ClubSetting(key="update_check_checked_at", value=datetime.now(timezone.utc).isoformat()))
        await db.commit()

        status = await get_update_status(db)
        assert status.update_available is True
        assert status.latest_version == "9.9.9"
        assert status.checked_at is not None
