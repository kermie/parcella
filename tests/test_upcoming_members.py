"""
Issue #167: a member whose membership hasn't started yet (member_since
in the future -- a pending application) must not count as an actual
member anywhere active_member_filter() (app/database.py) is used --
the member list itself, invoices, meeting sign-in sheets, dashboard
counts, etc. Before this fix, active_member_filter() only checked
deleted_at/member_until, never member_since, so a pending applicant
was silently treated as fully active everywhere.

member_since IS NULL is unaffected (treated as "already started", same
as every member before this field existed).
"""
from datetime import date, timedelta

from sqlalchemy import select

from app.database import AsyncSessionLocal, active_member_filter
from app.models import Member, MemberParcel, Parcel, ClubSetting


async def _enable_finances_module():
    async with AsyncSessionLocal() as session:
        session.add(ClubSetting(key="modul_finances", value="true", description="test"))
        await session.commit()


async def _make_run(client, year="2026"):
    r_create = await client.post("/finances/runs", data={
        "year": year, "subject": "Upcoming member test", "issued_date": f"{year}-08-01",
        "due_date": f"{year}-09-01", "footer_text": "",
    })
    assert r_create.status_code in (302, 303)
    return r_create.headers["location"].rstrip("/").split("/")[-1]


async def test_active_member_filter_excludes_future_member_since():
    async with AsyncSessionLocal() as session:
        upcoming = Member(first_name="Upcoming", last_name="Applicant", member_since=date.today() + timedelta(days=30))
        started = Member(first_name="Already", last_name="Started", member_since=date.today() - timedelta(days=1))
        no_since = Member(first_name="NoSince", last_name="Legacy", member_since=None)
        session.add_all([upcoming, started, no_since])
        await session.commit()

        result = await session.execute(select(Member.last_name).where(active_member_filter()))
        last_names = {row[0] for row in result.all()}

    assert "Applicant" not in last_names, "a member whose membership hasn't started yet must not count as active"
    assert "Started" in last_names
    assert "Legacy" in last_names, "member_since IS NULL must be treated as already-started, not excluded"


async def test_members_list_excludes_upcoming_member_by_default(client, admin_user):
    await client.post("/auth/login", data={"email": "admin@example.com", "password": "testpasswort123"})

    async with AsyncSessionLocal() as session:
        session.add(Member(first_name="Pending", last_name="Applicant", member_since=date.today() + timedelta(days=30)))
        await session.commit()

    r_default = await client.get("/members/")
    assert r_default.status_code == 200
    assert "Applicant, Pending" not in r_default.text

    r_include_inactive = await client.get("/members/?include_inactive=true")
    assert r_include_inactive.status_code == 200
    assert "Applicant, Pending" in r_include_inactive.text, "the existing include_inactive escape hatch must still surface them"


async def test_signin_sheet_excludes_upcoming_member(client, admin_user):
    await client.post("/auth/login", data={"email": "admin@example.com", "password": "testpasswort123"})

    async with AsyncSessionLocal() as session:
        parcel = Parcel(plot_number="UPCOMING-1")
        upcoming_member = Member(
            first_name="Pending", last_name="Tenant", member_since=date.today() + timedelta(days=30),
        )
        session.add_all([parcel, upcoming_member])
        await session.flush()
        session.add(MemberParcel(member_id=upcoming_member.id, parcel_id=parcel.id, is_invoice_address=True))
        await session.commit()

    response = await client.post("/members/signin-sheet", data={"headline": "Test"})
    assert response.status_code == 200
    assert "Pending Tenant" not in response.text


async def test_fixed_per_person_invoice_excludes_upcoming_member(client, admin_user):
    await client.post("/auth/login", data={"email": "admin@example.com", "password": "testpasswort123"})
    await _enable_finances_module()

    async with AsyncSessionLocal() as session:
        upcoming_member = Member(
            first_name="Pending", last_name="Payer", member_since=date.today() + timedelta(days=30),
            street="Gartenweg 1", postal_code="12345", city="Testort",
        )
        started_member = Member(
            first_name="Actual", last_name="Payer",
            street="Gartenweg 2", postal_code="12345", city="Testort",
        )
        session.add_all([upcoming_member, started_member])
        await session.commit()

    run_id = await _make_run(client)
    r_item = await client.post(f"/finances/runs/{run_id}/items", data={
        "order_number": "10", "name": "Membership fee", "description": "",
        "pricing_mode": "fixed_per_person", "unit_price": "25.00",
        "applies_to_all_members": "on",
    })
    assert r_item.status_code in (302, 303)

    r_preview = await client.get(f"/finances/runs/{run_id}/preview")
    assert r_preview.status_code == 200
    assert "Pending Payer" not in r_preview.text
    assert "Actual Payer" in r_preview.text
