# Gartenverein Verwaltung

[![Lizenz: AGPL v3](https://img.shields.io/badge/Lizenz-AGPL%20v3-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.12-blue)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-green)](https://fastapi.tiangolo.com)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-blue)](https://postgresql.org)

Eine Open-Source Webanwendung zur Verwaltung von Kleingärtnervereinen: Mitglieder, Parzellen, Pachtverwaltung und Pflichtstunden.

Entstanden als Vibe-Coding-Projekt mit dem Ziel, proprietäre Vereinssoftware zu ersetzen – generisch genug für beliebige Kleingärtnervereine.

📖 **Ausführliche Dokumentation im [Wiki](../../wiki)**

---

## Lizenz

Dieses Projekt steht unter der **GNU Affero General Public License v3.0**
(siehe [LICENSE](./LICENSE)). Das bedeutet insbesondere: Wer eine
modifizierte Version dieser Software als Netzwerkdienst (z.B. SaaS für
andere Vereine) betreibt, muss den Quellcode der modifizierten Version
öffentlich zugänglich machen. Details und Mitwirkungshinweise in [CONTRIBUTING.md](./CONTRIBUTING.md).

---

## Funktionen (aktueller Stand)

- ✅ Benutzeranmeldung per Session (Cookie-basiert)
- ✅ Einladungssystem (kein öffentliches Registrieren)
- ✅ Rollensystem: Admin, Vorstand, Kassierer, Lesend
- ✅ Mitgliederverwaltung (Stammdaten, mehrere Telefonnummern, mehrere E-Mail-Adressen, IBAN)
- ✅ Parzellentverwaltung (Status: aktiv/gekündigt/gelöscht, Fläche, Kündigung)
- ✅ m:n-Zuordnung Mitglied ↔ Parzelle (Haupt-/Mitpächter, Doppelgärten)
- ✅ CSV-Export und -Import (Mitglieder, Parzellen) mit Duplikaterkennung
- ✅ Vereinseinstellungen (Flächen A/B/C, SMTP-Konfiguration)
- ✅ Dashboard mit Live-Statistiken (Mitglieder, Parzellen, Flächen)
- ✅ Pflichtstunden-System (Jahresbasierte Konfiguration, konfigurierbar pro Parzelle oder pro Mitglied)
- ✅ Arbeitseinsätze (Standard und Besondere), Teilnehmerverwaltung mit Stunden-Erfassung
- ✅ Patenschaften (pauschale Stundenanrechnung für Bereichsverantwortliche)
- ✅ Vereinsrollen / erweiterter Vorstand mit Pflichtstunden-Befreiung
- ✅ Jahresauswertung Pflichtstunden mit CSV-Export
- ✅ REST-API mit JWT-Authentifizierung und Swagger-Dokumentation
- ✅ Datenbankmigrationen via Alembic

## Geplant (nächste Phasen)

- Passwort ändern für eingeloggte Benutzer
- Mitglied einer Vereinsrolle zuordnen (UI)
- Rechnungsstellung (per Parzelle, Fläche, Mitglied)
- Strom- und Wasserabrechnung
- Dokumentenverwaltung
- Serienbriefe / E-Mail-Kampagnen
- WordPress-Integration (Arbeitseinsatz-Anmeldungen per API)
- i18n (Englische Oberfläche)

---

## Tech-Stack

| Komponente | Technologie |
|---|---|
| Backend | Python 3.12 + FastAPI |
| Templates | Jinja2 (Server-Side Rendering) |
| CSS | Bootstrap 5 |
| Datenbank | PostgreSQL 16 |
| Migrationen | Alembic |
| Container | Docker + docker compose |

---

## Schnellstart (Entwicklung)

### 1. Repository klonen und konfigurieren

```bash
git clone https://github.com/kermie/gartenverein.git
cd gartenverein
cp .env.example .env
# .env nach Bedarf anpassen (Passwörter, SMTP etc.)
```

### 2. UID/GID eintragen (verhindert root-Dateien auf dem Host)

```bash
echo "UID=$(id -u)" >> .env
echo "GID=$(id -g)" >> .env
```

### 3. Docker-Container bauen, Datenbank migrieren und starten

```bash
docker compose build web
docker compose run --rm --entrypoint alembic web upgrade head
docker compose up -d
```

Die Anwendung ist nun unter **http://localhost:8000** erreichbar.
API-Dokumentation: **http://localhost:8000/api/docs**

### 4. Erster Login

Beim ersten Start wird automatisch ein Admin-Konto angelegt:

- **E-Mail:** `admin@gartenverein.local`
- **Passwort:** `admin1234`

⚠️ **Bitte sofort nach dem ersten Login das Passwort ändern!**

---

## REST-API

Neben der Web-Oberfläche gibt es eine vollständige REST-API unter `/api/v1/`.

**Interaktive Dokumentation:**
- Swagger UI: http://localhost:8000/api/docs
- ReDoc: http://localhost:8000/api/redoc
- OpenAPI-Schema (JSON): http://localhost:8000/api/openapi.json

### Authentifizierung (JWT)

```bash
# Token anfordern
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "admin@gartenverein.local", "passwort": "admin1234"}'

# Antwort: {"access_token": "...", "token_type": "bearer", "expires_in_minuten": 1440}

# Token verwenden
curl http://localhost:8000/api/v1/mitglieder \
  -H "Authorization: Bearer <access_token>"
```

Tokens sind 24 Stunden gültig. Die Swagger-UI hat einen "Authorize"-Button für bequemes Testen.

### Wichtigste Endpunkte

| Methode | Pfad | Beschreibung |
|---|---|---|
| POST | `/api/v1/auth/login` | Token anfordern (JSON) |
| GET | `/api/v1/auth/me` | Eigenes Profil abrufen |
| GET | `/api/v1/stats` | Dashboard-Statistiken |
| GET | `/api/v1/mitglieder` | Mitglieder auflisten (Suche, Paginierung) |
| GET | `/api/v1/mitglieder/{id}` | Mitglied inkl. Parzellen abrufen |
| POST | `/api/v1/mitglieder` | Mitglied anlegen |
| PUT | `/api/v1/mitglieder/{id}` | Mitglied aktualisieren (Teilupdate) |
| DELETE | `/api/v1/mitglieder/{id}` | Mitglied löschen (Soft-Delete) |
| POST | `/api/v1/mitglieder/{id}/telefonnummern` | Telefonnummer hinzufügen |
| POST | `/api/v1/mitglieder/{id}/email-adressen` | E-Mail-Adresse hinzufügen |
| GET | `/api/v1/parzellen` | Parzellen auflisten (Status-Filter) |
| GET | `/api/v1/parzellen/{id}` | Parzelle inkl. Mitgliedern abrufen |
| POST | `/api/v1/parzellen` | Parzelle anlegen |
| PUT | `/api/v1/parzellen/{id}` | Parzelle aktualisieren (auch Status/Kündigung) |
| POST | `/api/v1/parzellen/{id}/zuordnungen` | Mitglied zuordnen |
| DELETE | `/api/v1/parzellen/{id}/zuordnungen/{zid}` | Zuordnung entfernen |
| GET | `/api/v1/einstellungen` | Vereinseinstellungen abrufen |
| PUT | `/api/v1/einstellungen/{schluessel}` | Einstellung setzen (nur Admin/Vorstand) |

Schreibzugriff (POST/PUT/DELETE) erfordert die Rolle `admin`, `vorstand` oder `kassierer`.
Lesezugriff ist für alle authentifizierten Benutzer (auch `lesend`) erlaubt.

---

## Datenbankmigrationen (Alembic)

Schemaänderungen laufen über Alembic statt automatischem `create_all()`.

```bash
# Migrationen anwenden (läuft auch automatisch beim Containerstart)
docker compose run --rm --entrypoint alembic web upgrade head

# Neue Migration nach Modelländerung erzeugen
docker compose run --rm web alembic revision --autogenerate -m "Kurzbeschreibung"
```

Bei bestehender Installation vor Alembic-Einführung: siehe [MIGRATION-HINWEIS.md](./MIGRATION-HINWEIS.md).

---

## Produktion (Hetzner)

Für Produktion `ENVIRONMENT=production` setzen:

```bash
SECRET_KEY=<langer-zufaelliger-string>
ENVIRONMENT=production
POSTGRES_PASSWORD=<sicheres-passwort>
```

Empfehlung: Nginx als Reverse Proxy mit Let's Encrypt (Certbot) vorschalten.

```nginx
server {
    listen 443 ssl;
    server_name verwaltung.meinverein.de;
    location / {
        proxy_pass http://localhost:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

---

## Datenbankstruktur

```
benutzer                    – Anwendungsbenutzer (nicht Vereinsmitglieder)
einladungen                 – Einladungstoken per E-Mail
mitglieder                  – Vereinsmitglieder
mitglied_telefon            – n Telefonnummern pro Mitglied
mitglied_email              – n E-Mail-Adressen pro Mitglied
parzellen                   – Gartenparzellen
mitglied_parzelle           – m:n Zuordnung Mitglied ↔ Parzelle (mit Metadaten)
vereinseinstellungen        – Key-Value für Vereinsstammdaten
pflichtstunden_konfiguration – Jahresbasierte Stunden/Satz-Konfiguration
vereinsrollen               – Vereinsämter (Vorstand, erweiterter Vorstand etc.)
mitglied_vereinsrolle       – Zuordnung Mitglied → Vereinsrolle (jahresbasiert)
arbeitseinsaetze            – Standard- und Besondere Einsätze
einsatz_teilnahmen          – Wer war bei welchem Einsatz (mit Stunden)
patenschaften               – Bereichsverantwortlichkeiten (pauschale Stundenanrechnung)
```
