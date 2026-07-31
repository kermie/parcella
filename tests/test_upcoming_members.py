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

Follow-up (same issue): Member.is_active -- a second, Python-side
mirror of active_member_filter() used by the member list's status
badge and the REST API's active_only filter -- was missed in the
original fix and still showed a pending application as active. Also
adds a dedicated /members/?pending_only=true view, sorted by
created_at (the "entered into the system" timestamp), for reviewing a
queue of pending applications.
"""
from datetime import date, timedelta

from sqlalchemy import select

from tests.conftest import login, auth_header
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


async def test_member_is_active_property_reflects_member_since():
    async with AsyncSessionLocal() as session:
        upcoming = Member(first_name="Upcoming", last_name="Applicant", member_since=date.today() + timedelta(days=30))
        started = Member(first_name="Already", last_name="Started", member_since=date.today() - timedelta(days=1))
        session.add_all([upcoming, started])
        await session.commit()

    assert upcoming.is_active is False, "Member.is_active must mirror active_member_filter()'s member_since check"
    assert started.is_active is True


async def test_pending_only_view_shows_upcoming_members_sorted_by_created_at(client, admin_user):
    await client.post("/auth/login", data={"email": "admin@example.com", "password": "testpasswort123"})

    async with AsyncSessionLocal() as session:
        active_member = Member(first_name="Already", last_name="Active", member_since=date.today() - timedelta(days=1))
        second_applicant = Member(first_name="Second", last_name="Applicant", member_since=date.today() + timedelta(days=10))
        session.add_all([active_member, second_applicant])
        await session.commit()

    # Added in a separate commit, after the first applicant, so
    # created_at ordering is unambiguous even at second resolution.
    async with AsyncSessionLocal() as session:
        first_applicant = Member(first_name="First", last_name="Applicant", member_since=date.today() + timedelta(days=5))
        session.add(first_applicant)
        await session.commit()

    response = await client.get("/members/?pending_only=true")
    assert response.status_code == 200
    text = response.text

    assert "Active, Already" not in text, "an already-active member must not appear in the pending-only view"
    assert "Applicant, Second" in text
    assert "Applicant, First" in text
    # "Second" was entered into the system before "First" (committed
    # first above), so it must be listed first (oldest-applied-first).
    assert text.index("Applicant, Second") < text.index("Applicant, First")


async def test_pending_only_view_includes_blank_member_since(client, admin_user):
    """A member entered with no confirmed start date at all (neither
    member_since nor member_until set) is just as much "not actually a
    member yet" as one with a future member_since -- reported live:
    Sophia Möbius had blank dates and didn't show up in the pending
    view before this fix. Deliberately does NOT change
    active_member_filter() itself -- she must stay "active" everywhere
    else (invoices, meetings, etc.), same as any other legacy member
    with no member_since ever set."""
    await client.post("/auth/login", data={"email": "admin@example.com", "password": "testpasswort123"})

    async with AsyncSessionLocal() as session:
        blank_dates_member = Member(first_name="Sophia", last_name="Möbius", member_since=None, member_until=None)
        session.add(blank_dates_member)
        await session.commit()

    response = await client.get("/members/?pending_only=true")
    assert response.status_code == 200
    assert "Möbius, Sophia" in response.text

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Member.last_name).where(active_member_filter()))
        last_names = {row[0] for row in result.all()}
    assert "Möbius" in last_names, "active_member_filter() itself must be unaffected -- still active everywhere else"


async def test_pending_only_view_empty_state(client, admin_user):
    await client.post("/auth/login", data={"email": "admin@example.com", "password": "testpasswort123"})

    response = await client.get("/members/?pending_only=true")
    assert response.status_code == 200


async def test_rest_api_active_only_excludes_upcoming_member(client, admin_user):
    token = await login(client, "admin@example.com")
    headers = auth_header(token)

    async with AsyncSessionLocal() as session:
        session.add(Member(first_name="Pending", last_name="ApiApplicant", member_since=date.today() + timedelta(days=30)))
        session.add(Member(first_name="Actual", last_name="ApiMember"))
        await session.commit()

    response = await client.get("/api/v1/members?active_only=true", headers=headers)
    assert response.status_code == 200
    names = {(m["first_name"], m["last_name"]) for m in response.json()}

    assert ("Pending", "ApiApplicant") not in names
    assert ("Actual", "ApiMember") in names
