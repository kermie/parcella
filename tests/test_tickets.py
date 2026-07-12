"""
Tests für das Ticketsystem. Beschränkt auf Logik, die ohne echten
Mailserver testbar ist – der tatsächliche IMAP-Abruf/SMTP-Versand
(app/ticket_mailer.py) erfordert einen echten Mailserver und wird hier
bewusst NICHT automatisiert getestet (siehe docs/testing.md für die
Begründung dieser Grenze).
"""
from tests.conftest import login, auth_header


async def test_ticket_anlegen_und_automatischer_mitglied_abgleich(client, admin_benutzer):
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
            "betreff": "Frage zur Parcel", "absender_email": "petra@example.com",
            "nachricht": "Wo finde ich meine Wasseruhr?",
        },
        headers=headers,
    )).json()

    assert ticket["mitglied_id"] == mitglied["id"]
    assert ticket["status"] == "NICHT_ZUGEWIESEN"
    assert len(ticket["nachrichten"]) == 1


async def test_ticket_zuweisung_aendert_status(client, admin_benutzer):
    token = await login(client, "admin@example.com")
    headers = auth_header(token)

    ticket = (await client.post(
        "/api/v1/tickets",
        json={"betreff": "Test", "absender_email": "unbekannt@example.com", "nachricht": "Hallo"},
        headers=headers,
    )).json()

    zugewiesen = (await client.put(
        f"/api/v1/tickets/{ticket['id']}/zuweisung",
        json={"benutzer_id": admin_benutzer.id},
        headers=headers,
    )).json()
    assert zugewiesen["status"] == "ZUGEWIESEN"
    assert zugewiesen["zugewiesen_an_id"] == admin_benutzer.id

    aufgehoben = (await client.put(
        f"/api/v1/tickets/{ticket['id']}/zuweisung",
        json={"benutzer_id": None},
        headers=headers,
    )).json()
    assert aufgehoben["status"] == "NICHT_ZUGEWIESEN"


async def test_ticket_status_zurueckgestellt_erfordert_datum(client, admin_benutzer):
    token = await login(client, "admin@example.com")
    headers = auth_header(token)

    ticket = (await client.post(
        "/api/v1/tickets",
        json={"betreff": "Test", "absender_email": "x@example.com", "nachricht": "Hallo"},
        headers=headers,
    )).json()

    ohne_datum = await client.put(
        f"/api/v1/tickets/{ticket['id']}/status",
        json={"status": "ZURUECKGESTELLT"},
        headers=headers,
    )
    assert ohne_datum.status_code == 422

    mit_datum = await client.put(
        f"/api/v1/tickets/{ticket['id']}/status",
        json={"status": "ZURUECKGESTELLT", "zurueckgestellt_bis": "2030-01-01"},
        headers=headers,
    )
    assert mit_datum.status_code == 200
