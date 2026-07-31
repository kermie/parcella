# Module: Metering (Water & Electricity)

> **Note on the renaming:** the code (models, tables, URLs, API
> endpoints) has been fully converted to English:
> `Zaehlpunkt` -> `MeteringPoint`, `Zaehler` -> `Meter`,
> `Zaehlerstand` -> `MeterReading`, `/wasser/` -> `/water/`,
> `/strom/` -> `/electricity/`. Details and lessons learned in
> [Architecture Decisions](./ADR/0009-third-module-to-english-zaehlerwesen-metering.md).
> This page continues to describe the domain logic, which did not
> change in the process.

Manages water and electricity meters via **one shared codebase** -- the
clearest example in the project of generalizing structurally similar
requirements instead of duplicating them.

Module flags: `water` and `electricity` (independently toggleable)

## Data model

```
metering_points  – a metering point: main meter, parcel, or club connection.
                   Has a "medium" (WATER/ELECTRICITY) and a "type".
meters           – the physical meter at a metering point (number,
                   calibration deadline, install/removal date, initial reading)
meter_readings   – annual readings of a meter
```

A parcel can have both a water and an electricity metering point -- two
rows in the same table, distinguished by `medium`.

## Key decision: router factory instead of duplication

Water and electricity are structurally identical: main meter + sub-
meters, annual reading, consumption calculation, plausibility checks. The
only differences are the unit (m³ vs. kWh), decimal places (1 vs. 0), and
display labels.

Instead of maintaining two separate router files, there is **one**
factory function, `erstelle_metering_router()`, in
`app/routers/metering.py`, which produces a fully configured router for
**one** medium. `main.py` instantiates it twice:

```python
water_router = erstelle_metering_router(
    medium=MeteringMedium.WATER, url_prefix="/water", modul_name="water",
    medium_label="Wasser", unit="m³", icon="bi-droplet", dezimalstellen=1,
)
electricity_router = erstelle_metering_router(
    medium=MeteringMedium.ELECTRICITY, url_prefix="/electricity", modul_name="electricity",
    medium_label="Strom", unit="kWh", icon="bi-lightning-charge", dezimalstellen=0,
)
```

A bug fix or new feature therefore only has to be written **once**. The
templates (`app/templates/metering/`) are likewise shared -- they receive
`unit`, `medium_label`, `icon`, etc. as variables instead of hard-coding
the values.

If a third medium is added in the future (gas?), one more call to
`erstelle_metering_router()` with the matching configuration is enough.

## Plausibility checks

**Per-meter monotonicity** (hard, blocking): a new reading must not be
smaller than the previous reading for the *same* meter number -- both
backward (not smaller than the prior value) and forward (not larger than
an already-recorded later value, if one exists). See
`check_monotonicity()` in `app/zaehler_utils.py`.

**Overall plausibility** (warning, non-blocking): the sum of parcel and
club consumption must not exceed the main meter's consumption. This is
shown as a warning banner, not an error -- because readings are entered
with a time lag, and a temporarily "incomplete" data state is not an
error but normal.

## Meter exchange and history

When a meter is exchanged (e.g. every 6 years for water, due to
calibration deadlines), the old one is **not deleted** but deactivated
(`is_active = false`, `removed_at` set). The new meter gets its own row
with a new number and its own initial reading. This correctly separates
consumption calculations -- no mixing of old and new meter readings.

## Known pitfalls

- **Jinja2 can't do Python's `.format()`**: `"%.{}f"|format(places)|format(value)`
  does not work (Jinja's `format` filter uses the old `%` operator).
  Solution: a custom Jinja filter `fmt`, registered directly on the
  `Jinja2Templates` object in `metering.py`:
  ```python
  templates.env.filters["fmt"] = lambda value, places: f"{float(value):.{places}f}"
  ```
- **MissingGreenlet on creation**: when a database row is newly created
  via `db.add()` + `commit()` (rather than loaded via a query), its
  relationships (`relationship` fields) are not eagerly loaded. A later
  access triggers a synchronous lazy load, which raises `MissingGreenlet`
  with the async database driver. Fix: explicitly reload the row with
  `selectinload(...)` after creating it (see `_get_or_create_pi()` in the
  insurance module for an example of this pattern).

## REST API

This module has (added after the fact) a complete set of REST API
endpoints for this module (JWT-authenticated, see `/api/docs`). See the
README for the endpoint overview. Background: early modules were
initially built as web UI only, with the API added later -- since then
the rule is that every new module gets **both** the web UI and API
endpoints **from the start** (see Architecture Decisions).
