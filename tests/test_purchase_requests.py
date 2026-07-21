"""
Tests for the Purchase Requests module. Focus: the four-eyes principle
itself -- exactly the control this module exists for in the first
place. A regression here would be especially serious (a security hole,
not a mere comfort bug).
"""
from tests.conftest import login, auth_header


async def test_zwei_unterschiedliche_freigaben_fuehren_zu_genehmigt(
    client, admin_user, board_user, second_board_user
):
    token = await login(client, "admin@example.com")
    headers = auth_header(token)

    pr = (await client.post(
        "/api/v1/purchase-requests",
        json={"title": "Neuer Rasenmäher", "justification": "Alter ist kaputt"},
        headers=headers,
    )).json()
    assert pr["status"] == "OPEN"

    token_v1 = await login(client, "vorstand@example.com")
    r1 = await client.post(
        f"/api/v1/purchase-requests/{pr['id']}/approve", headers=auth_header(token_v1)
    )
    assert r1.status_code == 200
    assert r1.json()["status"] == "OPEN"  # only 1 of 2 so far

    token_v2 = await login(client, "vorstand2@example.com")
    r2 = await client.post(
        f"/api/v1/purchase-requests/{pr['id']}/approve", headers=auth_header(token_v2)
    )
    assert r2.status_code == 200
    assert r2.json()["status"] == "APPROVED"  # now 2 of 2


async def test_antragsteller_darf_nicht_selbst_freigeben(client, admin_user, board_user):
    """Core protection of the four-eyes principle: whoever requests may not also approve."""
    token = await login(client, "vorstand@example.com")
    headers = auth_header(token)

    pr = (await client.post(
        "/api/v1/purchase-requests",
        json={"title": "Selbst beantragt", "justification": "Test"},
        headers=headers,
    )).json()

    # The requester themselves tries to approve -- must be rejected
    response = await client.post(f"/api/v1/purchase-requests/{pr['id']}/approve", headers=headers)
    assert response.status_code == 403


async def test_gleiche_person_kann_nicht_doppelt_freigeben(
    client, admin_user, board_user, second_board_user
):
    """Two approvals must come from TWO DIFFERENT people."""
    token = await login(client, "admin@example.com")
    headers = auth_header(token)

    pr = (await client.post(
        "/api/v1/purchase-requests",
        json={"title": "Test", "justification": "Test"},
        headers=headers,
    )).json()

    token_v1 = await login(client, "vorstand@example.com")
    await client.post(f"/api/v1/purchase-requests/{pr['id']}/approve", headers=auth_header(token_v1))

    # The same person tries to approve a second time
    zweiter_versuch = await client.post(
        f"/api/v1/purchase-requests/{pr['id']}/approve", headers=auth_header(token_v1)
    )
    assert zweiter_versuch.status_code == 409

    # Status must still be OPEN, not incorrectly APPROVED
    aktuell = (await client.get(f"/api/v1/purchase-requests/{pr['id']}", headers=headers)).json()
    assert aktuell["status"] == "OPEN"


async def test_ablehnung_durch_eine_person_genuegt(client, admin_user, board_user):
    """Veto principle: a single rejection stops the request immediately."""
    token = await login(client, "admin@example.com")
    headers = auth_header(token)

    pr = (await client.post(
        "/api/v1/purchase-requests",
        json={"title": "Fragwürdige Anschaffung", "justification": "Test"},
        headers=headers,
    )).json()

    token_v1 = await login(client, "vorstand@example.com")
    r = await client.post(
        f"/api/v1/purchase-requests/{pr['id']}/reject",
        json={"rejection_reason": "Nicht notwendig"},
        headers=auth_header(token_v1),
    )
    assert r.status_code == 200
    assert r.json()["status"] == "REJECTED"
    assert r.json()["rejection_reason"] == "Nicht notwendig"


async def test_normale_mitglieder_koennen_nicht_freigeben(client, admin_user):
    """Only board/admin may approve -- regular members may not."""
    from app.models import User, UserRole
    from app.auth import hash_password
    from app.database import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        einfaches_mitglied = User(
            email="mitglied@example.com", name="Normales Member",
            password_hash=hash_password("testpasswort123"), role=UserRole.READONLY,
        )
        session.add(einfaches_mitglied)
        await session.commit()

    token_admin = await login(client, "admin@example.com")
    pr = (await client.post(
        "/api/v1/purchase-requests",
        json={"title": "Test", "justification": "Test"},
        headers=auth_header(token_admin),
    )).json()

    token_mitglied = await login(client, "mitglied@example.com")
    response = await client.post(
        f"/api/v1/purchase-requests/{pr['id']}/approve", headers=auth_header(token_mitglied)
    )
    assert response.status_code == 403
