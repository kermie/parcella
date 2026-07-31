"""
Issue #170: a freshly-created applicant with no member_since set at
all was labeled "active" in /members/'s status badge, since
Member.is_active correctly (per issue #167) still counts a blank
member_since as active for billing/invitation purposes -- but showing
that raw value as the badge text is misleading for a record with no
confirmed start date.

Display-only fix, confirmed with the reporter: does NOT change
is_active/active_member_filter() themselves (invoices, meeting
sign-in, dashboard counts stay exactly as decided for #167), and no
new field/toggle -- member_since remains the only input. Only the
member list's status column now shows a distinct "pending" badge for
this one case (blank member_since AND is_active still True).
"""
from datetime import date, timedelta

from app.database import AsyncSessionLocal
from app.models import Member


async def test_blank_member_since_shows_pending_badge_not_active(client, admin_user):
    await client.post("/auth/login", data={"email": "admin@example.com", "password": "testpasswort123"})

    async with AsyncSessionLocal() as session:
        member = Member(first_name="Fresh", last_name="Applicant", member_since=None, member_until=None)
        session.add(member)
        await session.commit()
        member_id = member.id

    response = await client.get("/members/")
    assert response.status_code == 200

    assert member.is_active is True, "is_active itself must be unaffected -- still active for billing/invitations"

    # Isolate the row for this member to avoid matching another row's badge.
    row_start = response.text.index(f"/members/{member_id}")
    row_end = response.text.index("</tr>", row_start)
    row_html = response.text[row_start:row_end]
    assert "badge-ausstehend" in row_html
    assert "badge-aktiv" not in row_html


async def test_member_since_in_past_still_shows_active_badge(client, admin_user):
    """A confirmed start date (even one just set to today) must still
    show the normal "active" badge -- this fix only targets the blank
    case, not every active member."""
    await client.post("/auth/login", data={"email": "admin@example.com", "password": "testpasswort123"})

    async with AsyncSessionLocal() as session:
        member = Member(first_name="Confirmed", last_name="Member", member_since=date.today() - timedelta(days=1))
        session.add(member)
        await session.commit()
        member_id = member.id

    response = await client.get("/members/")
    assert response.status_code == 200

    row_start = response.text.index(f"/members/{member_id}")
    row_end = response.text.index("</tr>", row_start)
    row_html = response.text[row_start:row_end]
    assert "badge-aktiv" in row_html
    assert "badge-ausstehend" not in row_html


async def test_expired_member_with_blank_member_since_still_shows_inactive_badge(client, admin_user):
    """member_until in the past already makes is_active False regardless
    of member_since -- must still show the normal "inactive" badge, not
    the new pending one (pending is only for the is_active-still-True
    blank case)."""
    await client.post("/auth/login", data={"email": "admin@example.com", "password": "testpasswort123"})

    async with AsyncSessionLocal() as session:
        member = Member(
            first_name="Formerly", last_name="Member",
            member_since=None, member_until=date.today() - timedelta(days=1),
        )
        session.add(member)
        await session.commit()
        member_id = member.id

    response = await client.get("/members/?include_inactive=true")
    assert response.status_code == 200

    row_start = response.text.index(f"/members/{member_id}")
    row_end = response.text.index("</tr>", row_start)
    row_html = response.text[row_start:row_end]
    assert "badge-gekuendigt" in row_html
    assert "badge-ausstehend" not in row_html
