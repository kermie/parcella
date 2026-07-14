# Parcella

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL%20v3-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.12-blue)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-green)](https://fastapi.tiangolo.com)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-blue)](https://postgresql.org)

An open-source web application for managing allotment garden associations
("Kleingartenverein" / "Schrebergarten" associations): members, parcels,
lease administration, and mandatory work hours.

Started as a vibe-coding project with the goal of replacing proprietary
association software -- generic enough for any allotment garden
association, in any country.

📖 **Detailed documentation in the [Wiki](../../wiki)**

---

## License

This project is licensed under the **GNU Affero General Public License
v3.0** (see [LICENSE](./LICENSE)). In particular, this means: anyone who
runs a modified version of this software as a network service (e.g. SaaS
for other associations) must make the source code of that modified
version publicly available. Details and contribution guidelines in
[CONTRIBUTING.md](./CONTRIBUTING.md).

---

## Features (current state)

- ✅ Session-based login (cookie-based)
- ✅ Invitation system (no public sign-up)
- ✅ Role system: Admin, Board, Treasurer, Read-only
- ✅ Member management (core data, multiple phone numbers, multiple email
  addresses, IBAN)
- ✅ Parcel management (status: active/terminated/deleted, area, termination)
- ✅ Many-to-many member ↔ parcel assignment (primary/co-tenant, multiple
  parcels per member)
- ✅ CSV export and import (members, parcels) with duplicate detection
- ✅ Club settings (A/B/C area sizes, SMTP configuration)
- ✅ Dashboard with live statistics (members, parcels, areas)
- ✅ Work-hours system (year-based configuration, configurable per parcel
  or per member)
- ✅ Work sessions (standard and special), participant management with
  hours tracking
- ✅ Sponsorships (flat-rate hour credit for area coordinators)
- ✅ Club roles / extended board with work-hours exemption
- ✅ Annual work-hours report with CSV export
- ✅ Water and electricity metering (metering points, meters, readings,
  consumption reports)
- ✅ Property and accident insurance tracking per parcel, with annual
  report
- ✅ Ticket system with automatic member matching, spam heuristics, and
  IMAP inbox polling
- ✅ Purchase requests with a two-person approval principle (two distinct
  board members must approve before a purchase is made)
- ✅ REST API with JWT authentication and Swagger documentation
- ✅ Database migrations via Alembic
- ✅ i18n: 7 languages (German, English, Polish, Czech, Slovak, French,
  Dutch), one language per installation, switchable in admin settings,
  every module and the navigation fully translated. JSON translation
  catalogs, English as the runtime fallback for any missing key.
- ✅ l10n: region and currency are independent settings from language
  (e.g. an English-language UI can still show German number formatting
  and EUR). Number and money formatting via Babel (correct
  decimal/thousands separators and currency symbol position per
  region); address display order adapts per region (continental
  postcode-before-city vs. UK-style postcode-last).
- ✅ Responsive/mobile layout: off-canvas navigation on narrow screens,
  wide tables scroll independently of the page

## Planned (next phases)

- Password change for logged-in users
- Assign a member to a club role (UI)
- Invoicing (per parcel, area, or member)
- Document management
- Mail merge / email campaigns
- WordPress integration (work-session sign-up via API)

---

## Tech stack

| Component | Technology |
|---|---|
| Backend | Python 3.12 + FastAPI |
| Templates | Jinja2 (server-side rendering) |
| CSS | Bootstrap 5 |
| Database | PostgreSQL 16 |
| Migrations | Alembic |
| i18n/l10n | JSON translation catalogs + Babel (number/currency formatting) |
| Container | Docker + docker compose |

---

## Quick start (development)

### 1. Clone and configure the repository

```bash
git clone https://github.com/kermie/gartenverein.git
cd gartenverein
cp .env.example .env
# adjust .env as needed (passwords, SMTP, etc.)
```

### 2. Set UID/GID (avoids root-owned files on the host)

```bash
echo "UID=$(id -u)" >> .env
echo "GID=$(id -g)" >> .env
```

### 3. Build the Docker container, migrate the database, and start

```bash
docker compose build web
docker compose run --rm --entrypoint alembic web upgrade head
docker compose up -d
```

The application is now available at **http://localhost:8000**.
API documentation: **http://localhost:8000/api/docs**

### 4. First login

An admin account is created automatically on first startup:

- **Email:** `admin@gartenverein.local`
- **Password:** `admin1234`

⚠️ **Please change the password immediately after your first login!**

---

## REST API

Alongside the web UI, there is a full REST API under `/api/v1/`.

**Interactive documentation:**
- Swagger UI: http://localhost:8000/api/docs
- ReDoc: http://localhost:8000/api/redoc
- OpenAPI schema (JSON): http://localhost:8000/api/openapi.json

### Authentication (JWT)

```bash
# Request a token
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "admin@gartenverein.local", "password": "admin1234"}'

# Response: {"access_token": "...", "token_type": "bearer", "expires_in_minutes": 1440}

# Use the token
curl http://localhost:8000/api/v1/members \
  -H "Authorization: Bearer <access_token>"
```

Tokens are valid for 24 hours. The Swagger UI has an "Authorize" button
for convenient testing.

### Key endpoints

| Method | Path | Description |
|---|---|---|
| POST | `/api/v1/auth/login` | Request token (JSON) |
| GET | `/api/v1/auth/me` | Retrieve own profile |
| GET | `/api/v1/stats` | Dashboard statistics |
| GET | `/api/v1/members` | List members (search, pagination) |
| GET | `/api/v1/members/{id}` | Retrieve member incl. parcels |
| POST | `/api/v1/members` | Create member |
| PUT | `/api/v1/members/{id}` | Update member (partial update) |
| DELETE | `/api/v1/members/{id}` | Delete member (soft delete) |
| POST | `/api/v1/members/{id}/phones` | Add phone number |
| POST | `/api/v1/members/{id}/emails` | Add email address |
| GET | `/api/v1/parcels` | List parcels (status filter) |
| GET | `/api/v1/parcels/{id}` | Retrieve parcel incl. members |
| POST | `/api/v1/parcels` | Create parcel |
| PUT | `/api/v1/parcels/{id}` | Update parcel (also status/termination) |
| POST | `/api/v1/parcels/{id}/assignments` | Assign a member |
| DELETE | `/api/v1/parcels/{id}/assignments/{aid}` | Remove assignment |
| GET | `/api/v1/club-settings` | Retrieve club settings |
| PUT | `/api/v1/club-settings/{key}` | Set a setting (admin/board only) |
| GET/PUT | `/api/v1/work-hours/configuration/{year}` | Work-hours configuration |
| GET/POST/PUT/DELETE | `/api/v1/work-hours/club-roles` | Club roles + assignments |
| GET/POST/PUT/DELETE | `/api/v1/work-hours/sessions` | Work sessions + participations |
| GET/POST/PUT/DELETE | `/api/v1/work-hours/sponsorships` | Sponsorships |
| GET | `/api/v1/work-hours/evaluation/{year}` | Annual report |
| GET/POST/PUT/DELETE | `/api/v1/water/metering-points` | Water metering points + meters |
| POST | `/api/v1/water/metering-points/{id}/meter/exchange` | Exchange water meter |
| GET/POST/DELETE | `/api/v1/water/metering-points/{id}/readings` | Water readings |
| GET | `/api/v1/water/evaluation/{year}` | Water consumption report |
| GET/POST/PUT/DELETE | `/api/v1/electricity/metering-points` | Electricity metering points + meters (same shape as water) |
| GET/POST/PUT/DELETE | `/api/v1/insurance/packages` | Property insurance packages |
| GET/PUT | `/api/v1/insurance/configuration/{year}` | Accident insurance amounts |
| GET/PUT | `/api/v1/insurance/parcels/{id}/{year}` | Insurance status of a parcel |
| GET | `/api/v1/insurance/evaluation/{year}` | Annual report |
| GET/POST | `/api/v1/tickets` | List/create tickets |
| GET/PUT | `/api/v1/tickets/{id}` | Ticket detail / status / assignment |
| GET/POST | `/api/v1/tickets/{id}/messages` | Ticket messages |
| GET/POST | `/api/v1/purchase-requests` | List/create purchase requests |
| POST | `/api/v1/purchase-requests/{id}/approve` | Approve (two distinct approvals needed) |
| POST | `/api/v1/purchase-requests/{id}/reject` | Reject (single rejection is enough) |

Write access (POST/PUT/DELETE) requires the role `admin`, `board`, or
`treasurer`. Read access is available to all authenticated users
(including `readonly`).

---

## Database migrations (Alembic)

Schema changes go through Alembic rather than automatic `create_all()`.

```bash
# Apply migrations (also runs automatically on container startup)
docker compose run --rm --entrypoint alembic web upgrade head

# Generate a new migration after a model change
docker compose run --rm web alembic revision --autogenerate -m "Short description"
```

For an existing installation predating Alembic: see
[MIGRATION-NOTE.md](./MIGRATION-NOTE.md).

---

## Production (Hetzner)

For production, set `ENVIRONMENT=production`:

```bash
SECRET_KEY=<long-random-string>
ENVIRONMENT=production
POSTGRES_PASSWORD=<secure-password>
```

Recommended: put Nginx in front as a reverse proxy with Let's Encrypt
(Certbot).

```nginx
server {
    listen 443 ssl;
    server_name verwaltung.myassociation.example;
    location / {
        proxy_pass http://localhost:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

---

## Database structure

```
users                         – application users (not club members)
invitations                   – email invitation tokens
members                       – club members
member_phones                 – n phone numbers per member
member_emails                 – n email addresses per member
parcels                       – garden parcels
member_parcels                – m:n member <-> parcel assignment (with metadata)
club_settings                 – key-value store for club master data
work_hours_configuration      – year-based hours/rate configuration
club_roles                    – club offices (board, extended board, etc.)
member_club_roles             – member -> club role assignment (year-based)
work_sessions                 – standard and special work sessions
session_participations        – who attended which session (with hours)
sponsorships                  – area responsibilities (flat-rate hour credit)
change_history                 – generic audit log for field changes
metering_points, meters,
meter_readings                 – water/electricity metering
property_insurance_packages,
insurance_configuration,
parcel_insurance                – insurance tracking per parcel/year
tickets, ticket_messages        – support ticket system
purchase_requests,
purchase_request_approvals      – purchase requests with two-person approval
```
