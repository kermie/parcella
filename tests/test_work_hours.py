"""
Tests for the Work Hours module. Focus on the business logic with
higher regression risk: group exemption under PER_PARCEL (any() instead
of all() -- see Architecture Decisions) and the annual evaluation.
"""
from tests.conftest import login, auth_header


async def _erstelle_configuration(client, headers, year=2026, mode="PER_PARCEL"):
    return await client.put(
        f"/api/v1/work-hours/configuration/{year}",
        json={"year": year, "hours_required": "5.0", "rate_per_hour_eur": "25.00", "mode": mode},
        headers=headers,
    )


async def test_configuration_upsert(client, admin_user):
    token = await login(client, "admin@example.com")
    headers = auth_header(token)

    response = await _erstelle_configuration(client, headers)
    assert response.status_code == 200
    assert response.json()["hours_required"] == "5.00" or float(response.json()["hours_required"]) == 5.0


async def test_session_und_participation(client, admin_user):
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


async def test_task_lifecycle(client, admin_user):
    """
    Covers the full task lifecycle: create in the backlog, schedule to a
    session, assign to one of that session's participants, and confirm
    that rescheduling to a different session clears the assignment (an
    assignment to a specific person only makes sense for the session
    they actually signed up for).
    """
    token = await login(client, "admin@example.com")
    headers = auth_header(token)

    member = (await client.post(
        "/api/v1/members", json={"first_name": "Elena", "last_name": "Elder"}, headers=headers
    )).json()

    session_a = (await client.post(
        "/api/v1/work-hours/sessions",
        json={"title": "Spring Cleanup", "type": "STANDARD", "date": "2026-04-01"},
        headers=headers,
    )).json()
    session_b = (await client.post(
        "/api/v1/work-hours/sessions",
        json={"title": "Summer Maintenance", "type": "STANDARD", "date": "2026-07-01"},
        headers=headers,
    )).json()

    participation = (await client.post(
        f"/api/v1/work-hours/sessions/{session_a['id']}/participations",
        json={"member_id": member["id"], "status": "REGISTERED"},
        headers=headers,
    )).json()

    # Create in the backlog (no session yet)
    task = (await client.post(
        "/api/v1/work-hours/tasks",
        json={"title": "Water the flower beds", "workload": "LIGHT"},
        headers=headers,
    )).json()
    assert task["session_id"] is None

    # Schedule to session A
    task = (await client.put(
        f"/api/v1/work-hours/tasks/{task['id']}",
        json={"session_id": session_a["id"]},
        headers=headers,
    )).json()
    assert task["session_id"] == session_a["id"]

    # Assign to the participant who signed up for session A
    task = (await client.put(
        f"/api/v1/work-hours/tasks/{task['id']}",
        json={"assigned_participation_id": participation["id"]},
        headers=headers,
    )).json()
    assert task["assigned_participation_id"] == participation["id"]

    # Assigning to a participant of a DIFFERENT session must be rejected
    response = await client.put(
        f"/api/v1/work-hours/tasks/{task['id']}",
        json={"session_id": session_b["id"], "assigned_participation_id": participation["id"]},
        headers=headers,
    )
    assert response.status_code == 400

    # Rescheduling to session B (without forcing the assignment) clears it
    task = (await client.put(
        f"/api/v1/work-hours/tasks/{task['id']}",
        json={"session_id": session_b["id"]},
        headers=headers,
    )).json()
    assert task["session_id"] == session_b["id"]
    assert task["assigned_participation_id"] is None

    delete_response = await client.delete(
        f"/api/v1/work-hours/tasks/{task['id']}", headers=headers
    )
    assert delete_response.status_code == 204


async def test_befreiung_gilt_fuer_ganze_parcel_bei_per_parcel(client, admin_user):
    """
    Most important regression test for the 'any() instead of all()'
    decision: if ONE tenant of a parcel is exempt as a board member, the
    WHOLE parcel must count as exempt -- including the other
    (non-exempt) tenant.
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
        json={"member_id": befreiter["id"], "parcel_id": parcel["id"]},
        headers=headers,
    )
    await client.post(
        f"/api/v1/parcels/{parcel['id']}/assignments",
        json={"member_id": mitpaechter["id"], "parcel_id": parcel["id"]},
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
