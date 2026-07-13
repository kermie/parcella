# Documentation

This documentation accompanies the development of the Gartenmanager
association-management software and is extended continuously with every
new feature -- not added afterwards at the end, but written while the
decisions and reasoning are still fresh.

## Modules

- [Members & Parcels](./module-members-parcels.md) -- core module, always active
- [Work Hours](./module-work-hours.md) -- work sessions, sponsorships, club roles
- [Metering (Water & Electricity)](./module-metering.md) -- shared codebase for both media
- [Insurance](./module-insurance.md) -- property and accident insurance per parcel
- [Ticket System](./module-tickets.md) -- support tickets, all 3 stages complete
- [Purchase Requests](./module-purchase-requests.md) -- two-person approval principle for club expenses

## Cross-cutting topics

- [Architecture Decisions](./architecture-decisions.md) -- why certain things are built the way they are
- [Operations](./operations.md) -- Docker, migrations, SMTP setup, troubleshooting
- [Automated Tests](./testing.md) -- testing philosophy, execution, known limits

## For new modules

The following pattern has proven itself when building a new module (see
Architecture Decisions for details):

1. Models in `app/models.py`, enum values **always uppercase** (see the
   lesson learned from several bugs around this)
2. Migration in `migrations/versions/`, revision name under 32 characters
3. Router with `dependencies=[Depends(require_modul("<name>"))]` -- both
   the web UI and the REST API from the start (API-first rule)
4. Entry in `app/module_flags.py` (`MODULE_DEFAULTS`)
5. Entry in `app/routers/admin.py` (`MODULE_FELDER`) for the enable/disable UI
6. Navigation block in `app/templates/base.html` as a collapsible `nav-group`
7. `tests/test_<module>.py` with at least one happy-path test
8. Write a new page here in `docs/` while the decisions are still fresh
