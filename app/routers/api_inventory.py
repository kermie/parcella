"""
API-Router: Inventory -- categories, items, and the lending system.
"""
from datetime import date
from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models import InventoryCategory, InventoryItem, InventoryOwnerType, ItemLoan, User
from app.api_auth import get_current_api_user, require_write_access
from app.module_flags import require_module
from app.schemas import (
    InventoryCategoryOut, InventoryCategoryCreate,
    InventoryItemOut, InventoryItemCreate, InventoryItemUpdate,
    ItemLoanOut, ItemLoanCreate, ItemLoanReturn,
)

router = APIRouter(
    prefix="/api/v1/inventory",
    tags=["API: Inventory"],
    dependencies=[Depends(require_module("inventory"))],
)


# ---------------------------------------------------------------------------
# Categories
# ---------------------------------------------------------------------------

@router.get("/categories", response_model=List[InventoryCategoryOut], summary="List categories")
async def list_categories(
    db: AsyncSession = Depends(get_db), user: User = Depends(get_current_api_user),
):
    result = await db.execute(select(InventoryCategory).order_by(InventoryCategory.name))
    return result.scalars().all()


@router.post(
    "/categories", response_model=InventoryCategoryOut, status_code=status.HTTP_201_CREATED,
    summary="Create a category",
)
async def create_category(
    daten: InventoryCategoryCreate,
    db: AsyncSession = Depends(get_db), user: User = Depends(require_write_access),
):
    existing = await db.execute(select(InventoryCategory).where(InventoryCategory.name == daten.name))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="A category with this name already exists")
    category = InventoryCategory(name=daten.name, description=daten.description)
    db.add(category)
    await db.commit()
    await db.refresh(category)
    return category


@router.put("/categories/{category_id}", response_model=InventoryCategoryOut, summary="Update a category")
async def update_category(
    category_id: str, daten: InventoryCategoryCreate,
    db: AsyncSession = Depends(get_db), user: User = Depends(require_write_access),
):
    result = await db.execute(select(InventoryCategory).where(InventoryCategory.id == category_id))
    category = result.scalar_one_or_none()
    if not category:
        raise HTTPException(status_code=404, detail="Category not found")
    category.name = daten.name
    category.description = daten.description
    await db.commit()
    await db.refresh(category)
    return category


@router.delete("/categories/{category_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete a category")
async def delete_category(
    category_id: str, db: AsyncSession = Depends(get_db), user: User = Depends(require_write_access),
):
    """Items in this category are NOT deleted -- their category_id is
    just cleared (ON DELETE SET NULL), so removing a category never
    loses inventory data."""
    result = await db.execute(select(InventoryCategory).where(InventoryCategory.id == category_id))
    category = result.scalar_one_or_none()
    if not category:
        raise HTTPException(status_code=404, detail="Category not found")
    await db.delete(category)
    await db.commit()


# ---------------------------------------------------------------------------
# Items
# ---------------------------------------------------------------------------

def _item_out(item: InventoryItem) -> InventoryItemOut:
    """InventoryItemOut needs the computed quantity_on_loan/available_quantity
    properties, which from_attributes picks up fine since they're plain
    @property on the model -- this helper just makes that explicit at
    call sites rather than relying on it implicitly everywhere."""
    return InventoryItemOut.model_validate(item)


async def _reload_item(db: AsyncSession, item_id: str) -> InventoryItem:
    """Re-fetches the item with everything InventoryItemOut needs
    eager-loaded, after a commit. Deliberately NOT db.refresh(item,
    attribute_names=["loans"]): that only refreshes the one named
    relationship, but a commit expires every attribute on the object,
    so a later access of a plain column (e.g. updated_at, touched by
    onupdate=func.now()) would still trigger an async-unsafe lazy load
    -- the exact "lazy-load crash" risk already documented elsewhere in
    this project. A full re-query sidesteps the issue entirely.
    """
    result = await db.execute(
        select(InventoryItem).options(selectinload(InventoryItem.loans)).where(InventoryItem.id == item_id)
    )
    return result.scalar_one()


@router.get("/items", response_model=List[InventoryItemOut], summary="List items")
async def list_items(
    category_id: Optional[str] = Query(None),
    is_borrowable: Optional[bool] = Query(None),
    include_retired: bool = Query(False, description="Include retired items (excluded by default)"),
    db: AsyncSession = Depends(get_db), user: User = Depends(get_current_api_user),
):
    query = select(InventoryItem).options(selectinload(InventoryItem.loans)).order_by(InventoryItem.name)
    if not include_retired:
        query = query.where(InventoryItem.retired_at.is_(None))
    if category_id:
        query = query.where(InventoryItem.category_id == category_id)
    if is_borrowable is not None:
        query = query.where(InventoryItem.is_borrowable == is_borrowable)
    result = await db.execute(query)
    return [_item_out(item) for item in result.scalars().all()]


@router.get("/items/{item_id}", response_model=InventoryItemOut, summary="Get an item")
async def get_item(
    item_id: str, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_api_user),
):
    result = await db.execute(
        select(InventoryItem).options(selectinload(InventoryItem.loans)).where(InventoryItem.id == item_id)
    )
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    return _item_out(item)


def _validate_owner(owner_type: str, owner_member_id: Optional[str]) -> None:
    if owner_type == InventoryOwnerType.MEMBER.value and not owner_member_id:
        raise HTTPException(status_code=400, detail="owner_member_id is required when owner_type is MEMBER")


@router.post("/items", response_model=InventoryItemOut, status_code=status.HTTP_201_CREATED, summary="Create an item")
async def create_item(
    daten: InventoryItemCreate,
    db: AsyncSession = Depends(get_db), user: User = Depends(require_write_access),
):
    _validate_owner(daten.owner_type, daten.owner_member_id)
    item = InventoryItem(
        name=daten.name, description=daten.description, category_id=daten.category_id,
        owner_type=InventoryOwnerType(daten.owner_type),
        owner_member_id=daten.owner_member_id if daten.owner_type == InventoryOwnerType.MEMBER.value else None,
        storage_location=daten.storage_location,
        purchase_date=daten.purchase_date, purchase_price=daten.purchase_price,
        current_value=daten.current_value, current_value_updated_at=daten.current_value_updated_at,
        replacement_cost=daten.replacement_cost,
        quantity_total=daten.quantity_total, is_borrowable=daten.is_borrowable,
        default_loan_fee=daten.default_loan_fee, notes=daten.notes,
        created_by_id=user.id,
    )
    db.add(item)
    await db.commit()
    item = await _reload_item(db, item.id)
    return _item_out(item)


@router.put("/items/{item_id}", response_model=InventoryItemOut, summary="Update an item")
async def update_item(
    item_id: str, daten: InventoryItemUpdate,
    db: AsyncSession = Depends(get_db), user: User = Depends(require_write_access),
):
    result = await db.execute(
        select(InventoryItem).options(selectinload(InventoryItem.loans)).where(InventoryItem.id == item_id)
    )
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")

    _validate_owner(daten.owner_type, daten.owner_member_id)

    item.name = daten.name
    item.description = daten.description
    item.category_id = daten.category_id
    item.owner_type = InventoryOwnerType(daten.owner_type)
    item.owner_member_id = daten.owner_member_id if daten.owner_type == InventoryOwnerType.MEMBER.value else None
    item.storage_location = daten.storage_location
    item.purchase_date = daten.purchase_date
    item.purchase_price = daten.purchase_price
    item.current_value = daten.current_value
    item.current_value_updated_at = daten.current_value_updated_at
    item.replacement_cost = daten.replacement_cost
    item.quantity_total = daten.quantity_total
    item.is_borrowable = daten.is_borrowable
    item.default_loan_fee = daten.default_loan_fee
    item.notes = daten.notes

    await db.commit()
    item = await _reload_item(db, item.id)
    return _item_out(item)


@router.post("/items/{item_id}/retire", response_model=InventoryItemOut, summary="Retire an item")
async def retire_item(
    item_id: str, db: AsyncSession = Depends(get_db), user: User = Depends(require_write_access),
):
    """Marks the item as no longer owned/in service without deleting
    it -- see InventoryItem.retired_at's docstring in app/models.py for
    why this exists as a separate action from DELETE."""
    from datetime import datetime, timezone

    result = await db.execute(
        select(InventoryItem).options(selectinload(InventoryItem.loans)).where(InventoryItem.id == item_id)
    )
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    item.retired_at = datetime.now(timezone.utc)
    await db.commit()
    item = await _reload_item(db, item.id)
    return _item_out(item)


@router.delete("/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete an item")
async def delete_item(
    item_id: str, db: AsyncSession = Depends(get_db), user: User = Depends(require_write_access),
):
    """A genuine hard delete, for data-entry mistakes -- also deletes
    any loan history for this item (cascade). For an item that was
    real and is now sold/scrapped/lost, use retire instead so the
    financial and loan history survives."""
    result = await db.execute(select(InventoryItem).where(InventoryItem.id == item_id))
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    await db.delete(item)
    await db.commit()


# ---------------------------------------------------------------------------
# Loans
# ---------------------------------------------------------------------------

@router.get("/loans", response_model=List[ItemLoanOut], summary="List loans (all items)")
async def list_all_loans(
    outstanding_only: bool = Query(True, description="Only loans not yet returned (default)"),
    db: AsyncSession = Depends(get_db), user: User = Depends(get_current_api_user),
):
    """Cross-item view of who currently has what borrowed -- the board
    overview, not scoped to a single item."""
    query = select(ItemLoan).order_by(ItemLoan.borrowed_date.desc())
    if outstanding_only:
        query = query.where(ItemLoan.returned_date.is_(None))
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/items/{item_id}/loans", response_model=List[ItemLoanOut], summary="List loans for an item")
async def list_item_loans(
    item_id: str, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_api_user),
):
    result = await db.execute(
        select(ItemLoan).where(ItemLoan.item_id == item_id).order_by(ItemLoan.borrowed_date.desc())
    )
    return result.scalars().all()


@router.post(
    "/items/{item_id}/loans", response_model=ItemLoanOut, status_code=status.HTTP_201_CREATED,
    summary="Check out (borrow) some quantity of an item",
)
async def create_loan(
    item_id: str, daten: ItemLoanCreate,
    db: AsyncSession = Depends(get_db), user: User = Depends(require_write_access),
):
    result = await db.execute(
        select(InventoryItem).options(selectinload(InventoryItem.loans)).where(InventoryItem.id == item_id)
    )
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    if not item.is_borrowable:
        raise HTTPException(status_code=400, detail="This item isn't marked as borrowable")
    if daten.quantity < 1:
        raise HTTPException(status_code=400, detail="Quantity must be at least 1")
    if daten.quantity > item.available_quantity:
        raise HTTPException(
            status_code=400,
            detail=f"Only {item.available_quantity} of {item.quantity_total} available right now",
        )

    loan = ItemLoan(
        item_id=item_id, member_id=daten.member_id, quantity=daten.quantity,
        borrowed_date=daten.borrowed_date,
        fee_charged=daten.fee_charged if daten.fee_charged is not None else item.default_loan_fee,
        note=daten.note, created_by_id=user.id,
    )
    db.add(loan)
    await db.commit()
    await db.refresh(loan)
    return loan


@router.post("/loans/{loan_id}/return", response_model=ItemLoanOut, summary="Mark a loan as returned")
async def return_loan(
    loan_id: str, daten: ItemLoanReturn,
    db: AsyncSession = Depends(get_db), user: User = Depends(require_write_access),
):
    result = await db.execute(select(ItemLoan).where(ItemLoan.id == loan_id))
    loan = result.scalar_one_or_none()
    if not loan:
        raise HTTPException(status_code=404, detail="Loan not found")
    if loan.returned_date is not None:
        raise HTTPException(status_code=400, detail="This loan was already marked as returned")
    loan.returned_date = daten.returned_date or date.today()
    await db.commit()
    await db.refresh(loan)
    return loan
