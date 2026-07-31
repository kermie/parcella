# Metering price configuration drives automatic water/electricity usage billing

**Context:** ADR 0042 already established two "automatically scoped"
invoice pricing modes -- `WORK_HOURS_SHORTFALL` (rate from
`WorkHoursConfiguration`) and `INSURANCE_COST` (premiums from the
insurance module) -- where the item form hides both the manual unit
price and the parcel-scope picker, since the underlying module's own
data already determines who owes what.

`WATER_USAGE`/`ELECTRICITY_USAGE` were meant to join that group from
the start -- the pricing-mode dropdown already labeled them "(from
module)" -- but the metering module never actually got a price
setting, so both modes still required a manually-entered `unit_price`
on every item template/run item, and still respected the manual
parcel-scope picker even though a parcel without a meter of that
medium was already excluded by `calculate_consumption()` returning
`None`. In practice this meant water/electricity billing was
non-functional: the two real item templates for this club had
`unit_price` left blank (nothing to bill) precisely because there was
nowhere else to put the number.

**Decision: give `WATER_USAGE`/`ELECTRICITY_USAGE` full parity with
`WORK_HOURS_SHORTFALL`/`INSURANCE_COST`.**

- New `MeteringPriceConfiguration` model: one row per `(medium, year)`
  (unique constraint on the pair), holding `price_per_unit` (EUR per
  m³/kWh) + an optional note. Historized per year, mirroring
  `WorkHoursConfiguration`'s shape exactly (ADR 0005: old years' prices
  must stay intact for past runs' invoices).
- Edited at `/water/configuration` / `/electricity/configuration` --
  added directly to the existing `create_metering_router()` factory
  (`app/routers/metering.py`), so both media share the same CRUD
  routes/templates rather than duplicating a settings page per medium.
- `item_quantity_and_price` (`app/invoice_generation.py`) now reads
  `price_per_unit` from this table for the run's year instead of
  `definition.unit_price`; if no price is configured for that year,
  the item bills nothing for that year -- same "nothing configured ->
  nothing billed" behavior insurance/work-hours already have.
- The parcel-scope bypass tuple in `compute_invoices_for_run` gains
  `WATER_USAGE`/`ELECTRICITY_USAGE` alongside the existing two modes:
  every parcel becomes a loop candidate, and having (or not having) an
  active metering point + reading of that medium is what actually
  limits billing -- exactly the same shape as insurance/work-hours
  limiting themselves via their own module's data, not a manual
  picker.
- `item_template_create`/`item_template_update`/`item_create`/
  `item_update` (`app/routers/finances.py`) now null out `unit_price`
  for these two modes as well; the item forms hide their scope picker
  and unit-price input and show a mode-specific "Automatic (parcels
  with an active water/electricity meter)" note, mirroring the
  existing insurance/work-hours treatment exactly.

**Consequence, confirmed acceptable:** this removes the *ability* to
manually restrict water/electricity billing to a parcel subset via the
item template/run item -- both of this club's real templates already
had `applies_to_all_parcels=True`, so nothing currently configured
changes behavior. A club that genuinely needs to exclude a specific
metered parcel from billing has no such override going forward (same
constraint insurance/work-hours already accepted in ADR 0042).

**Update (2026-07-31): the price-sourcing half was reverted; the
scope-bypass half was kept.** The user clarified the actual want after
seeing this shipped: a club's utility tariff (EUR per m³/kWh) changes
from one invoice run to the next, and the price should stay a
manually-typed field entered fresh when writing each invoice -- not a
per-year setting stored centrally and hidden from the item form.
`MeteringPriceConfiguration` (model, migration, `/water/configuration`
+`/electricity/configuration` CRUD, REST endpoints) was removed
entirely; `item_quantity_and_price` reads `definition.unit_price`
again for `WATER_USAGE`/`ELECTRICITY_USAGE`, and `finances.py`'s
`_AUTOMATIC_PRICING_MODES` no longer includes these two modes, so the
unit-price input is visible again on both item forms. The *scope*
bypass (a parcel with no active meter of that medium simply has no
consumption to bill, so the manual parcel-scope picker stays hidden)
was correct and is unchanged -- only the price half of "full parity
with insurance/work-hours" was the misread.

**Update (2026-07-31, later same day): the price-sourcing half was
restored.** After seeing manual per-invoice pricing live, the user
clarified they actually wanted the original per-year config back --
the config-page idea was "brilliant" and should stay as it was.
`MeteringPriceConfiguration` (model, migration, `/water/configuration`
+`/electricity/configuration` CRUD, REST endpoints) was re-added
exactly as originally shipped; `item_quantity_and_price` reads price
from it again for `WATER_USAGE`/`ELECTRICITY_USAGE`, and
`finances.py`'s `_AUTOMATIC_PRICING_MODES` includes both modes again,
so the unit-price input is hidden on both item forms once more, same
as insurance/work-hours. Net effect after both updates: back to this
ADR's original design exactly as first written above -- the
intermediate revert is kept in history for anyone wondering why the
migration numbering has a drop-then-recreate pair (0061 create, 0064
drop, 0065 recreate) rather than one straight line.
