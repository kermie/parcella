"""
Suggests possible outgoing/incoming invoice matches for an account
booking (issue #188), matched by amount first, then by counterparty
name. Confirmed with the reporter: picking a suggestion creates a real
InvoicePayment/IncomingInvoicePayment against that invoice (not just
an informational link) -- this deliberately reopens ADR 0059's
"reconciling an import against outstanding invoices is a different,
harder problem, deliberately out of scope" stance, specifically for
matching against known open invoices (matching one AccountTransaction
against another, e.g. two entries for the same bank fee, remains out
of scope -- this is squarely "this booking is actually paying off an
invoice I already know about").
"""
from dataclasses import dataclass
from decimal import Decimal
from typing import List

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import IncomingInvoice, Invoice

MAX_MATCH_SUGGESTIONS = 5
AMOUNT_TOLERANCE = Decimal("0.01")


@dataclass
class InvoiceMatch:
    kind: str  # "invoice" | "incoming_invoice"
    id: str
    label: str
    amount_due: float
    name_matches: bool

    @property
    def option_value(self) -> str:
        return f"{self.kind}:{self.id}"


def _names_overlap(a: str, b: str) -> bool:
    a = (a or "").strip().lower()
    b = (b or "").strip().lower()
    if not a or not b:
        return False
    if a in b or b in a:
        return True
    return bool(set(a.split()) & set(b.split()))


async def find_matching_invoices(db: AsyncSession, amount: Decimal, counterparty: str) -> List[InvoiceMatch]:
    """amount is the booking's signed amount -- positive (money coming
    into the account) looks for a still-owed outgoing Invoice;
    negative (money going out) looks for a still-owed IncomingInvoice."""
    abs_amount = abs(amount)
    matches: List[InvoiceMatch] = []

    if amount >= 0:
        result = await db.execute(
            select(Invoice).options(selectinload(Invoice.payments), selectinload(Invoice.reminders))
        )
        for invoice in result.scalars().all():
            if invoice.payment_status == "paid":
                continue
            due = Decimal(str(invoice.amount_due))
            if abs(due - abs_amount) <= AMOUNT_TOLERANCE:
                matches.append(InvoiceMatch(
                    kind="invoice", id=invoice.id,
                    label=f"{invoice.invoice_number} — {invoice.recipient_names}".replace("\n", ", "),
                    amount_due=float(due), name_matches=_names_overlap(counterparty, invoice.recipient_names),
                ))
    else:
        result = await db.execute(
            select(IncomingInvoice).options(
                selectinload(IncomingInvoice.line_items), selectinload(IncomingInvoice.payments),
            )
        )
        for incoming in result.scalars().all():
            if incoming.payment_status == "paid":
                continue
            due = Decimal(str(incoming.total_amount)) - Decimal(str(incoming.paid_total))
            if abs(due - abs_amount) <= AMOUNT_TOLERANCE:
                matches.append(InvoiceMatch(
                    kind="incoming_invoice", id=incoming.id,
                    label=f"{incoming.sender} — {incoming.invoice_number or incoming.invoice_date.isoformat()}",
                    amount_due=float(due), name_matches=_names_overlap(counterparty, incoming.sender),
                ))

    matches.sort(key=lambda m: not m.name_matches)
    return matches[:MAX_MATCH_SUGGESTIONS]


def best_match_option(matches: List[InvoiceMatch]) -> str:
    """The option value to preselect in a match dropdown -- the first
    name-matching candidate if there's exactly one, otherwise "none"
    (never guess between several plausible candidates)."""
    name_matching = [m for m in matches if m.name_matches]
    if len(name_matching) == 1:
        return name_matching[0].option_value
    return "none"
