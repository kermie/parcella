"""
Tests for metering (water & electricity). Focus: the monotonicity
check (a reading may not decrease) and consumption calculation.
"""
from tests.conftest import login, auth_header


async def test_metering_point_create_and_reading(client, admin_user):
    token = await login(client, "admin@example.com")
    headers = auth_header(token)

    parcel = (await client.post(
        "/api/v1/parcels", json={"plot_number": "G200"}, headers=headers
    )).json()

    metering_point = (await client.post(
        "/api/v1/water/metering-points",
        json={
            "type": "PARCEL", "parcel_id": parcel["id"],
            "number": "W-12345", "initial_reading": "0.0",
        },
        headers=headers,
    )).json()
    assert metering_point["current_meter"]["number"] == "W-12345"

    entry = await client.post(
        f"/api/v1/water/metering-points/{metering_point['id']}/readings",
        json={"year": 2026, "date": "2026-10-01", "reading": "12.5"},
        headers=headers,
    )
    assert entry.status_code == 201


async def test_reading_may_not_decrease(client, admin_user):
    """
    Core rule of the plausibility check: a new reading must be at
    least as high as the previous one of the same water meter.
    """
    token = await login(client, "admin@example.com")
    headers = auth_header(token)

    metering_point = (await client.post(
        "/api/v1/water/metering-points",
        json={"type": "CLUB", "label": "Vereinsheim", "number": "W-99999", "initial_reading": "0.0"},
        headers=headers,
    )).json()

    r1 = await client.post(
        f"/api/v1/water/metering-points/{metering_point['id']}/readings",
        json={"year": 2025, "date": "2025-10-01", "reading": "50.0"},
        headers=headers,
    )
    assert r1.status_code == 201

    # A lower reading in the following year must be rejected
    r2 = await client.post(
        f"/api/v1/water/metering-points/{metering_point['id']}/readings",
        json={"year": 2026, "date": "2026-10-01", "reading": "30.0"},
        headers=headers,
    )
    assert r2.status_code == 422

    # A higher reading is perfectly fine
    r3 = await client.post(
        f"/api/v1/water/metering-points/{metering_point['id']}/readings",
        json={"year": 2026, "date": "2026-10-01", "reading": "75.0"},
        headers=headers,
    )
    assert r3.status_code == 201


async def test_consumption_calculation(client, admin_user):
    """Consumption = current reading minus last reading (or initial reading)."""
    token = await login(client, "admin@example.com")
    headers = auth_header(token)

    metering_point = (await client.post(
        "/api/v1/water/metering-points",
        json={"type": "MAIN_METER", "label": "Hauptzähler", "number": "W-1", "initial_reading": "100.0"},
        headers=headers,
    )).json()

    await client.post(
        f"/api/v1/water/metering-points/{metering_point['id']}/readings",
        json={"year": 2026, "date": "2026-10-01", "reading": "150.0"},
        headers=headers,
    )

    evaluation = (await client.get("/api/v1/water/evaluation/2026", headers=headers)).json()
    zeile = next(z for z in evaluation if z["metering_point_id"] == metering_point["id"])
    assert float(zeile["consumption"]) == 50.0  # 150 - initial reading 100


async def test_electricity_and_water_separate(client, admin_user):
    """Water and electricity MeteringPoints must be independent, separate lists."""
    token = await login(client, "admin@example.com")
    headers = auth_header(token)

    await client.post(
        "/api/v1/water/metering-points",
        json={"type": "CLUB", "label": "Nur Wasser", "number": "W-A", "initial_reading": "0"},
        headers=headers,
    )
    await client.post(
        "/api/v1/electricity/metering-points",
        json={"type": "CLUB", "label": "Nur Strom", "number": "S-A", "initial_reading": "0"},
        headers=headers,
    )

    wasser_liste = (await client.get("/api/v1/water/metering-points", headers=headers)).json()
    strom_liste = (await client.get("/api/v1/electricity/metering-points", headers=headers)).json()

    assert len(wasser_liste) == 1
    assert len(strom_liste) == 1
    assert wasser_liste[0]["label"] == "Nur Wasser"
    assert strom_liste[0]["label"] == "Nur Strom"


# ---------------------------------------------------------------------------
# ADR 0070: shared service layer + unified (Group-based, not role-only)
# authorization for the API.
# ---------------------------------------------------------------------------

async def test_monotonicity_error_uses_shared_i18n_text_not_hardcoded_german(client, admin_user):
    """ADR 0070: the API used to resolve check_monotonicity()'s
    (key, params) via format_monotonicity_error_de() -- always German,
    regardless of the club's configured language -- instead of the
    shared i18n catalog the HTML side uses via t_for(). Both now share
    one code path (app.services.metering.record_reading)."""
    token = await login(client, "admin@example.com")
    headers = auth_header(token)

    metering_point = (await client.post(
        "/api/v1/water/metering-points",
        json={"type": "CLUB", "label": "i18n test", "number": "W-I18N", "initial_reading": "0.0"},
        headers=headers,
    )).json()
    await client.post(
        f"/api/v1/water/metering-points/{metering_point['id']}/readings",
        json={"year": 2025, "date": "2025-10-01", "reading": "50.0"},
        headers=headers,
    )

    response = await client.post(
        f"/api/v1/water/metering-points/{metering_point['id']}/readings",
        json={"year": 2026, "date": "2026-10-01", "reading": "30.0"},
        headers=headers,
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    # Static portions of the shared en.json text -- not asserting exact
    # Decimal formatting of {new_value}/{previous_value}, which depends
    # on DB numeric precision.
    assert "must not be smaller than the previous reading" in detail
    assert "Zählerstand" not in detail  # not the old hard-coded-German text


async def test_treasurer_without_group_grant_is_blocked_from_water_write_via_api(client):
    """ADR 0070: api_metering.py used require_write_access (role-only)
    -- ANY TREASURER could write metering data via the API regardless
    of Group configuration. Now the API checks the same Group-derived
    permission as HTML ("water"/"electricity" modules)."""
    from app.database import AsyncSessionLocal
    from app.models import User, UserRole
    from app.auth import hash_password

    async with AsyncSessionLocal() as db:
        user = User(
            email="treasurer-no-group-water@example.com", name="Treasurer No Group",
            password_hash=hash_password("testpasswort123"), role=UserRole.TREASURER,
        )
        db.add(user)
        await db.commit()

    token = await login(client, "treasurer-no-group-water@example.com")
    response = await client.post(
        "/api/v1/water/metering-points",
        json={"type": "CLUB", "label": "Blocked", "number": "W-BLOCK", "initial_reading": "0.0"},
        headers=auth_header(token),
    )
    assert response.status_code == 403


async def test_readonly_with_group_grant_can_write_water_via_api(client):
    """Flip side: a READONLY user granted water:write via a Group could
    already write metering data through the HTML UI, but the role-only
    API check blocked them regardless of Group membership."""
    from app.database import AsyncSessionLocal
    from app.models import User, UserRole, Group, GroupModulePermission, GroupMembership
    from app.auth import hash_password

    async with AsyncSessionLocal() as db:
        user = User(
            email="readonly-with-group-water@example.com", name="Readonly With Group",
            password_hash=hash_password("testpasswort123"), role=UserRole.READONLY,
        )
        db.add(user)
        await db.flush()

        group = Group(name="Water Handlers")
        db.add(group)
        await db.flush()
        db.add(GroupModulePermission(group_id=group.id, module="water", can_read=True, can_write=True))
        db.add(GroupMembership(user_id=user.id, group_id=group.id))
        await db.commit()

    token = await login(client, "readonly-with-group-water@example.com")
    response = await client.post(
        "/api/v1/water/metering-points",
        json={"type": "CLUB", "label": "Allowed", "number": "W-ALLOW", "initial_reading": "0.0"},
        headers=auth_header(token),
    )
    assert response.status_code == 201, response.text
