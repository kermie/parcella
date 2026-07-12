"""
Tests für das Pflichtstunden-Modul. Schwerpunkt auf der Geschäftslogik mit
höherem Regressionsrisiko: Gruppen-Befreiung bei PRO_PACHTVERTRAG (any()
statt all() – siehe Architektur-Entscheidungen) und die Jahresauswertung.
"""
from tests.conftest import login, auth_header


async def _erstelle_konfiguration(client, headers, jahr=2026, modus="pro_pachtvertrag"):
    return await client.put(
        f"/api/v1/pflichtstunden/konfiguration/{jahr}",
        json={"jahr": jahr, "stunden_gesamt": "5.0", "stundensatz_eur": "25.00", "modus": modus},
        headers=headers,
    )


async def test_konfiguration_upsert(client, admin_benutzer):
    token = await login(client, "admin@example.com")
    headers = auth_header(token)

    response = await _erstelle_konfiguration(client, headers)
    assert response.status_code == 200
    assert response.json()["stunden_gesamt"] == "5.00" or float(response.json()["stunden_gesamt"]) == 5.0


async def test_arbeitseinsatz_und_teilnahme(client, admin_benutzer):
    token = await login(client, "admin@example.com")
    headers = auth_header(token)

    mitglied = (await client.post(
        "/api/v1/members", json={"first_name": "Klaus", "last_name": "Fleissig"}, headers=headers
    )).json()

    einsatz = (await client.post(
        "/api/v1/pflichtstunden/einsaetze",
        json={"titel": "Frühjahrsputz", "typ": "STANDARD", "datum": "2026-04-01"},
        headers=headers,
    )).json()

    teilnahme = await client.post(
        f"/api/v1/pflichtstunden/einsaetze/{einsatz['id']}/teilnahmen",
        json={"mitglied_id": mitglied["id"], "status": "ERSCHIENEN", "stunden_geleistet": "3.0"},
        headers=headers,
    )
    assert teilnahme.status_code == 201


async def test_befreiung_gilt_fuer_ganze_parzelle_bei_pro_pachtvertrag(client, admin_benutzer):
    """
    Wichtigster Regressionstest für die 'any() statt all()'-Entscheidung:
    Ist EIN Pächter einer Parcel als Vorstand befreit, muss die GANZE
    Parcel als befreit gelten – auch der andere (nicht befreite) Pächter.
    """
    token = await login(client, "admin@example.com")
    headers = auth_header(token)

    await _erstelle_konfiguration(client, headers, jahr=2026, modus="pro_pachtvertrag")

    befreiter = (await client.post(
        "/api/v1/members", json={"first_name": "Christian", "last_name": "Vorstand"}, headers=headers
    )).json()
    mitpaechter = (await client.post(
        "/api/v1/members", json={"first_name": "Alexandra", "last_name": "Mitpaechter"}, headers=headers
    )).json()
    parzelle = (await client.post(
        "/api/v1/parcels", json={"plot_number": "G100"}, headers=headers
    )).json()

    await client.post(
        f"/api/v1/parcels/{parzelle['id']}/assignments",
        json={"member_id": befreiter["id"], "parcel_id": parzelle["id"], "is_primary_tenant": True},
        headers=headers,
    )
    await client.post(
        f"/api/v1/parcels/{parzelle['id']}/assignments",
        json={"member_id": mitpaechter["id"], "parcel_id": parzelle["id"], "is_primary_tenant": False},
        headers=headers,
    )

    rolle = (await client.post(
        "/api/v1/pflichtstunden/vereinsrollen",
        json={"name": "Vorstandsvorsitzender", "pflichtstunden_befreit": True, "befreiungsgrund": "VORSTAND"},
        headers=headers,
    )).json()

    await client.post(
        "/api/v1/pflichtstunden/vereinsrollen/zuordnungen",
        json={"mitglied_id": befreiter["id"], "vereinsrolle_id": rolle["id"], "jahr": 2026},
        headers=headers,
    )

    auswertung = (await client.get("/api/v1/pflichtstunden/auswertung/2026", headers=headers)).json()
    zeile = next(z for z in auswertung if z["bezeichnung"] == "G100")

    assert zeile["befreit"] is True
    assert float(zeile["offen_stunden"]) == 0.0
    assert float(zeile["schuldbetrag_eur"]) == 0.0
