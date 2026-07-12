"""Tests für das Versicherungsmodul."""
from tests.conftest import login, auth_header


async def test_paket_anlegen_und_kosten_berechnung(client, admin_benutzer):
    token = await login(client, "admin@example.com")
    headers = auth_header(token)

    paket = (await client.post(
        "/api/v1/versicherungen/pakete",
        json={"jahr": 2026, "bezeichnung": "Paket 1", "betrag_eur": "40.00"},
        headers=headers,
    )).json()

    await client.put(
        "/api/v1/versicherungen/konfiguration/2026",
        json={"jahr": 2026, "unfall_grundbetrag_eur": "3.00", "unfall_zusatzbetrag_eur": "3.00"},
        headers=headers,
    )

    parzelle = (await client.post(
        "/api/v1/parcels", json={"plot_number": "G300"}, headers=headers
    )).json()

    status_response = await client.put(
        f"/api/v1/versicherungen/parcels/{parzelle['id']}/2026",
        json={
            "hat_sachversicherung": True, "sach_paket_id": paket["id"],
            "hat_unfallversicherung": True, "zusatzpersonen_mitglied_ids": [],
        },
        headers=headers,
    )
    assert status_response.status_code == 200
    daten = status_response.json()
    assert float(daten["sach_kosten_eur"]) == 40.0
    assert float(daten["unfall_kosten_eur"]) == 3.0
    assert float(daten["gesamt_kosten_eur"]) == 43.0


async def test_zusatzperson_erhoeht_unfallkosten(client, admin_benutzer):
    token = await login(client, "admin@example.com")
    headers = auth_header(token)

    await client.put(
        "/api/v1/versicherungen/konfiguration/2026",
        json={"jahr": 2026, "unfall_grundbetrag_eur": "3.00", "unfall_zusatzbetrag_eur": "3.00"},
        headers=headers,
    )

    parzelle = (await client.post("/api/v1/parcels", json={"plot_number": "G301"}, headers=headers)).json()
    zusatzperson = (await client.post(
        "/api/v1/members", json={"first_name": "Weiterer", "last_name": "Paechter"}, headers=headers
    )).json()

    daten = (await client.put(
        f"/api/v1/versicherungen/parcels/{parzelle['id']}/2026",
        json={
            "hat_sachversicherung": False, "hat_unfallversicherung": True,
            "zusatzpersonen_mitglied_ids": [zusatzperson["id"]],
        },
        headers=headers,
    )).json()

    assert float(daten["unfall_kosten_eur"]) == 6.0  # 3 Grund + 3 Zusatzperson
