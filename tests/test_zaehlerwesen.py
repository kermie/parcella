"""
Tests für das Zählerwesen (Wasser & Strom). Schwerpunkt: die
Monotonie-Prüfung (Zählerstand darf nicht sinken) und die
Verbrauchsberechnung.
"""
from tests.conftest import login, auth_header


async def test_zaehlpunkt_anlegen_und_ablesung(client, admin_benutzer):
    token = await login(client, "admin@example.com")
    headers = auth_header(token)

    parzelle = (await client.post(
        "/api/v1/parcels", json={"plot_number": "G200"}, headers=headers
    )).json()

    zaehlpunkt = (await client.post(
        "/api/v1/wasser/zaehlpunkte",
        json={
            "typ": "PARZELLE", "parcel_id": parzelle["id"],
            "nummer": "W-12345", "anfangsstand": "0.0",
        },
        headers=headers,
    )).json()
    assert zaehlpunkt["aktueller_zaehler"]["nummer"] == "W-12345"

    ablesung = await client.post(
        f"/api/v1/wasser/zaehlpunkte/{zaehlpunkt['id']}/zaehlerstaende",
        json={"jahr": 2026, "datum": "2026-10-01", "stand": "12.5"},
        headers=headers,
    )
    assert ablesung.status_code == 201


async def test_zaehlerstand_darf_nicht_sinken(client, admin_benutzer):
    """
    Kernregel der Plausibilitätsprüfung: ein neuer Zählerstand muss
    mindestens so hoch sein wie der vorherige derselben Wasseruhr.
    """
    token = await login(client, "admin@example.com")
    headers = auth_header(token)

    zaehlpunkt = (await client.post(
        "/api/v1/wasser/zaehlpunkte",
        json={"typ": "VEREIN", "bezeichnung": "Vereinsheim", "nummer": "W-99999", "anfangsstand": "0.0"},
        headers=headers,
    )).json()

    r1 = await client.post(
        f"/api/v1/wasser/zaehlpunkte/{zaehlpunkt['id']}/zaehlerstaende",
        json={"jahr": 2025, "datum": "2025-10-01", "stand": "50.0"},
        headers=headers,
    )
    assert r1.status_code == 201

    # Ein niedrigerer Stand im Folgejahr muss abgelehnt werden
    r2 = await client.post(
        f"/api/v1/wasser/zaehlpunkte/{zaehlpunkt['id']}/zaehlerstaende",
        json={"jahr": 2026, "datum": "2026-10-01", "stand": "30.0"},
        headers=headers,
    )
    assert r2.status_code == 422

    # Ein höherer Stand ist völlig in Ordnung
    r3 = await client.post(
        f"/api/v1/wasser/zaehlpunkte/{zaehlpunkt['id']}/zaehlerstaende",
        json={"jahr": 2026, "datum": "2026-10-01", "stand": "75.0"},
        headers=headers,
    )
    assert r3.status_code == 201


async def test_verbrauchsberechnung(client, admin_benutzer):
    """Verbrauch = aktueller Stand minus letzter Stand (oder Anfangsstand)."""
    token = await login(client, "admin@example.com")
    headers = auth_header(token)

    zaehlpunkt = (await client.post(
        "/api/v1/wasser/zaehlpunkte",
        json={"typ": "HAUPTZAEHLER", "bezeichnung": "Hauptzähler", "nummer": "W-1", "anfangsstand": "100.0"},
        headers=headers,
    )).json()

    await client.post(
        f"/api/v1/wasser/zaehlpunkte/{zaehlpunkt['id']}/zaehlerstaende",
        json={"jahr": 2026, "datum": "2026-10-01", "stand": "150.0"},
        headers=headers,
    )

    auswertung = (await client.get("/api/v1/wasser/auswertung/2026", headers=headers)).json()
    zeile = next(z for z in auswertung if z["zaehlpunkt_id"] == zaehlpunkt["id"])
    assert float(zeile["verbrauch"]) == 50.0  # 150 - Anfangsstand 100


async def test_strom_und_wasser_getrennt(client, admin_benutzer):
    """Wasser- und Strom-Zaehlpunkte müssen unabhängige, getrennte Listen sein."""
    token = await login(client, "admin@example.com")
    headers = auth_header(token)

    await client.post(
        "/api/v1/wasser/zaehlpunkte",
        json={"typ": "VEREIN", "bezeichnung": "Nur Wasser", "nummer": "W-A", "anfangsstand": "0"},
        headers=headers,
    )
    await client.post(
        "/api/v1/strom/zaehlpunkte",
        json={"typ": "VEREIN", "bezeichnung": "Nur Strom", "nummer": "S-A", "anfangsstand": "0"},
        headers=headers,
    )

    wasser_liste = (await client.get("/api/v1/wasser/zaehlpunkte", headers=headers)).json()
    strom_liste = (await client.get("/api/v1/strom/zaehlpunkte", headers=headers)).json()

    assert len(wasser_liste) == 1
    assert len(strom_liste) == 1
    assert wasser_liste[0]["bezeichnung"] == "Nur Wasser"
    assert strom_liste[0]["bezeichnung"] == "Nur Strom"
