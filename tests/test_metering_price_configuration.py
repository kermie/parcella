"""
Issue: give water_usage/electricity_usage invoice pricing modes full
parity with work_hours_shortfall/insurance_cost -- price pulled from a
new per-year MeteringPriceConfiguration instead of a manually-entered
unit_price, and scope automatically limited to parcels with an active
meter of that medium (see docs/ADR/0056).

Covers both layers: the REST API CRUD for the new configuration
(mirrors tests/test_work_hours.py's test_configuration_upsert), and the
web CRUD routes added to create_metering_router()'s factory.
"""
from tests.conftest import login, auth_header


async def test_price_configuration_upsert_via_api(client, admin_user):
    token = await login(client, "admin@example.com")
    headers = auth_header(token)

    create = await client.put(
        "/api/v1/water/configuration/2026",
        json={"medium": "WATER", "year": 2026, "price_per_unit": "2.50", "note": "initial"},
        headers=headers,
    )
    assert create.status_code == 200
    assert float(create.json()["price_per_unit"]) == 2.50

    update = await client.put(
        "/api/v1/water/configuration/2026",
        json={"medium": "WATER", "year": 2026, "price_per_unit": "2.75", "note": "rate change"},
        headers=headers,
    )
    assert update.status_code == 200
    assert float(update.json()["price_per_unit"]) == 2.75

    listing = await client.get("/api/v1/water/configuration", headers=headers)
    assert listing.status_code == 200
    assert len(listing.json()) == 1

    get_year = await client.get("/api/v1/water/configuration/2026", headers=headers)
    assert get_year.status_code == 200
    assert float(get_year.json()["price_per_unit"]) == 2.75

    missing_year = await client.get("/api/v1/water/configuration/1999", headers=headers)
    assert missing_year.status_code == 404


async def test_water_and_electricity_prices_are_independent(client, admin_user):
    token = await login(client, "admin@example.com")
    headers = auth_header(token)

    await client.put(
        "/api/v1/water/configuration/2026",
        json={"medium": "WATER", "year": 2026, "price_per_unit": "2.50"},
        headers=headers,
    )
    await client.put(
        "/api/v1/electricity/configuration/2026",
        json={"medium": "ELECTRICITY", "year": 2026, "price_per_unit": "0.35"},
        headers=headers,
    )

    water = await client.get("/api/v1/water/configuration/2026", headers=headers)
    electricity = await client.get("/api/v1/electricity/configuration/2026", headers=headers)
    assert float(water.json()["price_per_unit"]) == 2.50
    assert float(electricity.json()["price_per_unit"]) == 0.35


async def web_login(client, email: str = "admin@example.com", password: str = "testpasswort123") -> None:
    response = await client.post("/auth/login", data={"email": email, "password": password})
    assert response.status_code in (302, 303)


async def test_price_configuration_web_pages_render(client, admin_user):
    await web_login(client, "admin@example.com")

    create = await client.post(
        "/water/configuration/new",
        data={"year": "2026", "price_per_unit": "2.50", "note": "test"},
    )
    assert create.status_code in (302, 303)

    page = await client.get("/water/configuration")
    assert page.status_code == 200
    assert "2,50" in page.text or "2.50" in page.text


async def test_price_configuration_year_collision_rejected_on_edit(client, admin_user):
    await web_login(client, "admin@example.com")

    await client.post("/water/configuration/new", data={"year": "2026", "price_per_unit": "2.50"})
    await client.post("/water/configuration/new", data={"year": "2027", "price_per_unit": "2.60"})

    from sqlalchemy import select
    from app.database import AsyncSessionLocal
    from app.models import MeteringPriceConfiguration, MeteringMedium

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(MeteringPriceConfiguration).where(
                MeteringPriceConfiguration.medium == MeteringMedium.WATER,
                MeteringPriceConfiguration.year == 2027,
            )
        )
        config_2027 = result.scalar_one()

    resp = await client.post(
        f"/water/configuration/{config_2027.id}/edit",
        data={"year": "2026", "price_per_unit": "2.60"},
    )
    assert resp.status_code == 400
