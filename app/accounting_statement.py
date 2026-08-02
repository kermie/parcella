"""
Cash-based accounting statement (issue #179) for the tax office: how
much cash actually moved in a calendar year, broken down by
FinanceCategory. Issue #182: neither side counts anything until it has
actually been paid -- an outgoing invoice's subtotal or an incoming
invoice's total never appears here on their own, only the payments
recorded against them.

Neither InvoicePayment nor IncomingInvoicePayment has a category of
its own -- a payment doesn't say which specific line item(s) it
settles. Instead, each payment's amount is split proportionally across
its invoice's line-item categories, weighted by each category's share
of the invoice total. InvoiceLineItem.category_id only started
existing with issue #179, and IncomingInvoicePayment only started
existing with issue #181 -- payments against invoices/positions
predating those changes, and any line item never assigned a category,
fall into the "Uncategorized" bucket. See docs/ADR/0060 and 0061 for
the reasoning and its limitations.

AccountTransaction (issue #174's generic ledger) is deliberately
excluded here -- it has no category of its own either, and folding it
in wouldn't serve this issue's "based on the categories I entered" ask
any better than leaving it off. See ADR 0060.
"""
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import (
    FinanceCategory, IncomingInvoice, IncomingInvoicePayment,
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


def _split_payment_by_category(
    payment_amount: Decimal, positions: List[Tuple[Optional[str], Decimal]],
) -> Dict[Optional[str], Decimal]:
    """Splits one payment's amount across category_id proportionally by
    each position's share of the invoice/incoming-invoice total --
    shared by both the income and expense side, since a payment never
    says which specific position(s) it settles. positions is a list of
    (category_id, amount) tuples, already normalized by the caller from
    whichever line-item shape it has (InvoiceLineItem.line_total vs.
    IncomingInvoiceLineItem.amount)."""
    totals: Dict[Optional[str], Decimal] = defaultdict(Decimal)
    total = sum((amount for _, amount in positions), Decimal("0"))
    if not positions or total <= 0:
        totals[None] += payment_amount
        return totals
    for category_id, amount in positions:
        share = (amount / total * payment_amount).quantize(Decimal("0.01"))
        totals[category_id] += share
    return totals


async def _income_totals_by_category(db: AsyncSession, year: int) -> Dict[Optional[str], Decimal]:
    result = await db.execute(
        select(InvoicePayment)
        .options(selectinload(InvoicePayment.invoice).selectinload(Invoice.line_items))
        .where(InvoicePayment.paid_on >= date(year, 1, 1), InvoicePayment.paid_on <= date(year, 12, 31))
    )
    payments = result.scalars().all()

    totals: Dict[Optional[str], Decimal] = defaultdict(Decimal)
    for payment in payments:
        positions = [(li.category_id, Decimal(str(li.line_total))) for li in payment.invoice.line_items]
        for cid, amount in _split_payment_by_category(Decimal(str(payment.amount)), positions).items():
            totals[cid] += amount
    return totals


async def _expense_totals_by_category(db: AsyncSession, year: int) -> Dict[Optional[str], Decimal]:
    result = await db.execute(
        select(IncomingInvoicePayment)
        .options(selectinload(IncomingInvoicePayment.incoming_invoice).selectinload(IncomingInvoice.line_items))
        .where(IncomingInvoicePayment.paid_on >= date(year, 1, 1), IncomingInvoicePayment.paid_on <= date(year, 12, 31))
    )
    payments = result.scalars().all()

    totals: Dict[Optional[str], Decimal] = defaultdict(Decimal)
    for payment in payments:
        positions = [(li.category_id, Decimal(str(li.amount))) for li in payment.incoming_invoice.line_items]
        for cid, amount in _split_payment_by_category(Decimal(str(payment.amount)), positions).items():
            totals[cid] += amount
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
    income_dates = (await db.execute(select(InvoicePayment.paid_on))).scalars().all()
    expense_dates = (await db.execute(select(IncomingInvoicePayment.paid_on))).scalars().all()
    years = {d.year for d in income_dates if d} | {d.year for d in expense_dates if d}
    years.add(date.today().year)
    return sorted(years, reverse=True)
