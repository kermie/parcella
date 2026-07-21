"""Tests for the insurance module."""
from tests.conftest import login, auth_header


async def test_package_create_and_cost_calculation(client, admin_user):
    token = await login(client, "admin@example.com")
    headers = auth_header(token)

    package = (await client.post(
        "/api/v1/insurance/packages",
        json={"year": 2026, "name": "Paket 1", "amount_eur": "40.00"},
        headers=headers,
    )).json()

    await client.put(
        "/api/v1/insurance/configuration/2026",
        json={"year": 2026, "accident_base_amount_eur": "3.00", "accident_additional_amount_eur": "3.00"},
        headers=headers,
    )

    parcel = (await client.post(
        "/api/v1/parcels", json={"plot_number": "G300"}, headers=headers
    )).json()

    status_response = await client.put(
        f"/api/v1/insurance/parcels/{parcel['id']}/2026",
        json={
            "has_property_insurance": True, "property_package_id": package["id"],
            "has_accident_insurance": True, "additional_person_member_ids": [],
        },
        headers=headers,
    )
    assert status_response.status_code == 200
    daten = status_response.json()
    assert float(daten["property_cost_eur"]) == 40.0
    assert float(daten["accident_cost_eur"]) == 3.0
    assert float(daten["total_cost_eur"]) == 43.0


async def test_additional_person_increases_accident_cost(client, admin_user):
    token = await login(client, "admin@example.com")
    headers = auth_header(token)

    await client.put(
        "/api/v1/insurance/configuration/2026",
        json={"year": 2026, "accident_base_amount_eur": "3.00", "accident_additional_amount_eur": "3.00"},
        headers=headers,
    )

    parcel = (await client.post("/api/v1/parcels", json={"plot_number": "G301"}, headers=headers)).json()
    additional_person = (await client.post(
        "/api/v1/members", json={"first_name": "Weiterer", "last_name": "Paechter"}, headers=headers
    )).json()

    daten = (await client.put(
        f"/api/v1/insurance/parcels/{parcel['id']}/2026",
        json={
            "has_property_insurance": False, "has_accident_insurance": True,
            "additional_person_member_ids": [additional_person["id"]],
        },
        headers=headers,
    )).json()

    assert float(daten["accident_cost_eur"]) == 6.0  # 3 Grund + 3 Zusatzperson
