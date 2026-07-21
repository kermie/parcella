"""
Tests for the ticket system. Limited to logic that's testable without
a real mail server -- actual IMAP fetch/SMTP send (app/ticket_mailer.py)
requires a real mail server and is deliberately NOT automated-tested
here (see docs/testing.md for the reasoning behind this boundary).
"""
from tests.conftest import login, auth_header


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
