"""
Cash-based accounting statement (issue #179) for the tax office: how
much cash actually moved in a calendar year, broken down by
FinanceCategory.

Expenses come straight from IncomingInvoiceLineItem (already
categorized per position since issue #178), using the incoming
invoice's invoice_date as the cash-out date -- there's no separate
payment-date tracking for incoming invoices, so this assumes a bill is
paid when recorded.

Income is trickier: InvoicePayment (money actually received against a
club invoice) has no category of its own -- a payment doesn't say
which specific line item(s) it settles. Instead, each payment's amount
is split proportionally across its invoice's line-item categories,
weighted by each category's share of the invoice subtotal.
InvoiceLineItem only started carrying category_id with issue #179;
payments against invoices finalized before that change -- and any line
item never assigned a category -- fall into the "Uncategorized"
bucket. See docs/ADR/0060 for the reasoning and its limitations.

AccountTransaction (issue #174's generic ledger) is deliberately
excluded here -- it has no category of its own either, and folding it
in wouldn't serve this issue's "based on the categories I entered" ask
any better than leaving it off. See ADR 0060.
"""
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import (
    FinanceCategory, IncomingInvoice, IncomingInvoiceLineItem,
    Invoice, InvoicePayment,
)


@dataclass
class CategoryAmount:
    category: Optional[FinanceCategory]
    amount: float


@dataclass
class CashAccountingStatement:
    year: int
    income_by_category: List[CategoryAmount]
    income_total: float
    expense_by_category: List[CategoryAmount]
    expense_total: float

    @property
    def net_result(self) -> float:
        return self.income_total - self.expense_total


async def _income_totals_by_category(db: AsyncSession, year: int) -> Dict[Optional[str], Decimal]:
    result = await db.execute(
        select(InvoicePayment)
        .options(selectinload(InvoicePayment.invoice).selectinload(Invoice.line_items))
        .where(InvoicePayment.paid_on >= date(year, 1, 1), InvoicePayment.paid_on <= date(year, 12, 31))
    )
    payments = result.scalars().all()

    totals: Dict[Optional[str], Decimal] = defaultdict(Decimal)
    for payment in payments:
        line_items = payment.invoice.line_items
        subtotal = sum((Decimal(str(li.line_total)) for li in line_items), Decimal("0"))
        payment_amount = Decimal(str(payment.amount))
        if not line_items or subtotal <= 0:
            totals[None] += payment_amount
            continue
        for li in line_items:
            share = (Decimal(str(li.line_total)) / subtotal * payment_amount).quantize(Decimal("0.01"))
            totals[li.category_id] += share
    return totals


async def _expense_totals_by_category(db: AsyncSession, year: int) -> Dict[Optional[str], Decimal]:
    result = await db.execute(
        select(IncomingInvoiceLineItem)
        .join(IncomingInvoice, IncomingInvoiceLineItem.incoming_invoice_id == IncomingInvoice.id)
        .where(IncomingInvoice.invoice_date >= date(year, 1, 1), IncomingInvoice.invoice_date <= date(year, 12, 31))
    )
    line_items = result.scalars().all()

    totals: Dict[Optional[str], Decimal] = defaultdict(Decimal)
    for li in line_items:
        totals[li.category_id] += Decimal(str(li.amount))
    return totals


async def compute_cash_accounting_statement(db: AsyncSession, year: int) -> CashAccountingStatement:
    income_totals = await _income_totals_by_category(db, year)
    expense_totals = await _expense_totals_by_category(db, year)

    category_ids = {cid for cid in (*income_totals.keys(), *expense_totals.keys()) if cid}
    categories_by_id: Dict[str, FinanceCategory] = {}
    if category_ids:
        result = await db.execute(select(FinanceCategory).where(FinanceCategory.id.in_(category_ids)))
        categories_by_id = {c.id: c for c in result.scalars().all()}

    def _rows(totals: Dict[Optional[str], Decimal]) -> List[CategoryAmount]:
        rows = [
            CategoryAmount(category=categories_by_id.get(cid) if cid else None, amount=float(amount))
            for cid, amount in totals.items()
            if amount != 0
        ]
        rows.sort(key=lambda r: (r.category is None, r.category.code if r.category else ""))
        return rows

    income_rows = _rows(income_totals)
    expense_rows = _rows(expense_totals)

    return CashAccountingStatement(
        year=year,
        income_by_category=income_rows,
        income_total=sum((r.amount for r in income_rows), 0.0),
        expense_by_category=expense_rows,
        expense_total=sum((r.amount for r in expense_rows), 0.0),
    )


async def available_statement_years(db: AsyncSession) -> List[int]:
    payment_dates = (await db.execute(select(InvoicePayment.paid_on))).scalars().all()
    invoice_dates = (await db.execute(select(IncomingInvoice.invoice_date))).scalars().all()
    years = {d.year for d in payment_dates if d} | {d.year for d in invoice_dates if d}
    years.add(date.today().year)
    return sorted(years, reverse=True)
