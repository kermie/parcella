"""
Tests für das Einkaufswünsche-Modul. Schwerpunkt: das Vier-Augen-Prinzip
selbst – genau die Kontrolle, die dieses Modul überhaupt existieren lässt.
Ein Regressionsfehler hier wäre besonders schwerwiegend (Sicherheitslücke,
kein reiner Komfortbug).
"""
from tests.conftest import login, auth_header


async def test_zwei_unterschiedliche_freigaben_fuehren_zu_genehmigt(
    client, admin_benutzer, vorstand_benutzer, zweiter_vorstand_benutzer
):
    token = await login(client, "admin@example.com")
    headers = auth_header(token)

    ew = (await client.post(
        "/api/v1/einkaufswuensche",
        json={"titel": "Neuer Rasenmäher", "begruendung": "Alter ist kaputt"},
        headers=headers,
    )).json()
    assert ew["status"] == "OFFEN"

    token_v1 = await login(client, "vorstand@example.com")
    r1 = await client.post(
        f"/api/v1/einkaufswuensche/{ew['id']}/freigeben", headers=auth_header(token_v1)
    )
    assert r1.status_code == 200
    assert r1.json()["status"] == "OFFEN"  # erst 1 von 2

    token_v2 = await login(client, "vorstand2@example.com")
    r2 = await client.post(
        f"/api/v1/einkaufswuensche/{ew['id']}/freigeben", headers=auth_header(token_v2)
    )
    assert r2.status_code == 200
    assert r2.json()["status"] == "GENEHMIGT"  # jetzt 2 von 2


async def test_antragsteller_darf_nicht_selbst_freigeben(client, admin_benutzer, vorstand_benutzer):
    """Kernschutz des Vier-Augen-Prinzips: wer beantragt, darf nicht mitgenehmigen."""
    token = await login(client, "vorstand@example.com")
    headers = auth_header(token)

    ew = (await client.post(
        "/api/v1/einkaufswuensche",
        json={"titel": "Selbst beantragt", "begruendung": "Test"},
        headers=headers,
    )).json()

    # Der Antragsteller selbst versucht freizugeben – muss abgelehnt werden
    response = await client.post(f"/api/v1/einkaufswuensche/{ew['id']}/freigeben", headers=headers)
    assert response.status_code == 403


async def test_gleiche_person_kann_nicht_doppelt_freigeben(
    client, admin_benutzer, vorstand_benutzer, zweiter_vorstand_benutzer
):
    """Zwei Freigaben müssen von ZWEI UNTERSCHIEDLICHEN Personen kommen."""
    token = await login(client, "admin@example.com")
    headers = auth_header(token)

    ew = (await client.post(
        "/api/v1/einkaufswuensche",
        json={"titel": "Test", "begruendung": "Test"},
        headers=headers,
    )).json()

    token_v1 = await login(client, "vorstand@example.com")
    await client.post(f"/api/v1/einkaufswuensche/{ew['id']}/freigeben", headers=auth_header(token_v1))

    # Dieselbe Person versucht ein zweites Mal freizugeben
    zweiter_versuch = await client.post(
        f"/api/v1/einkaufswuensche/{ew['id']}/freigeben", headers=auth_header(token_v1)
    )
    assert zweiter_versuch.status_code == 409

    # Status muss weiterhin OFFEN sein, nicht fälschlich GENEHMIGT
    aktuell = (await client.get(f"/api/v1/einkaufswuensche/{ew['id']}", headers=headers)).json()
    assert aktuell["status"] == "OFFEN"


async def test_ablehnung_durch_eine_person_genuegt(client, admin_benutzer, vorstand_benutzer):
    """Veto-Prinzip: eine einzelne Ablehnung stoppt den Antrag sofort."""
    token = await login(client, "admin@example.com")
    headers = auth_header(token)

    ew = (await client.post(
        "/api/v1/einkaufswuensche",
        json={"titel": "Fragwürdige Anschaffung", "begruendung": "Test"},
        headers=headers,
    )).json()

    token_v1 = await login(client, "vorstand@example.com")
    r = await client.post(
        f"/api/v1/einkaufswuensche/{ew['id']}/ablehnen",
        json={"ablehnungsgrund": "Nicht notwendig"},
        headers=auth_header(token_v1),
    )
    assert r.status_code == 200
    assert r.json()["status"] == "ABGELEHNT"
    assert r.json()["ablehnungsgrund"] == "Nicht notwendig"


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
    ew = (await client.post(
        "/api/v1/einkaufswuensche",
        json={"titel": "Test", "begruendung": "Test"},
        headers=auth_header(token_admin),
    )).json()

    token_mitglied = await login(client, "mitglied@example.com")
    response = await client.post(
        f"/api/v1/einkaufswuensche/{ew['id']}/freigeben", headers=auth_header(token_mitglied)
    )
    assert response.status_code == 403
