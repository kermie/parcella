# Account bookings can be matched against open invoices, creating a real payment

**Context:** ADR 0059 explicitly scoped reconciliation out of the
account bookings feature: "reconciling an import against outstanding
invoices is a different, harder problem, deliberately out of scope."
[Issue #188](https://github.com/kermie/parcella/issues/188) asked for
exactly that -- while recording a manual booking or importing a CSV,
show a list of open invoices whose amount (and ideally counterparty
name) matches, and let the user pick one. Confirmed directly with the
reporter: picking a match should create a real `InvoicePayment`/
`IncomingInvoicePayment` against that invoice, not just an
informational link, and the picker should apply to both the manual
"Add booking" form and CSV import (reviewed per row before anything is
created).

**Decision: add an explicit, user-confirmed matching step to both
entry points, never automatic.**

- `app/invoice_matching.py`'s `find_matching_invoices()` looks for
  still-open invoices whose amount matches exactly (within a cent, to
  tolerate float/Decimal conversion): a non-negative booking amount
  searches outgoing `Invoice`s (by `amount_due`), a negative one
  searches `IncomingInvoice`s (by `total_amount - paid_total`) --
  direction is inferred from the sign, since a booking's amount
  already tells you which side of the ledger it settles. Candidates
  are then flagged (not filtered) by whether the counterparty name
  overlaps the invoice's recipient/sender, and `best_match_option()`
  only pre-selects a candidate when there's exactly one name match --
  never guesses between several plausible ones.
- Manual add is now three steps instead of one:
  `/bookings/manual/preview` (compute matches, render a choice) ->
  `/bookings/manual/confirm` (create the chosen record). CSV import
  gained a middle step between the existing column-mapping page and
  actually creating anything: `/bookings/import/match` (one dropdown
  per row) -> `/bookings/import/finalize` (creates each row per its
  chosen match, still applying the CSV/#187 IBAN backfill
  independently of whether a row matched an invoice).
- Picking a match creates `InvoicePayment`/`IncomingInvoicePayment`
  with `account_id` set (so it still shows up in that account's
  bookings and in the club-wide bookings list, issue #180) and
  `amount = abs(booking amount)` (payments are always positive,
  regardless of which direction the account booking's signed amount
  came in). Not picking a match falls back to the exact same
  `AccountTransaction` creation as before.

**Consequence, accepted:** ADR 0059's reconciliation-is-out-of-scope
stance still holds for the case it was actually about --
automatically matching/merging bank statement rows without user
confirmation. This feature never does that: nothing is ever matched
without an explicit, visible choice, and "no match" (a generic
booking) remains the default whenever there's ambiguity or no
candidate at all.
