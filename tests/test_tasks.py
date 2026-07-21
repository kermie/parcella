"""
Tests for the task board module. Focus: the card-ordering algorithm
(app/task_board.py) -- cross-column moves, same-column reordering, and
that deleting a card doesn't leave a gap in `position` -- and the
admin/board-only permission boundary on both the web UI and the API.
"""
from tests.conftest import login, auth_header


async def _enable_module(client, headers):
    response = await client.put(
        "/api/v1/club-settings/modul_tasks", json={"value": "true"}, headers=headers,
    )
    assert response.status_code == 200, response.text


async def web_login(client, email: str, password: str = "testpasswort123") -> None:
    response = await client.post("/auth/login", data={"email": email, "password": password})
    assert response.status_code in (302, 303)


async def test_create_defaults_to_todo_column_at_end(client, admin_user):
    token = await login(client, "admin@example.com")
    headers = auth_header(token)
    await _enable_module(client, headers)

    first = (await client.post("/api/v1/tasks", json={"title": "First"}, headers=headers)).json()
    second = (await client.post("/api/v1/tasks", json={"title": "Second"}, headers=headers)).json()

    assert first["status"] == "TODO"
    assert first["position"] == 0
    assert second["status"] == "TODO"
    assert second["position"] == 1


async def test_move_to_different_column_appends_and_compacts_old_column(client, admin_user):
    token = await login(client, "admin@example.com")
    headers = auth_header(token)
    await _enable_module(client, headers)

    a = (await client.post("/api/v1/tasks", json={"title": "A"}, headers=headers)).json()
    b = (await client.post("/api/v1/tasks", json={"title": "B"}, headers=headers)).json()
    c = (await client.post("/api/v1/tasks", json={"title": "C"}, headers=headers)).json()

    moved = (await client.post(
        f"/api/v1/tasks/{a['id']}/move", json={"status": "IN_PROGRESS", "position": 0}, headers=headers,
    )).json()
    assert moved["status"] == "IN_PROGRESS"
    assert moved["position"] == 0

    todo = (await client.get("/api/v1/tasks", params={"status": "TODO"}, headers=headers)).json()
    todo_ids_in_order = [t["id"] for t in sorted(todo, key=lambda t: t["position"])]
    assert todo_ids_in_order == [b["id"], c["id"]]
    assert [t["position"] for t in sorted(todo, key=lambda t: t["position"])] == [0, 1]


async def test_reorder_within_same_column(client, admin_user):
    token = await login(client, "admin@example.com")
    headers = auth_header(token)
    await _enable_module(client, headers)

    a = (await client.post("/api/v1/tasks", json={"title": "A"}, headers=headers)).json()
    b = (await client.post("/api/v1/tasks", json={"title": "B"}, headers=headers)).json()
    c = (await client.post("/api/v1/tasks", json={"title": "C"}, headers=headers)).json()

    # Move C (currently position 2) to the front of TODO
    await client.post(f"/api/v1/tasks/{c['id']}/move", json={"status": "TODO", "position": 0}, headers=headers)

    todo = (await client.get("/api/v1/tasks", params={"status": "TODO"}, headers=headers)).json()
    ordered = [t["id"] for t in sorted(todo, key=lambda t: t["position"])]
    assert ordered == [c["id"], a["id"], b["id"]]


async def test_delete_closes_gap_in_remaining_column(client, admin_user):
    token = await login(client, "admin@example.com")
    headers = auth_header(token)
    await _enable_module(client, headers)

    a = (await client.post("/api/v1/tasks", json={"title": "A"}, headers=headers)).json()
    b = (await client.post("/api/v1/tasks", json={"title": "B"}, headers=headers)).json()
    c = (await client.post("/api/v1/tasks", json={"title": "C"}, headers=headers)).json()

    delete_response = await client.delete(f"/api/v1/tasks/{b['id']}", headers=headers)
    assert delete_response.status_code == 204

    todo = (await client.get("/api/v1/tasks", params={"status": "TODO"}, headers=headers)).json()
    ordered = sorted(todo, key=lambda t: t["position"])
    assert [t["id"] for t in ordered] == [a["id"], c["id"]]
    assert [t["position"] for t in ordered] == [0, 1]


async def test_update_task_fields(client, admin_user):
    token = await login(client, "admin@example.com")
    headers = auth_header(token)
    await _enable_module(client, headers)

    task = (await client.post("/api/v1/tasks", json={"title": "Original"}, headers=headers)).json()

    updated = (await client.put(
        f"/api/v1/tasks/{task['id']}",
        json={"title": "Renamed", "description": "Details", "due_date": "2026-12-01"},
        headers=headers,
    )).json()

    assert updated["title"] == "Renamed"
    assert updated["description"] == "Details"
    assert updated["due_date"] == "2026-12-01"


async def test_readonly_member_cannot_access_api(client, admin_user):
    from app.models import User, UserRole
    from app.auth import hash_password
    from app.database import AsyncSessionLocal

    token = await login(client, "admin@example.com")
    headers = auth_header(token)
    await _enable_module(client, headers)

    async with AsyncSessionLocal() as session:
        session.add(User(
            email="readonly@example.com", name="Readonly",
            password_hash=hash_password("testpasswort123"), role=UserRole.READONLY,
        ))
        await session.commit()

    readonly_token = await login(client, "readonly@example.com")
    response = await client.get("/api/v1/tasks", headers=auth_header(readonly_token))
    assert response.status_code == 403


async def test_readonly_member_gets_403_on_web_board(client, admin_user):
    from app.models import User, UserRole
    from app.auth import hash_password
    from app.database import AsyncSessionLocal

    token = await login(client, "admin@example.com")
    headers = auth_header(token)
    await _enable_module(client, headers)

    async with AsyncSessionLocal() as session:
        session.add(User(
            email="readonly2@example.com", name="Readonly Two",
            password_hash=hash_password("testpasswort123"), role=UserRole.READONLY,
        ))
        await session.commit()

    await web_login(client, "readonly2@example.com")
    response = await client.get("/tasks/")
    assert response.status_code == 403


async def test_web_board_renders_and_create_edit_delete_flow(client, admin_user):
    token = await login(client, "admin@example.com")
    headers = auth_header(token)
    await _enable_module(client, headers)

    await web_login(client, "admin@example.com")

    create_response = await client.post(
        "/tasks/new", data={"title": "Fix the gate lock", "description": "Squeaky hinge", "due_date": "2026-08-01"},
    )
    assert create_response.status_code in (302, 303)

    board_response = await client.get("/tasks/")
    assert board_response.status_code == 200
    assert "Fix the gate lock" in board_response.text
    assert "UndefinedError" not in board_response.text

    import re
    m = re.search(r'/tasks/([a-f0-9-]+)/edit', board_response.text)
    assert m, "no edit link found on board"
    task_id = m.group(1)

    edit_page = await client.get(f"/tasks/{task_id}/edit")
    assert edit_page.status_code == 200
    assert "Fix the gate lock" in edit_page.text

    edit_response = await client.post(
        f"/tasks/{task_id}/edit",
        data={"title": "Fix the gate lock (urgent)", "description": "Squeaky hinge", "due_date": ""},
    )
    assert edit_response.status_code in (302, 303)

    board_response2 = await client.get("/tasks/")
    assert "Fix the gate lock (urgent)" in board_response2.text

    move_response = await client.post(
        f"/tasks/{task_id}/move",
        json={"status": "DONE", "position": 0},
    )
    assert move_response.status_code == 200
    assert move_response.json()["ok"] is True

    delete_response = await client.post(f"/tasks/{task_id}/delete")
    assert delete_response.status_code in (302, 303)

    board_response3 = await client.get("/tasks/")
    assert "Fix the gate lock" not in board_response3.text


async def test_module_disabled_returns_404(client, admin_user):
    token = await login(client, "admin@example.com")
    headers = auth_header(token)
    response = await client.put(
        "/api/v1/club-settings/modul_tasks", json={"value": "false"}, headers=headers,
    )
    assert response.status_code == 200, response.text

    await web_login(client, "admin@example.com")

    response = await client.get("/tasks/")
    assert response.status_code == 404

    api_response = await client.get("/api/v1/tasks", headers=headers)
    assert api_response.status_code == 404
