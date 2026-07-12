"""
Tests für das Purchase-Requests-Modul (Einkaufswünsche). Schwerpunkt: das
Vier-Augen-Prinzip selbst – genau die Kontrolle, die dieses Modul überhaupt
existieren lässt. Ein Regressionsfehler hier wäre besonders schwerwiegend
(Sicherheitslücke, kein reiner Komfortbug).
"""
from tests.conftest import login, auth_header


async def test_zwei_unterschiedliche_freigaben_fuehren_zu_genehmigt(
    client, admin_benutzer, vorstand_benutzer, zweiter_vorstand_benutzer
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
    assert r1.json()["status"] == "OPEN"  # erst 1 von 2

    token_v2 = await login(client, "vorstand2@example.com")
    r2 = await client.post(
        f"/api/v1/purchase-requests/{pr['id']}/approve", headers=auth_header(token_v2)
    )
    assert r2.status_code == 200
    assert r2.json()["status"] == "APPROVED"  # jetzt 2 von 2


async def test_antragsteller_darf_nicht_selbst_freigeben(client, admin_benutzer, vorstand_benutzer):
    """Kernschutz des Vier-Augen-Prinzips: wer beantragt, darf nicht mitgenehmigen."""
    token = await login(client, "vorstand@example.com")
    headers = auth_header(token)

    pr = (await client.post(
        "/api/v1/purchase-requests",
        json={"title": "Selbst beantragt", "justification": "Test"},
        headers=headers,
    )).json()

    # Der Antragsteller selbst versucht freizugeben – muss abgelehnt werden
    response = await client.post(f"/api/v1/purchase-requests/{pr['id']}/approve", headers=headers)
    assert response.status_code == 403


async def test_gleiche_person_kann_nicht_doppelt_freigeben(
    client, admin_benutzer, vorstand_benutzer, zweiter_vorstand_benutzer
):
    """Zwei Freigaben müssen von ZWEI UNTERSCHIEDLICHEN Personen kommen."""
    token = await login(client, "admin@example.com")
    headers = auth_header(token)

    pr = (await client.post(
        "/api/v1/purchase-requests",
        json={"title": "Test", "justification": "Test"},
        headers=headers,
    )).json()

    token_v1 = await login(client, "vorstand@example.com")
    await client.post(f"/api/v1/purchase-requests/{pr['id']}/approve", headers=auth_header(token_v1))

    # Dieselbe Person versucht ein zweites Mal freizugeben
    zweiter_versuch = await client.post(
        f"/api/v1/purchase-requests/{pr['id']}/approve", headers=auth_header(token_v1)
    )
    assert zweiter_versuch.status_code == 409

    # Status muss weiterhin OPEN sein, nicht fälschlich APPROVED
    aktuell = (await client.get(f"/api/v1/purchase-requests/{pr['id']}", headers=headers)).json()
    assert aktuell["status"] == "OPEN"


async def test_ablehnung_durch_eine_person_genuegt(client, admin_benutzer, vorstand_benutzer):
    """Veto-Prinzip: eine einzelne Ablehnung stoppt den Antrag sofort."""
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


async def test_normale_mitglieder_koennen_nicht_freigeben(client, admin_benutzer):
    """Nur Vorstand/Admin dürfen freigeben – einfache Mitglieder nicht."""
    from app.models import Benutzer, BenutzerRolle
    from app.auth import hash_passwort
    from app.database import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        einfaches_mitglied = Benutzer(
            email="mitglied@example.com", name="Normales Member",
            passwort_hash=hash_passwort("testpasswort123"), rolle=BenutzerRolle.LESEND,
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
