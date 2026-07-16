"""Tests für Mitglieder, Parzellen und ihre m:n-Zuordnung."""
from tests.conftest import login, auth_header


async def test_mitglied_anlegen_und_abrufen(client, admin_user):
    token = await login(client, "admin@example.com")

    response = await client.post(
        "/api/v1/members",
        json={"first_name": "Erika", "last_name": "Musterfrau"},
        headers=auth_header(token),
    )
    assert response.status_code == 201
    mitglied = response.json()
    assert mitglied["first_name"] == "Erika"

    response = await client.get(f"/api/v1/members/{mitglied['id']}", headers=auth_header(token))
    assert response.status_code == 200
    assert response.json()["last_name"] == "Musterfrau"


async def test_parcel_anlegen_doppelte_plot_number_abgelehnt(client, admin_user):
    token = await login(client, "admin@example.com")

    response = await client.post(
        "/api/v1/parcels", json={"plot_number": "G001"}, headers=auth_header(token)
    )
    assert response.status_code == 201

    response = await client.post(
        "/api/v1/parcels", json={"plot_number": "g001"}, headers=auth_header(token)
    )
    assert response.status_code == 409  # Groß-/Kleinschreibung wird normalisiert (G001 == g001)


async def test_mitglied_parzelle_zuordnung_und_doppelgarten(client, admin_user):
    token = await login(client, "admin@example.com")
    headers = auth_header(token)

    m1 = (await client.post("/api/v1/members", json={"first_name": "Anna", "last_name": "Eins"}, headers=headers)).json()
    m2 = (await client.post("/api/v1/members", json={"first_name": "Bruno", "last_name": "Zwei"}, headers=headers)).json()
    p1 = (await client.post("/api/v1/parcels", json={"plot_number": "G010"}, headers=headers)).json()
    p2 = (await client.post("/api/v1/parcels", json={"plot_number": "G011"}, headers=headers)).json()

    # Doppelgarten: ein Member bekommt zwei Parzellen
    r1 = await client.post(
        f"/api/v1/parcels/{p1['id']}/assignments",
        json={"member_id": m1["id"], "parcel_id": p1["id"]},
        headers=headers,
    )
    assert r1.status_code == 201

    r2 = await client.post(
        f"/api/v1/parcels/{p2['id']}/assignments",
        json={"member_id": m1["id"], "parcel_id": p2["id"]},
        headers=headers,
    )
    assert r2.status_code == 201

    # Gemeinschaftsgarten: zweites Member auf derselben Parcel
    r3 = await client.post(
        f"/api/v1/parcels/{p1['id']}/assignments",
        json={"member_id": m2["id"], "parcel_id": p1["id"]},
        headers=headers,
    )
    assert r3.status_code == 201

    detail = (await client.get(f"/api/v1/parcels/{p1['id']}", headers=headers)).json()
    assert len(detail["members"]) == 2
