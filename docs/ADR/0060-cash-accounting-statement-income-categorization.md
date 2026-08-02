# Cash-based accounting statement: income categorized via invoice line items, AccountTransaction excluded

**Context:** [Issue #179](https://github.com/kermie/parcella/issues/179)
asked for a cash-based accounting statement for the tax office,
broken down by the categories a board member already enters
(`FinanceCategory`, issue #67). Expenses are straightforward --
`IncomingInvoiceLineItem` (issue #178) already carries `category_id` +
`amount` per position. Income is not: `InvoicePayment` (money actually
received against a club invoice) has never had a category of its own,
and neither does `AccountTransaction` (issue #174's generic ledger
entry for money movements with no invoice behind them).

**Decision: derive a payment's category breakdown from the invoice it
settles, splitting proportionally by each category's share of the
invoice subtotal; leave `AccountTransaction` out of the statement
entirely.**

- Added `InvoiceLineItem.category_id` (nullable, `ON DELETE SET NULL`),
  copied from the originating `InvoiceItemDefinition.category_id` at
  finalize time (`app/invoice_generation.py`) -- the same "snapshot at
  finalize" treatment already given to `name`/`description`/
  `quantity`/`unit_price`.
- `app/accounting_statement.py`'s `compute_cash_accounting_statement()`
  loads every `InvoicePayment` in the target year with its invoice's
  line items, computes each category's share of the invoice subtotal,
  and attributes that share of the payment amount to that category. A
  payment for an invoice with no line items, or a zero subtotal, is
  entirely uncategorized.
- Expenses: `IncomingInvoiceLineItem.amount` grouped by `category_id`,
  using the incoming invoice's `invoice_date` as the cash-out date --
  there's no separate "paid on" date for incoming invoices (unlike
  outgoing `InvoicePayment`), so this assumes a bill counts as paid
  when it was recorded, a known simplification.
- `AccountTransaction` is excluded from the statement entirely. It has
  no category of its own (see ADR 0059 -- it was designed as a generic
  ledger row, not a categorized one), and guessing a category from its
  sign or description would not serve "based on the categories I
  entered" any better than leaving it out. A club with meaningful
  uncategorized cash movements there won't see them reflected in this
  statement.

**Consequence, accepted:** `InvoiceLineItem.category_id` didn't exist
before this change, and there was never a stored link from a
finalized `InvoiceLineItem` back to the `InvoiceItemDefinition` it came
from -- so payments against invoices finalized before this migration
are **permanently** uncategorized income in this statement, with no
backfill possible. Categorized income only becomes accurate for
invoice runs finalized after this feature shipped. This was confirmed
directly with the reporter as an acceptable trade-off over the
alternative (adding `category_id` directly to `InvoicePayment`, which
would have the same historical-data gap but without reusing the
invoice's own categorization).
