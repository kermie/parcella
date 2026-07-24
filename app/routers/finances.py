"""
Finances module router: annual invoices (issues #55/#56/#57/#58).

Phase 1 (#56) lives here: creating an InvoiceRun and configuring its
InvoiceItemDefinitions. Generation/PDF/preview (#57) and delivery/
payment tracking (#58) build on top of this in later phases -- see
docs/ADR and the plan this was built from.
"""
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Optional

from fastapi import APIRouter, Request, Form, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.i18n import t_for
from app.models import (
    InvoiceRun, InvoiceRunStatus, InvoiceItemDefinition, InvoiceItemDefinitionParcel,
    InvoicePricingMode, Parcel, ParcelStatus,
)
from app.permissions import require_permission
from app.module_flags import require_module

router = APIRouter(
    prefix="/finances",
    tags=["finances"],
    dependencies=[Depends(require_module("finances"))],
)
from app.templating import templates


def _parse_decimal(value: str) -> Optional[Decimal]:
    value = (value or "").strip().replace(",", ".")
    if not value:
        return None
    try:
        return Decimal(value)
    except InvalidOperation:
        return None


async def _get_run_or_404(db: AsyncSession, run_id: str) -> InvoiceRun:
    result = await db.execute(
        select(InvoiceRun)
        .options(selectinload(InvoiceRun.item_definitions).selectinload(InvoiceItemDefinition.parcel_scopes))
        .where(InvoiceRun.id == run_id)
    )
    run = result.scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404)
    return run


async def _active_parcels(db: AsyncSession) -> list:
    result = await db.execute(
        select(Parcel).where(Parcel.status == ParcelStatus.ACTIVE).order_by(Parcel.plot_number)
    )
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# Invoice runs: list, create
# ---------------------------------------------------------------------------

@router.get("/", response_class=HTMLResponse)
async def run_list(request: Request, db: AsyncSession = Depends(get_db)):
    user = await require_permission(request, db, "finances", "read")

    result = await db.execute(
        select(InvoiceRun)
        .options(selectinload(InvoiceRun.item_definitions))
        .order_by(InvoiceRun.year.desc())
    )
    runs = list(result.scalars().all())

    return templates.TemplateResponse("finances/run_list.html", {
        "request": request, "user": user, "runs": runs,
        "today": date.today().isoformat(),
        "current_year": date.today().year,
    })


@router.post("/runs")
async def run_create(
    request: Request,
    year: int = Form(...),
    subject: str = Form(...),
    issued_date: str = Form(...),
    due_date: str = Form(...),
    footer_text: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    user = await require_permission(request, db, "finances", "write")

    run = InvoiceRun(
        year=year,
        subject=subject.strip(),
        issued_date=datetime.strptime(issued_date, "%Y-%m-%d").date(),
        due_date=datetime.strptime(due_date, "%Y-%m-%d").date(),
        footer_text=footer_text.strip() or None,
        created_by_id=user.id,
    )
    db.add(run)
    await db.commit()
    return RedirectResponse(f"/finances/runs/{run.id}", status_code=302)


@router.post("/runs/{run_id}/delete")
async def run_delete(run_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    await require_permission(request, db, "finances", "delete")

    run = await _get_run_or_404(db, run_id)
    if run.status != InvoiceRunStatus.DRAFT:
        return RedirectResponse(
            f"/finances/?error={t_for(request, 'finances.errors.cannot_delete_finalized_run')}",
            status_code=302,
        )
    await db.delete(run)
    await db.commit()
    return RedirectResponse("/finances/?success=1", status_code=302)


# ---------------------------------------------------------------------------
# Invoice run detail: item definitions
# ---------------------------------------------------------------------------

@router.get("/runs/{run_id}", response_class=HTMLResponse)
async def run_detail(run_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    user = await require_permission(request, db, "finances", "read")

    run = await _get_run_or_404(db, run_id)
    parcels = await _active_parcels(db)

    next_order = (max((i.order_number for i in run.item_definitions), default=0) + 10)

    return templates.TemplateResponse("finances/run_detail.html", {
        "request": request, "user": user, "run": run, "parcels": parcels,
        "pricing_modes": list(InvoicePricingMode),
        "next_order": next_order,
    })


@router.post("/runs/{run_id}/items")
async def item_create(
    run_id: str,
    request: Request,
    order_number: int = Form(0),
    name: str = Form(...),
    description: str = Form(""),
    pricing_mode: str = Form(...),
    unit_price: str = Form(""),
    applies_to_all_parcels: str = Form(""),
    parcel_ids: list[str] = Form([]),
    db: AsyncSession = Depends(get_db),
):
    await require_permission(request, db, "finances", "write")

    run = await _get_run_or_404(db, run_id)
    if run.status != InvoiceRunStatus.DRAFT:
        raise HTTPException(status_code=400, detail=t_for(request, "finances.errors.run_not_draft"))

    try:
        mode = InvoicePricingMode(pricing_mode)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid pricing_mode")

    applies_all = applies_to_all_parcels == "on"
    item = InvoiceItemDefinition(
        invoice_run_id=run.id,
        order_number=order_number,
        name=name.strip(),
        description=description.strip() or None,
        pricing_mode=mode,
        unit_price=_parse_decimal(unit_price) if mode != InvoicePricingMode.INSURANCE_COST else None,
        applies_to_all_parcels=applies_all,
    )
    db.add(item)
    await db.flush()

    if not applies_all:
        for parcel_id in parcel_ids:
            db.add(InvoiceItemDefinitionParcel(invoice_item_definition_id=item.id, parcel_id=parcel_id))

    await db.commit()
    return RedirectResponse(f"/finances/runs/{run_id}", status_code=302)


@router.post("/runs/{run_id}/items/{item_id}/edit")
async def item_update(
    run_id: str,
    item_id: str,
    request: Request,
    order_number: int = Form(0),
    name: str = Form(...),
    description: str = Form(""),
    pricing_mode: str = Form(...),
    unit_price: str = Form(""),
    applies_to_all_parcels: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    """
    Edits the simple fields of an item definition. Parcel scoping
    (which specific parcels it applies to) is deliberately NOT editable
    here -- re-create the item to change that -- keeping this form (and
    the inline table row it's submitted from) to a manageable size.
    """
    await require_permission(request, db, "finances", "write")

    result = await db.execute(
        select(InvoiceItemDefinition).where(
            InvoiceItemDefinition.id == item_id, InvoiceItemDefinition.invoice_run_id == run_id,
        )
    )
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404)

    run = await _get_run_or_404(db, run_id)
    if run.status != InvoiceRunStatus.DRAFT:
        raise HTTPException(status_code=400, detail=t_for(request, "finances.errors.run_not_draft"))

    try:
        mode = InvoicePricingMode(pricing_mode)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid pricing_mode")

    item.order_number = order_number
    item.name = name.strip()
    item.description = description.strip() or None
    item.pricing_mode = mode
    item.unit_price = _parse_decimal(unit_price) if mode != InvoicePricingMode.INSURANCE_COST else None
    item.applies_to_all_parcels = applies_to_all_parcels == "on"

    await db.commit()
    return RedirectResponse(f"/finances/runs/{run_id}", status_code=302)


@router.post("/runs/{run_id}/items/{item_id}/delete")
async def item_delete(run_id: str, item_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    await require_permission(request, db, "finances", "delete")

    result = await db.execute(
        select(InvoiceItemDefinition).where(
            InvoiceItemDefinition.id == item_id, InvoiceItemDefinition.invoice_run_id == run_id,
        )
    )
    item = result.scalar_one_or_none()
    if item:
        await db.delete(item)
        await db.commit()
    return RedirectResponse(f"/finances/runs/{run_id}", status_code=302)
