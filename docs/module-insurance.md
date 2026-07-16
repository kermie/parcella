# Module: Insurance (Versicherungen)

> **Note on the renaming:** the code (models, tables, URLs, API
> endpoints) has been fully converted to English:
> `SachversicherungPaket` -> `PropertyInsurancePackage`,
> `VersicherungsKonfiguration` -> `InsuranceConfiguration`,
> `ParzelleVersicherung` -> `ParcelInsurance`,
> `UnfallversicherungZusatzperson` -> `AccidentInsuranceAdditionalPerson`,
> `/versicherungen/` -> `/insurance/`. Details and lessons learned in
> [Architecture Decisions](./architecture-decisions.md).
> This page continues to describe the domain logic, which did not
> change in the process.

Manages two optional insurance types taken out per parcel: property
insurance (selectable package) and accident insurance (with automatic
household detection).

Module flag: `insurance`

## Data model

```
property_insurance_packages         – configurable packages per year (e.g. 40/60/80/100 EUR)
insurance_configuration              – year basis: accident base and additional amount
parcel_insurance                     – insurance status of a parcel for a year
accident_insurance_additional_persons – who is additionally insured beyond the household
```

## Key decision: household detection via address comparison

Accident insurance automatically covers all residents of a parcel who
share **the same address** with each other (street, postal code, city in
the member record) -- at no extra cost, since they live in the same
household. There's no designated "primary" resident to anchor this
comparison on (that role distinction was removed, see
[Architecture Decisions](./architecture-decisions.md)); instead, current
residents are grouped by matching address to each other, and the largest
matching group is the auto-covered household.

Residents with a **different address** are shown as candidates but **not**
added automatically -- the association deliberately decides per person
(checkbox) whether they should be additionally insured for the extra
amount. This was an explicit requirement: "can be additionally insured"
means opt-in, not automatic.

The detection happens in `household_grouping()`
(`app/insurance_utils.py`) and is deliberately **just a display aid**,
not a hard rule in the database -- the actual billing is based on the
explicit selection in `accident_insurance_additional_persons`, not on a
live calculation of addresses. This means: if a member's address changes
later, past years' billing is not retroactively affected.

## Configurable packages instead of fixed values

The property insurance packages (currently 40/60/80/100 EUR) are their
own table (`property_insurance_packages`), year-based, with a freely
editable number of packages and amounts -- not a hard-coded four-package
model. This follows the same principle as the work-hours configuration:
values that can change annually belong in a table, not in code.

## Known pitfalls

- Same `MissingGreenlet` pitfall as in the metering module: when a
  `ParcelInsurance` is created for the first time (when a parcel is
  opened for a year for the first time), the relationships must be
  explicitly reloaded after the commit before accessing `property_package`
  or `additional_persons`. See `_get_or_create_pi()`.

## REST API

This module has (added after the fact) a complete set of REST API
endpoints for this module (JWT-authenticated, see `/api/docs`). See the
README for the endpoint overview. Background: early modules were
initially built as web UI only, with the API added later -- since then
the rule is that every new module gets **both** the web UI and API
endpoints **from the start** (see Architecture Decisions).
