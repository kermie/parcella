"""
Tests for the ticket system. Limited to logic that's testable without
a real mail server -- actual IMAP fetch/SMTP send (app/ticket_mailer.py)
requires a real mail server and is deliberately NOT automated-tested
here (see docs/testing.md for the reasoning behind this boundary).
"""
from tests.conftest import login, auth_header


async def web_login(client, email: str = "admin@example.com", password: str = "testpasswort123") -> None:
    response = await client.post("/auth/login", data={"email": email, "password": password})
    assert response.status_code in (302, 303)


async def _create_tickets(client, headers, count: int) -> None:
    for i in range(count):
        response = await client.post(
            "/api/v1/tickets",
            json={"subject": f"Ticket {i}", "sender_email": f"sender{i}@example.com", "message": "Hallo"},
            headers=headers,
        )
        assert response.status_code == 201, response.text


async def test_ticket_create_and_automatic_member_matching(client, admin_user):
    token = await login(client, "admin@example.com")
    headers = auth_header(token)

    mitglied = (await client.post(
        "/api/v1/members", json={"first_name": "Petra", "last_name": "Beispiel"}, headers=headers
    )).json()
    await client.post(
        f"/api/v1/members/{mitglied['id']}/email-addresses",
        json={"address": "petra@example.com"},
        headers=headers,
    )

    ticket = (await client.post(
        "/api/v1/tickets",
        json={
            "subject": "Frage zur Parcel", "sender_email": "petra@example.com",
            "message": "Wo finde ich meine Wasseruhr?",
        },
        headers=headers,
    )).json()

    assert ticket["member_id"] == mitglied["id"]
    assert ticket["status"] == "ACTIVE"
    assert len(ticket["messages"]) == 1


async def test_ticket_zuweisung_aendert_status(client, admin_user):
    token = await login(client, "admin@example.com")
    headers = auth_header(token)

    ticket = (await client.post(
        "/api/v1/tickets",
        json={"subject": "Test", "sender_email": "unbekannt@example.com", "message": "Hallo"},
        headers=headers,
    )).json()

    zugewiesen = (await client.put(
        f"/api/v1/tickets/{ticket['id']}/assignment",
        json={"assigned_to_id": admin_user.id},
        headers=headers,
    )).json()
    assert zugewiesen["status"] == "ASSIGNED"
    assert zugewiesen["assigned_to_id"] == admin_user.id

    aufgehoben = (await client.put(
        f"/api/v1/tickets/{ticket['id']}/assignment",
        json={"assigned_to_id": None},
        headers=headers,
    )).json()
    assert aufgehoben["status"] == "ACTIVE"


async def test_ticket_status_zurueckgestellt_erfordert_datum(client, admin_user):
    token = await login(client, "admin@example.com")
    headers = auth_header(token)

    ticket = (await client.post(
        "/api/v1/tickets",
        json={"subject": "Test", "sender_email": "x@example.com", "message": "Hallo"},
        headers=headers,
    )).json()

    ohne_datum = await client.put(
        f"/api/v1/tickets/{ticket['id']}/status",
        json={"status": "POSTPONED"},
        headers=headers,
    )
    assert ohne_datum.status_code == 422

    mit_datum = await client.put(
        f"/api/v1/tickets/{ticket['id']}/status",
        json={"status": "POSTPONED", "postponed_until": "2030-01-01"},
        headers=headers,
    )
    assert mit_datum.status_code == 200


# ---------------------------------------------------------------------------
# Overview: infinite-scroll pagination
# ---------------------------------------------------------------------------

async def test_ticket_overview_first_page_is_capped(client, admin_user):
    """The list used to render every matching ticket in one unbounded
    HTML table -- now capped to TICKETS_PAGE_SIZE (50), with the rest
    fetched by the overview's infinite scroll from /tickets/list.json."""
    token = await login(client, "admin@example.com")
    headers = auth_header(token)
    await _create_tickets(client, headers, 55)

    await web_login(client)
    response = await client.get("/tickets/", params={"filter": "all"})
    assert response.status_code == 200
    # Only count rendered ticket rows, not the "bi-eye" substring that
    # also appears in the page's own inline <script> (row-templating JS
    # for infinite scroll) further down.
    rendered_rows = response.text.split("<script>")[0]
    assert rendered_rows.count("bi-eye") == 50
    assert "var hasMore = true;" in response.text


# ---------------------------------------------------------------------------
# Manual spam marking (the automated check only ever runs once, on
# arrival -- anything it misses needs a manual escape hatch)
# ---------------------------------------------------------------------------

async def test_web_ui_can_mark_and_unmark_a_ticket_as_spam(client, admin_user):
    from app.database import AsyncSessionLocal
    from app.models import Ticket
    from sqlalchemy import select

    token = await login(client, "admin@example.com")
    headers = auth_header(token)
    ticket = (await client.post(
        "/api/v1/tickets",
        json={"subject": "Totally normal", "sender_email": "someone@example.com", "message": "Hi"},
        headers=headers,
    )).json()

    await web_login(client)

    mark = await client.post(f"/tickets/{ticket['id']}/mark-spam")
    assert mark.status_code == 302

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Ticket).where(Ticket.id == ticket["id"]))
        marked = result.scalar_one()
        assert marked.spam_suspected is True
        # A human decision must be recorded -- the rescan routine relies
        # on this to never overwrite a staff call.
        assert marked.spam_reviewed_by_id == admin_user.id
        assert marked.spam_reviewed_at is not None

    filtered = await client.get("/tickets/", params={"filter": "spam"})
    assert "Totally normal" in filtered.text

    clear = await client.post(f"/tickets/{ticket['id']}/not-spam")
    assert clear.status_code == 302

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Ticket).where(Ticket.id == ticket["id"]))
        assert result.scalar_one().spam_suspected is False


async def test_bulk_mark_and_unmark_tickets_as_spam(client, admin_user):
    from app.database import AsyncSessionLocal
    from app.models import Ticket
    from sqlalchemy import select

    token = await login(client, "admin@example.com")
    headers = auth_header(token)
    t1 = (await client.post(
        "/api/v1/tickets",
        json={"subject": "First", "sender_email": "a@example.com", "message": "Hi"},
        headers=headers,
    )).json()
    t2 = (await client.post(
        "/api/v1/tickets",
        json={"subject": "Second", "sender_email": "b@example.com", "message": "Hi"},
        headers=headers,
    )).json()

    await web_login(client)

    mark = await client.post(
        "/tickets/bulk/mark-spam", data={"ticket_ids": [t1["id"], t2["id"]], "filter": "active"},
    )
    assert mark.status_code == 302

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Ticket).where(Ticket.id.in_([t1["id"], t2["id"]])))
        assert all(t.spam_suspected for t in result.scalars().all())

    clear = await client.post(
        "/tickets/bulk/not-spam", data={"ticket_ids": [t1["id"], t2["id"]], "filter": "spam"},
    )
    assert clear.status_code == 302

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Ticket).where(Ticket.id.in_([t1["id"], t2["id"]])))
        assert not any(t.spam_suspected for t in result.scalars().all())


# ---------------------------------------------------------------------------
# Backlog re-scan (POST /tickets/rescan-spam): the automated check only
# ever runs once, on arrival -- this is the catch-up pass for tickets
# that predate the filter being configured or a later config change.
# ---------------------------------------------------------------------------

async def test_rescan_spam_flags_matches_but_skips_reviewed_and_closed_tickets(client, admin_user):
    from app.database import AsyncSessionLocal
    from app.models import ClubSetting, Ticket, TicketStatus
    from sqlalchemy import select

    token = await login(client, "admin@example.com")
    headers = auth_header(token)

    async with AsyncSessionLocal() as db:
        db.add(ClubSetting(key="spam_keyword_blocklist", value="casino", description="test"))
        db.add(ClubSetting(key="spam_schwellenwert", value="0.1", description="test"))
        await db.commit()

    # A: never touched, still open -- should get flagged.
    never_reviewed = (await client.post(
        "/api/v1/tickets",
        json={"subject": "Casino night", "sender_email": "a@example.com", "message": "Come play"},
        headers=headers,
    )).json()

    # B: matches the same keyword, but staff already cleared it as a
    # false positive -- the rescan must leave that decision alone.
    already_reviewed = (await client.post(
        "/api/v1/tickets",
        json={"subject": "Casino night", "sender_email": "b@example.com", "message": "Come play"},
        headers=headers,
    )).json()
    await client.put(
        f"/api/v1/tickets/{already_reviewed['id']}/spam-status",
        json={"spam_suspected": False},
        headers=headers,
    )
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Ticket).where(Ticket.id == already_reviewed["id"]))
        reviewed = result.scalar_one()
        assert reviewed.spam_reviewed_by_id == admin_user.id
        assert reviewed.spam_reviewed_at is not None

    # C: matches the keyword too, but the ticket is closed -- out of scope.
    closed_ticket = (await client.post(
        "/api/v1/tickets",
        json={"subject": "Casino night", "sender_email": "c@example.com", "message": "Come play"},
        headers=headers,
    )).json()
    await client.put(
        f"/api/v1/tickets/{closed_ticket['id']}/status", json={"status": "CLOSED"}, headers=headers,
    )

    await web_login(client)
    response = await client.post("/tickets/rescan-spam")
    assert response.status_code == 302
    assert "message=" in response.headers["location"]

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Ticket).where(
                Ticket.id.in_([never_reviewed["id"], already_reviewed["id"], closed_ticket["id"]])
            )
        )
        by_id = {t.id: t for t in result.scalars().all()}

    assert by_id[never_reviewed["id"]].spam_suspected is True
    assert by_id[already_reviewed["id"]].spam_suspected is False
    assert by_id[closed_ticket["id"]].spam_suspected is False


async def test_tickets_list_json_returns_the_remaining_page(client, admin_user):
    token = await login(client, "admin@example.com")
    headers = auth_header(token)
    await _create_tickets(client, headers, 55)

    await web_login(client)
    response = await client.get("/tickets/list.json", params={"filter": "all", "offset": 50})
    assert response.status_code == 200
    data = response.json()
    assert len(data["rows"]) == 5
    assert data["has_more"] is False
