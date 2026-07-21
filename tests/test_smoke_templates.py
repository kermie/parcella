from tests.conftest import login, auth_header


async def test_smoke_parcels_pages_render_without_jinja_errors(client, admin_user):
    token = await login(client, "admin@example.com")
    headers = auth_header(token)

    parcel = (await client.post("/api/v1/parcels", json={"plot_number": "ZZTEST1"}, headers=headers)).json()
    member = (await client.post("/api/v1/members", json={"first_name": "Smoke", "last_name": "Test"}, headers=headers)).json()

    response = await client.post("/auth/login", data={"email": "admin@example.com", "password": "testpasswort123"})
    assert response.status_code in (302, 303)

    r_list = await client.get("/parcels/", params={"search": "ZZTEST"})
    assert r_list.status_code == 200
    assert "ZZTEST1" in r_list.text
    assert "UndefinedError" not in r_list.text

    r_detail = await client.get(f"/parcels/{parcel['id']}")
    assert r_detail.status_code == 200
    assert "UndefinedError" not in r_detail.text

    r_assign = await client.post(
        f"/parcels/{parcel['id']}/member/assign",
        data={"member_id": member["id"], "assigned_from": ""},
    )
    assert r_assign.status_code in (302, 303)

    r_detail2 = await client.get(f"/parcels/{parcel['id']}")
    assert r_detail2.status_code == 200
    assert "Smoke Test" in r_detail2.text
    assert "UndefinedError" not in r_detail2.text

    assignment_id = None
    r_edit_page = None
    import re
    m = re.search(r'/parcels/[a-f0-9-]+/member/([a-f0-9-]+)/edit', r_detail2.text)
    assert m, "no edit link found in detail page"
    assignment_id = m.group(1)

    r_edit_page = await client.get(f"/parcels/{parcel['id']}/member/{assignment_id}/edit")
    assert r_edit_page.status_code == 200
    assert "UndefinedError" not in r_edit_page.text

    r_dup = await client.post("/parcels/new", data={"plot_number": "ZZTEST1"})
    assert r_dup.status_code == 400
    assert "UndefinedError" not in r_dup.text
    assert "already exists" in r_dup.text or "existiert" in r_dup.text


async def test_smoke_members_pages_render_without_jinja_errors(client, admin_user):
    token = await login(client, "admin@example.com")
    headers = auth_header(token)

    member = (await client.post(
        "/api/v1/members", json={"first_name": "Erika", "last_name": "ZZSearchTest"}, headers=headers
    )).json()
    parcel = (await client.post("/api/v1/parcels", json={"plot_number": "ZZM1"}, headers=headers)).json()
    await client.post(
        f"/api/v1/parcels/{parcel['id']}/assignments",
        json={"member_id": member["id"], "parcel_id": parcel["id"]},
        headers=headers,
    )

    response = await client.post("/auth/login", data={"email": "admin@example.com", "password": "testpasswort123"})
    assert response.status_code in (302, 303)

    r_list = await client.get("/members/", params={"search": "ZZSearchTest"})
    assert r_list.status_code == 200
    assert "ZZSearchTest" in r_list.text
    assert "UndefinedError" not in r_list.text

    r_list_inactive = await client.get("/members/", params={"search": "ZZSearchTest", "include_inactive": "true"})
    assert r_list_inactive.status_code == 200
    assert "UndefinedError" not in r_list_inactive.text

    r_no_results = await client.get("/members/", params={"search": "NoSuchMemberXYZ"})
    assert r_no_results.status_code == 200
    assert "NoSuchMemberXYZ" in r_no_results.text
    assert "UndefinedError" not in r_no_results.text

    r_detail = await client.get(f"/members/{member['id']}")
    assert r_detail.status_code == 200
    assert "ZZM1" in r_detail.text
    assert "UndefinedError" not in r_detail.text


async def test_smoke_dashboard_renders_without_jinja_errors(client, admin_user):
    token = await login(client, "admin@example.com")
    headers = auth_header(token)

    await client.post("/api/v1/members", json={"first_name": "Dash", "last_name": "Board"}, headers=headers)
    parcel = (await client.post("/api/v1/parcels", json={"plot_number": "ZZD1"}, headers=headers)).json()

    response = await client.post("/auth/login", data={"email": "admin@example.com", "password": "testpasswort123"})
    assert response.status_code in (302, 303)

    r = await client.get("/")
    assert r.status_code == 200
    assert "UndefinedError" not in r.text
    assert "Dash Board" in r.text


async def test_smoke_metering_pages_render_without_jinja_errors(client, admin_user):
    token = await login(client, "admin@example.com")
    headers = auth_header(token)

    parcel = (await client.post("/api/v1/parcels", json={"plot_number": "ZZW1"}, headers=headers)).json()

    response = await client.post("/auth/login", data={"email": "admin@example.com", "password": "testpasswort123"})
    assert response.status_code in (302, 303)

    r_overview = await client.get("/water/")
    assert r_overview.status_code == 200
    assert "UndefinedError" not in r_overview.text

    r_points_list = await client.get("/water/metering-points")
    assert r_points_list.status_code == 200
    assert "UndefinedError" not in r_points_list.text

    r_new_page = await client.get("/water/metering-points/new")
    assert r_new_page.status_code == 200
    assert "UndefinedError" not in r_new_page.text

    r_create = await client.post(
        "/water/metering-points/new",
        data={"type": "PARCEL", "parcel_id": parcel["id"], "number": "WSM-1", "initial_reading": "0"},
    )
    assert r_create.status_code in (302, 303)
    location = r_create.headers["location"]
    point_id = location.rstrip("/").rsplit("/", 1)[-1]

    r_detail = await client.get(f"/water/metering-points/{point_id}")
    assert r_detail.status_code == 200
    assert "UndefinedError" not in r_detail.text
    assert "WSM-1" in r_detail.text

    r_reading = await client.post(
        f"/water/metering-points/{point_id}/readings/new",
        data={"year": "2026", "date": "2026-10-01", "reading": "42.0"},
    )
    assert r_reading.status_code in (302, 303)

    r_detail2 = await client.get(f"/water/metering-points/{point_id}")
    assert r_detail2.status_code == 200
    assert "UndefinedError" not in r_detail2.text

    r_readings_list = await client.get("/water/readings", params={"year": "2026"})
    assert r_readings_list.status_code == 200
    assert "UndefinedError" not in r_readings_list.text
    assert "WSM-1" in r_readings_list.text

    r_evaluation = await client.get("/water/evaluation", params={"year": "2026"})
    assert r_evaluation.status_code == 200
    assert "UndefinedError" not in r_evaluation.text

    r_evaluation_csv = await client.get("/water/evaluation/csv", params={"year": "2026"})
    assert r_evaluation_csv.status_code == 200

    r_exchange = await client.post(
        f"/water/metering-points/{point_id}/meter/exchange",
        data={"neue_nummer": "WSM-2", "removed_at": "2026-10-02", "installed_at": "2026-10-02", "initial_reading": "0"},
    )
    assert r_exchange.status_code in (302, 303)

    r_detail3 = await client.get(f"/water/metering-points/{point_id}")
    assert r_detail3.status_code == 200
    assert "UndefinedError" not in r_detail3.text
    assert "WSM-2" in r_detail3.text
