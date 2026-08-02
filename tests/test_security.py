"""
Regression tests for the findings of the security review on branch
claude/security-issues-review-jsfhoh.

Every test here pins down a hole that was actually open in the code at
some point -- not a hypothetical. Grouped by finding, in the order they
were fixed:

1. Stored XSS via a member address (members/detail.html rendered
   `address_lines(...)|join('<br>')|safe`, which marks the raw member
   data as trusted -- see app/l10n.py's jinja_address_html).
2. /api/v1/stats was reachable with no authentication at all, unlike
   every other /api/v1 router.
3. Login brute-force protection (app/rate_limit.py).
4. The default SECRET_KEY must not boot outside development.
5. The bootstrap admin must change its documented default password
   before it can use the app.
6. Security response headers.
7. CSRF protection for cookie-authenticated form POSTs.
"""
import pytest
from httpx import AsyncClient

from app.database import AsyncSessionLocal
from app.models import Member
from tests.conftest import auth_header, login


async def web_login(client: AsyncClient, email: str, password: str = "testpasswort123") -> None:
    response = await client.post("/auth/login", data={"email": email, "password": password})
    assert response.status_code in (302, 303), response.text


# ---------------------------------------------------------------------------
# 1. Stored XSS via member address fields
# ---------------------------------------------------------------------------

XSS_PAYLOAD = '<img src=x onerror="alert(1)">'


async def test_member_address_is_escaped_on_detail_page(client, admin_user):
    """A member's address is free text entered by anyone with
    members_parcels write access (web UI or REST API). It used to be
    rendered through `|join('<br>')|safe`, which -- because Jinja's join
    returns a plain str for plain-str items and separators -- emitted it
    completely unescaped, so a colleague's address could run script in a
    full-access admin's session."""
    async with AsyncSessionLocal() as session:
        member = Member(
            first_name="Xss", last_name="Test",
            street=XSS_PAYLOAD, postal_code="12345", city="Town",
        )
        session.add(member)
        await session.commit()
        member_id = member.id

    await web_login(client, admin_user.email)
    response = await client.get(f"/members/{member_id}")
    assert response.status_code == 200

    assert XSS_PAYLOAD not in response.text, "member address must never be rendered as raw HTML"
    assert "&lt;img src=x onerror=" in response.text, "the address should still be shown, escaped"
    # The <br> between address lines is genuine markup and must survive
    # the escaping -- otherwise the fix would just break the layout.
    assert "12345 Town" in response.text
    assert "&lt;br&gt;" not in response.text


async def test_address_html_escapes_every_line():
    """Unit-level counterpart to the route test above, so the guarantee
    is pinned to the helper and not only to one template."""
    from app.l10n import jinja_address_html

    rendered = jinja_address_html({}, XSS_PAYLOAD, "12345", "<b>Town</b>")
    assert "<img" not in rendered
    assert "&lt;img" in rendered
    assert "&lt;b&gt;Town" in rendered
    assert "<br>" in rendered, "line separator must stay real markup"


# ---------------------------------------------------------------------------
# 2. /api/v1/stats authentication
# ---------------------------------------------------------------------------

async def test_api_stats_requires_authentication(client):
    """Shipped unauthenticated: member counts, parcel counts and total
    areas were readable by anyone who could reach the port."""
    response = await client.get("/api/v1/stats")
    assert response.status_code == 401


async def test_api_stats_works_with_token(client, admin_user):
    token = await login(client, admin_user.email)
    response = await client.get("/api/v1/stats", headers=auth_header(token))
    assert response.status_code == 200
    assert "members_total" in response.json()
