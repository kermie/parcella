# Account bookings CSV import: user-driven column mapping, not a fixed header format

**Context:** ADR 0059 (issue #174) shipped CSV import for account
bookings with a fixed expected header row
(`Date;Amount;Description;Counterparty`) -- any CSV not already in
that shape simply failed to import correctly (unmapped columns
silently ignored via `dict.get()`). [Issue #186](https://github.com/kermie/parcella/issues/186)
(part of the "refining CSV import for accounts" epic, issue #183)
asked to "import any CSV of any structure," with a mapping step so the
user tells Parcella which of their file's columns are which. [Issue
#187](https://github.com/kermie/parcella/issues/187) builds directly
on this: if the CSV also carries an IBAN column, and a row's
counterparty matches an existing member by name, backfill that
member's IBAN when it's currently empty.

**Decision: replace the one-step fixed-header import with a two-step
upload-then-map flow, and drop the fixed-header assumption entirely.**

- Step 1 (`POST /accounts/{id}/bookings/import/preview`): parses only
  the header row (plus a few preview rows) of the uploaded CSV and
  renders a mapping form -- one dropdown per detected column, target
  fields `date` (required) / `amount` (required) / `description` /
  `counterparty` / `iban` (all others optional), pre-guessed from
  common header names (`_guess_column_mapping`) but always
  overridable. Delimiter auto-detection (`csv.Sniffer`) is unchanged
  from before.
- The raw CSV bytes are round-tripped to step 2 as a hidden
  base64-encoded form field rather than kept in server-side session
  state -- consistent with this app's stateless-request style
  elsewhere, and simple enough for the file sizes a club's bank export
  actually reaches.
- Step 2 (`POST /accounts/{id}/bookings/import/confirm`) decodes the
  CSV, applies the chosen column→field mapping per row, and creates
  `AccountTransaction` rows exactly as before (still tagged
  `source="csv_import"`, still never creates or matches an
  `InvoicePayment` -- ADR 0059's reconciliation-is-out-of-scope stance
  is unchanged).
- IBAN backfill (issue #187): only attempted when both `counterparty`
  and `iban` columns are mapped. Members are matched by exact,
  case-insensitive full-name equality against `"{first} {last}"` or
  `"{last} {first}"` (to tolerate either bank-statement name order) --
  no fuzzy matching. A match only updates `Member.iban` when it is
  currently empty, per the issue's explicit "if there's already a
  value in it, skip this functionality."

**Consequence, accepted:** matching by exact full name is fragile
(a typo, a middle name, or "Firstname M. Lastname" in the bank export
won't match) -- accepted as the simplest correct behavior for the
common case; a false negative just means no IBAN gets filled in
automatically (never a wrong match, since it's exact-string, not
fuzzy). The previous one-step fixed-header import is gone entirely,
not kept as a fallback -- issue #186 explicitly asked to generalize
it, and maintaining two import code paths for the same target table
would only invite them drifting apart.
