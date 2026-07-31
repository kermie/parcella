"""
Issue #168: a club-managed common area (paths, playground, etc.) can
now be tracked as a real Parcel row with ParcelStatus.COMMUNAL, so its
area_sqm is entered like any other plot. Before this, entering such a
parcel at all would have wrongly inflated Area A (leased parcels, see
app/area_utils.py's compute_area_a_sqm), since that sum previously
excluded only DELETED parcels.

Area B itself stays the existing manual "Total - Area A - Area C"
figure -- confirmed with the reporter this issue only fixes the Area A
leak, it does not switch Area B to summing COMMUNAL parcels directly
(see ADR 0057).
"""
from sqlalchemy import select

from app.area_utils import compute_area_a_sqm
from app.database import AsyncSessionLocal
from app.models import Parcel, ParcelStatus


async def test_communal_parcel_excluded_from_area_a(client, admin_user):
    await client.post("/auth/login", data={"email": "admin@example.com", "password": "testpasswort123"})

    async with AsyncSessionLocal() as session:
        session.add(Parcel(plot_number="COMMUNAL-1", area_sqm=300, status=ParcelStatus.ACTIVE))
        session.add(Parcel(plot_number="COMMUNAL-2", area_sqm=150, status=ParcelStatus.COMMUNAL))
        session.add(Parcel(plot_number="COMMUNAL-3", area_sqm=999, status=ParcelStatus.DELETED))
        await session.commit()

    async with AsyncSessionLocal() as session:
        area_a = await compute_area_a_sqm(session)

    assert area_a == 300.0, "COMMUNAL and DELETED parcels must not count toward Area A, only the ACTIVE one"


async def test_communal_status_is_freely_switchable_back_to_active(client, admin_user):
    """Confirmed with the reporter: this must stay a simple, reversible
    toggle via the existing status dropdown -- a club deciding to lease
    out a former common-area plot after all."""
    await client.post("/auth/login", data={"email": "admin@example.com", "password": "testpasswort123"})

    async with AsyncSessionLocal() as session:
        parcel = Parcel(plot_number="COMMUNAL-SWITCH", area_sqm=200, status=ParcelStatus.COMMUNAL)
        session.add(parcel)
        await session.commit()
        parcel_id = parcel.id

    r_update = await client.post(f"/parcels/{parcel_id}/edit", data={
        "plot_number": "COMMUNAL-SWITCH", "area_sqm": "200", "status": "ACTIVE",
    })
    assert r_update.status_code in (302, 303)

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Parcel).where(Parcel.id == parcel_id))
        updated = result.scalar_one()

    assert updated.status == ParcelStatus.ACTIVE

    async with AsyncSessionLocal() as session:
        area_a = await compute_area_a_sqm(session)
    assert area_a == 200.0, "once switched back to ACTIVE, the parcel must count toward Area A again"


async def test_communal_parcel_status_option_appears_in_parcel_form(client, admin_user):
    await client.post("/auth/login", data={"email": "admin@example.com", "password": "testpasswort123"})

    async with AsyncSessionLocal() as session:
        parcel = Parcel(plot_number="COMMUNAL-FORM", area_sqm=100, status=ParcelStatus.ACTIVE)
        session.add(parcel)
        await session.commit()
        parcel_id = parcel.id

    response = await client.get(f"/parcels/{parcel_id}/edit")
    assert response.status_code == 200
    assert 'value="COMMUNAL"' in response.text
