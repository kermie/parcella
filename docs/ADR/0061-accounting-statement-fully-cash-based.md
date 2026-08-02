# Cash-based accounting statement: expenses now use actual payment date, not invoice date

**Context:** [ADR 0060](./0060-cash-accounting-statement-income-categorization.md)
shipped the statement's income side already fully cash-based (only
`InvoicePayment.paid_on`-dated money ever counted), but the expense
side counted `IncomingInvoiceLineItem.amount` on the incoming
invoice's `invoice_date` -- i.e. the moment a bill was *recorded*, not
when it was actually paid, because incoming invoices had no payment
tracking yet. [Issue #182](https://github.com/kermie/parcella/issues/182)
called this out directly: "outgoing invoices and incoming invoices
cannot be added as long as they are not paid yet." [Issue #181](https://github.com/kermie/parcella/issues/181)
(mirroring `InvoicePayment` as `IncomingInvoicePayment`) landed first
specifically to unblock this fix.

**Decision: the expense side now mirrors the income side exactly --
`IncomingInvoicePayment.paid_on` decides which year an expense lands
in, and the payment amount is split proportionally across the incoming
invoice's line-item categories, the same shared logic
(`_split_payment_by_category` in `app/accounting_statement.py`) the
income side already used.**

- An incoming invoice with no payment recorded contributes nothing to
  the statement, for any year, until a payment exists against it --
  same as an outgoing invoice's subtotal never appearing until someone
  pays it.
- A partially-paid incoming invoice only counts the paid portion, in
  the year(s) it was actually paid -- a payment split across two
  calendar years (e.g. a bill from December, paid in January) lands
  its category amounts in the year of each individual payment.
- `available_statement_years()` now derives its year list from both
  `InvoicePayment.paid_on` and `IncomingInvoicePayment.paid_on`,
  instead of `IncomingInvoice.invoice_date`.

**Consequence, accepted:** any incoming invoice recorded before issue
#181 shipped, with no payment ever entered against it, disappears from
this statement entirely -- it was never really "cash based" before,
so this isn't a regression, but a club relying on the old
(inaccurate) expense figures needs to go back and record payments
against its historical incoming invoices for those years to reappear
correctly categorized.
