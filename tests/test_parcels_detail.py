"""
Issue #130: "keep former leasers bound to their plot until due date for
termination" -- a MemberParcel assignment terminated with a future
assigned_until hasn't taken effect yet and must still render in the
current-tenants table on the parcel detail page (app/templates/parcels/
detail.html), not the former-tenants history table below it.
"""
from datetime import date, timedelta

from app.database import AsyncSessionLocal
from app.models import Member, Parcel, MemberParcel


async def web_login(client, email: str, password: str = "testpasswort123") -> None:
    response = await client.post("/auth/login", data={"email": email, "password": password})
    assert response.status_code in (302, 303)


async def test_detail_page_shows_future_dated_termination_as_current(client, admin_user):
    await web_login(client, "admin@example.com")

    async with AsyncSessionLocal() as session:
        member = Member(first_name="Notice", last_name="Given")
        parcel = Parcel(plot_number="G097")
        session.add_all([member, parcel])
        await session.flush()
        session.add(MemberParcel(
            member_id=member.id, parcel_id=parcel.id,
            assigned_until=date.today() + timedelta(days=30), is_invoice_address=False,
        ))
        await session.commit()
        parcel_id = parcel.id

    response = await client.get(f"/parcels/{parcel_id}")
    assert response.status_code == 200
    assert "Notice Given" in response.text
    # Still current -- must not appear in the former-tenants history section.
    assert "Former tenants" not in response.text


async def test_detail_page_shows_past_dated_termination_as_former(client, admin_user):
    await web_login(client, "admin@example.com")

    async with AsyncSessionLocal() as session:
        member = Member(first_name="Already", last_name="Moved-Out")
        parcel = Parcel(plot_number="G098")
        session.add_all([member, parcel])
        await session.flush()
        session.add(MemberParcel(
            member_id=member.id, parcel_id=parcel.id,
            assigned_until=date.today() - timedelta(days=1), is_invoice_address=False,
        ))
        await session.commit()
        parcel_id = parcel.id

    response = await client.get(f"/parcels/{parcel_id}")
    assert response.status_code == 200
    assert "Already Moved-Out" in response.text
    assert "Former tenants" in response.text
