"""Tests for members, parcels, and their m:n assignment."""
from tests.conftest import login, auth_header


async def test_treasurer_without_group_grant_is_blocked_from_member_write_via_api(client):
    """ADR 0070: api_members.py used require_write_access (role-only) --
    ANY TREASURER could write members via the API regardless of Group
    configuration, even one a Group deliberately did not grant
    members_parcels:write to (correctly blocked in the HTML UI). Now
    the API checks the same Group-derived permission as HTML."""
    from app.database import AsyncSessionLocal
    from app.models import User, UserRole
    from app.auth import hash_password

    async with AsyncSessionLocal() as db:
        user = User(
            email="treasurer-no-group-members@example.com", name="Treasurer No Group",
            password_hash=hash_password("testpasswort123"), role=UserRole.TREASURER,
        )
        db.add(user)
        await db.commit()

    token = await login(client, "treasurer-no-group-members@example.com")
    response = await client.post(
        "/api/v1/members", json={"first_name": "Erika", "last_name": "Musterfrau"},
        headers=auth_header(token),
    )
    assert response.status_code == 403


async def test_readonly_with_group_grant_can_write_members_via_api(client):
    """Flip side of the bug above: a READONLY user granted
    members_parcels:write via a Group could already write members
    through the HTML UI, but the role-only API check blocked them
    regardless of Group membership. Pure bug fix: both surfaces now
    agree."""
    from app.database import AsyncSessionLocal
    from app.models import User, UserRole, Group, GroupModulePermission, GroupMembership
    from app.auth import hash_password

    async with AsyncSessionLocal() as db:
        user = User(
            email="readonly-with-group-members@example.com", name="Readonly With Group",
            password_hash=hash_password("testpasswort123"), role=UserRole.READONLY,
        )
        db.add(user)
        await db.flush()

        group = Group(name="Member Handlers")
        db.add(group)
        await db.flush()
        db.add(GroupModulePermission(group_id=group.id, module="members_parcels", can_read=True, can_write=True))
        db.add(GroupMembership(user_id=user.id, group_id=group.id))
        await db.commit()

    token = await login(client, "readonly-with-group-members@example.com")
    response = await client.post(
        "/api/v1/members", json={"first_name": "Erika", "last_name": "Musterfrau"},
        headers=auth_header(token),
    )
    assert response.status_code == 201, response.text


async def test_member_create_and_retrieve(client, admin_user):
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


async def test_parcel_create_duplicate_plot_number_rejected(client, admin_user):
    token = await login(client, "admin@example.com")

    response = await client.post(
        "/api/v1/parcels", json={"plot_number": "G001"}, headers=auth_header(token)
    )
    assert response.status_code == 201

    response = await client.post(
        "/api/v1/parcels", json={"plot_number": "g001"}, headers=auth_header(token)
    )
    assert response.status_code == 409  # case is normalized (G001 == g001)


async def test_member_parcel_assignment_and_double_garden(client, admin_user):
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
