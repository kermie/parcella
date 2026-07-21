"""
Inventory module web router.

An asset register for what the club owns (and what members store on
club property, tracked with the same financial fields -- see
InventoryItem's docstring in app/models.py), grouped into
freely-configurable categories, plus a lending system for borrowable
items. See docs/module-inventory.md for the full design.

Viewing is available to any logged-in member (require_user) --
transparency about club assets, same permission level as the member
list. Creating/editing/retiring items, managing categories, and
checking items in/out requires admin/board (require_admin).

Route registration order matters here: /categories/, /new, and
/loans/... are all registered before the single-segment /{item_id}
catch-all, so a request like GET /inventory/categories/ can't
accidentally be swallowed by /{item_id} treating "categories" as an
item ID.
"""
from datetime import date, datetime, timezone
from typing import Optional

from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database import get_db, active_member_filter
from app.models import (
    InventoryCategory, InventoryItem, InventoryOwnerType, ItemLoan, Member,
)
from app.auth import require_user, require_admin
from app.module_flags import require_module
from app.templating import templates

router = APIRouter(
    prefix="/inventory",
    tags=["inventory"],
    dependencies=[Depends(require_module("inventory"))],
)


def _parse_decimal(value: Optional[str]) -> Optional[float]:
    value = (value or "").strip().replace(",", ".")
    return float(value) if value else None


def _parse_date(value: Optional[str]) -> Optional[date]:
    value = (value or "").strip()
    return date.fromisoformat(value) if value else None


async def _get_item_or_404(db: AsyncSession, item_id: str) -> InventoryItem:
    result = await db.execute(
        select(InventoryItem)
        .options(
            selectinload(InventoryItem.category),
            selectinload(InventoryItem.owner_member),
            selectinload(InventoryItem.loans).selectinload(ItemLoan.member),
        )
        .where(InventoryItem.id == item_id)
    )
    item = result.scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail="Item not found")
    return item


# ---------------------------------------------------------------------------
# Items -- list
# ---------------------------------------------------------------------------

@router.get("/", response_class=HTMLResponse)
async def inventory_list(
    request: Request, category_id: str = "", include_retired: bool = False,
    db: AsyncSession = Depends(get_db),
):
    user = await require_user(request, db)

    query = (
        select(InventoryItem)
        .options(selectinload(InventoryItem.category), selectinload(InventoryItem.loans))
        .order_by(InventoryItem.name)
    )
    if not include_retired:
        query = query.where(InventoryItem.retired_at.is_(None))
    if category_id:
        query = query.where(InventoryItem.category_id == category_id)
    result = await db.execute(query)
    items = result.scalars().all()

    categories_result = await db.execute(select(InventoryCategory).order_by(InventoryCategory.name))
    categories = categories_result.scalars().all()

    # Group for display: (category-or-None, [items])
    by_category = {}
    for item in items:
        by_category.setdefault(item.category, []).append(item)
    grouped = sorted(by_category.items(), key=lambda pair: (pair[0].name if pair[0] else "\uffff"))

    return templates.TemplateResponse("inventory/list.html", {
        "request": request, "user": user, "grouped": grouped, "categories": categories,
        "category_id": category_id, "include_retired": include_retired,
    })


# ---------------------------------------------------------------------------
# Categories (registered before /{item_id} -- see module docstring)
# ---------------------------------------------------------------------------

@router.get("/categories/", response_class=HTMLResponse)
async def categories_list(request: Request, db: AsyncSession = Depends(get_db)):
    user = await require_admin(request, db)
    result = await db.execute(select(InventoryCategory).order_by(InventoryCategory.name))
    categories = result.scalars().all()
    return templates.TemplateResponse("inventory/categories.html", {
        "request": request, "user": user, "categories": categories, "error": None,
    })


@router.post("/categories/new")
async def category_create(request: Request, db: AsyncSession = Depends(get_db)):
    user = await require_admin(request, db)
    form = await request.form()
    name = (form.get("name") or "").strip()
    description = (form.get("description") or "").strip() or None

    if not name:
        result = await db.execute(select(InventoryCategory).order_by(InventoryCategory.name))
        return templates.TemplateResponse("inventory/categories.html", {
            "request": request, "user": user, "categories": result.scalars().all(),
            "error": "missing_name",
        }, status_code=400)

    existing = await db.execute(select(InventoryCategory).where(InventoryCategory.name == name))
    if existing.scalar_one_or_none():
        result = await db.execute(select(InventoryCategory).order_by(InventoryCategory.name))
        return templates.TemplateResponse("inventory/categories.html", {
            "request": request, "user": user, "categories": result.scalars().all(),
            "error": "duplicate_name",
        }, status_code=400)

    db.add(InventoryCategory(name=name, description=description))
    await db.commit()
    return RedirectResponse(url="/inventory/categories/", status_code=303)


@router.post("/categories/{category_id}/delete")
async def category_delete(category_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    """Items in this category are not deleted -- see the API's
    delete_category for the same note; category_id is just cleared."""
    await require_admin(request, db)
    result = await db.execute(select(InventoryCategory).where(InventoryCategory.id == category_id))
    category = result.scalar_one_or_none()
    if category is None:
        raise HTTPException(status_code=404, detail="Category not found")
    await db.delete(category)
    await db.commit()
    return RedirectResponse(url="/inventory/categories/", status_code=303)


# ---------------------------------------------------------------------------
# Active loans overview (registered before /{item_id} for the same
# route-ordering reason as categories)
# ---------------------------------------------------------------------------

@router.get("/loans/active", response_class=HTMLResponse)
async def active_loans_overview(request: Request, db: AsyncSession = Depends(get_db)):
    """Board-wide view of everything currently checked out, across
    every item -- "who has what out right now," not scoped to one
    item's detail page."""
    user = await require_user(request, db)
    result = await db.execute(
        select(ItemLoan)
        .options(selectinload(ItemLoan.item), selectinload(ItemLoan.member))
        .where(ItemLoan.returned_date.is_(None))
        .order_by(ItemLoan.borrowed_date)
    )
    loans = result.scalars().all()
    return templates.TemplateResponse("inventory/active_loans.html", {
        "request": request, "user": user, "loans": loans,
    })


@router.post("/loans/{loan_id}/return")
async def loan_return(loan_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    await require_admin(request, db)
    result = await db.execute(select(ItemLoan).where(ItemLoan.id == loan_id))
    loan = result.scalar_one_or_none()
    if loan is None:
        raise HTTPException(status_code=404, detail="Loan not found")
    if loan.returned_date is None:
        loan.returned_date = date.today()
        await db.commit()
    return RedirectResponse(url=f"/inventory/{loan.item_id}", status_code=303)


# ---------------------------------------------------------------------------
# Items -- create / edit (registered before /{item_id})
# ---------------------------------------------------------------------------

async def _item_form_context(request, db, user, item=None, error=None):
    categories_result = await db.execute(select(InventoryCategory).order_by(InventoryCategory.name))
    members_result = await db.execute(
        select(Member).where(active_member_filter()).order_by(Member.last_name, Member.first_name)
    )
    return {
        "request": request, "user": user, "item": item, "error": error,
        "categories": categories_result.scalars().all(),
        "members": members_result.scalars().all(),
        "InventoryOwnerType": InventoryOwnerType,
    }


@router.get("/new", response_class=HTMLResponse)
async def item_new_form(request: Request, db: AsyncSession = Depends(get_db)):
    user = await require_admin(request, db)
    return templates.TemplateResponse("inventory/form.html", await _item_form_context(request, db, user))


@router.post("/new")
async def item_create(request: Request, db: AsyncSession = Depends(get_db)):
    user = await require_admin(request, db)
    form = await request.form()

    name = (form.get("name") or "").strip()
    owner_type = form.get("owner_type") or "CLUB"
    owner_member_id = (form.get("owner_member_id") or "").strip() or None

    if not name:
        return templates.TemplateResponse(
            "inventory/form.html",
            await _item_form_context(request, db, user, error="missing_name"),
            status_code=400,
        )
    if owner_type == InventoryOwnerType.MEMBER.value and not owner_member_id:
        return templates.TemplateResponse(
            "inventory/form.html",
            await _item_form_context(request, db, user, error="missing_owner_member"),
            status_code=400,
        )

    item = InventoryItem(
        name=name,
        description=(form.get("description") or "").strip() or None,
        category_id=(form.get("category_id") or "").strip() or None,
        owner_type=InventoryOwnerType(owner_type),
        owner_member_id=owner_member_id if owner_type == InventoryOwnerType.MEMBER.value else None,
        storage_location=(form.get("storage_location") or "").strip() or None,
        purchase_date=_parse_date(form.get("purchase_date")),
        purchase_price=_parse_decimal(form.get("purchase_price")),
        current_value=_parse_decimal(form.get("current_value")),
        current_value_updated_at=_parse_date(form.get("current_value_updated_at")),
        replacement_cost=_parse_decimal(form.get("replacement_cost")),
        quantity_total=int(form.get("quantity_total") or 1),
        is_borrowable=form.get("is_borrowable") == "true",
        default_loan_fee=_parse_decimal(form.get("default_loan_fee")),
        notes=(form.get("notes") or "").strip() or None,
        created_by_id=user.id,
    )
    db.add(item)
    await db.commit()
    return RedirectResponse(url=f"/inventory/{item.id}", status_code=303)


@router.get("/{item_id}/edit", response_class=HTMLResponse)
async def item_edit_form(item_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    user = await require_admin(request, db)
    item = await _get_item_or_404(db, item_id)
    return templates.TemplateResponse("inventory/form.html", await _item_form_context(request, db, user, item=item))


@router.post("/{item_id}/edit")
async def item_update(item_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    user = await require_admin(request, db)
    item = await _get_item_or_404(db, item_id)
    form = await request.form()

    name = (form.get("name") or "").strip()
    owner_type = form.get("owner_type") or "CLUB"
    owner_member_id = (form.get("owner_member_id") or "").strip() or None

    if not name:
        return templates.TemplateResponse(
            "inventory/form.html",
            await _item_form_context(request, db, user, item=item, error="missing_name"),
            status_code=400,
        )
    if owner_type == InventoryOwnerType.MEMBER.value and not owner_member_id:
        return templates.TemplateResponse(
            "inventory/form.html",
            await _item_form_context(request, db, user, item=item, error="missing_owner_member"),
            status_code=400,
        )

    item.name = name
    item.description = (form.get("description") or "").strip() or None
    item.category_id = (form.get("category_id") or "").strip() or None
    item.owner_type = InventoryOwnerType(owner_type)
    item.owner_member_id = owner_member_id if owner_type == InventoryOwnerType.MEMBER.value else None
    item.storage_location = (form.get("storage_location") or "").strip() or None
    item.purchase_date = _parse_date(form.get("purchase_date"))
    item.purchase_price = _parse_decimal(form.get("purchase_price"))
    item.current_value = _parse_decimal(form.get("current_value"))
    item.current_value_updated_at = _parse_date(form.get("current_value_updated_at"))
    item.replacement_cost = _parse_decimal(form.get("replacement_cost"))
    item.quantity_total = int(form.get("quantity_total") or 1)
    item.is_borrowable = form.get("is_borrowable") == "true"
    item.default_loan_fee = _parse_decimal(form.get("default_loan_fee"))
    item.notes = (form.get("notes") or "").strip() or None

    await db.commit()
    return RedirectResponse(url=f"/inventory/{item.id}", status_code=303)


@router.post("/{item_id}/retire")
async def item_retire(item_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    await require_admin(request, db)
    item = await _get_item_or_404(db, item_id)
    item.retired_at = datetime.now(timezone.utc)
    await db.commit()
    return RedirectResponse(url=f"/inventory/{item.id}", status_code=303)


@router.post("/{item_id}/delete")
async def item_delete(item_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    await require_admin(request, db)
    item = await _get_item_or_404(db, item_id)
    await db.delete(item)
    await db.commit()
    return RedirectResponse(url="/inventory/", status_code=303)


@router.post("/{item_id}/loans/checkout")
async def loan_checkout(item_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    user = await require_admin(request, db)
    item = await _get_item_or_404(db, item_id)
    form = await request.form()

    member_id = (form.get("member_id") or "").strip()
    quantity = int(form.get("quantity") or 1)
    borrowed_date = _parse_date(form.get("borrowed_date")) or date.today()
    fee_charged = _parse_decimal(form.get("fee_charged"))
    note = (form.get("note") or "").strip() or None

    if not item.is_borrowable:
        raise HTTPException(status_code=400, detail="This item isn't marked as borrowable")
    if not member_id:
        raise HTTPException(status_code=400, detail="A member must be selected")
    if quantity < 1 or quantity > item.available_quantity:
        raise HTTPException(
            status_code=400,
            detail=f"Only {item.available_quantity} of {item.quantity_total} available right now",
        )

    loan = ItemLoan(
        item_id=item.id, member_id=member_id, quantity=quantity, borrowed_date=borrowed_date,
        fee_charged=fee_charged if fee_charged is not None else item.default_loan_fee,
        note=note, created_by_id=user.id,
    )
    db.add(loan)
    await db.commit()
    return RedirectResponse(url=f"/inventory/{item.id}", status_code=303)


# ---------------------------------------------------------------------------
# Item detail -- MUST be registered after /new, /categories/*, /loans/*
# and /{item_id}/* above, since it's the single-segment catch-all.
# ---------------------------------------------------------------------------

@router.get("/{item_id}", response_class=HTMLResponse)
async def item_detail(item_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    user = await require_user(request, db)
    item = await _get_item_or_404(db, item_id)

    members_result = await db.execute(
        select(Member).where(active_member_filter()).order_by(Member.last_name, Member.first_name)
    )

    loans_sorted = sorted(item.loans, key=lambda loan: loan.borrowed_date, reverse=True)

    return templates.TemplateResponse("inventory/detail.html", {
        "request": request, "user": user, "item": item,
        "loans": loans_sorted, "members": members_result.scalars().all(),
        "today": date.today().isoformat(),
    })
