"""
Tests für das Work-Hours-Modul (Pflichtstunden). Schwerpunkt auf der
Geschäftslogik mit höherem Regressionsrisiko: Gruppen-Befreiung bei
PER_PARCEL (any() statt all() – siehe Architektur-Entscheidungen) und
die Jahresauswertung.
"""
from tests.conftest import login, auth_header


async def _erstelle_configuration(client, headers, year=2026, mode="PER_PARCEL"):
    return await client.put(
        f"/api/v1/work-hours/configuration/{year}",
        json={"year": year, "hours_required": "5.0", "rate_per_hour_eur": "25.00", "mode": mode},
        headers=headers,
    )


async def test_configuration_upsert(client, admin_benutzer):
    token = await login(client, "admin@example.com")
    headers = auth_header(token)

    response = await _erstelle_configuration(client, headers)
    assert response.status_code == 200
    assert response.json()["hours_required"] == "5.00" or float(response.json()["hours_required"]) == 5.0


async def test_session_und_participation(client, admin_benutzer):
    token = await login(client, "admin@example.com")
    headers = auth_header(token)

    member = (await client.post(
        "/api/v1/members", json={"first_name": "Klaus", "last_name": "Fleissig"}, headers=headers
    )).json()

    session = (await client.post(
        "/api/v1/work-hours/sessions",
        json={"title": "Frühjahrsputz", "type": "STANDARD", "date": "2026-04-01"},
        headers=headers,
    )).json()

    participation = await client.post(
        f"/api/v1/work-hours/sessions/{session['id']}/participations",
        json={"member_id": member["id"], "status": "ATTENDED", "hours_completed": "3.0"},
        headers=headers,
    )
    assert participation.status_code == 201


async def test_befreiung_gilt_fuer_ganze_parcel_bei_per_parcel(client, admin_benutzer):
    """
    Wichtigster Regressionstest für die 'any() statt all()'-Entscheidung:
    Ist EIN Pächter einer Parcel als Vorstand befreit, muss die GANZE
    Parcel als befreit gelten – auch der andere (nicht befreite) Pächter.
    """
    token = await login(client, "admin@example.com")
    headers = auth_header(token)

    await _erstelle_configuration(client, headers, year=2026, mode="PER_PARCEL")

    befreiter = (await client.post(
        "/api/v1/members", json={"first_name": "Christian", "last_name": "Vorstand"}, headers=headers
    )).json()
    mitpaechter = (await client.post(
        "/api/v1/members", json={"first_name": "Alexandra", "last_name": "Mitpaechter"}, headers=headers
    )).json()
    parcel = (await client.post(
        "/api/v1/parcels", json={"plot_number": "G100"}, headers=headers
    )).json()

    await client.post(
        f"/api/v1/parcels/{parcel['id']}/assignments",
        json={"member_id": befreiter["id"], "parcel_id": parcel["id"], "is_primary_tenant": True},
        headers=headers,
    )
    await client.post(
        f"/api/v1/parcels/{parcel['id']}/assignments",
        json={"member_id": mitpaechter["id"], "parcel_id": parcel["id"], "is_primary_tenant": False},
        headers=headers,
    )

    role = (await client.post(
        "/api/v1/work-hours/club-roles",
        json={"name": "Vorstandsvorsitzender", "hours_exempt": True, "exemption_reason": "BOARD"},
        headers=headers,
    )).json()

    await client.post(
        "/api/v1/work-hours/club-roles/assignments",
        json={"member_id": befreiter["id"], "club_role_id": role["id"], "year": 2026},
        headers=headers,
    )

    evaluation = (await client.get("/api/v1/work-hours/evaluation/2026", headers=headers)).json()
    row = next(z for z in evaluation if z["label"] == "G100")

    assert row["exempt"] is True
    assert float(row["hours_open"]) == 0.0
    assert float(row["amount_due_eur"]) == 0.0
