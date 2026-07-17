"""
API-Router: Purchase Requests – Vier-Augen-Prinzip für Vereinsausgaben.
"""
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models import PurchaseRequest, PurchaseRequestApproval, PurchaseRequestStatus, User, UserRole
from app.api_auth import get_current_api_user, require_api_role
from app.module_flags import require_modul
from app.schemas import (
    PurchaseRequestCreate, PurchaseRequestOut, PurchaseRequestDetailOut, PurchaseRequestRejectRequest,
)

router = APIRouter(
    prefix="/api/v1/purchase-requests",
    tags=["API: Purchase Requests"],
    dependencies=[Depends(require_modul("purchase_requests"))],
)

_REQUIRED_APPROVALS = 2
require_vorstand_api = require_api_role(UserRole.ADMIN, UserRole.BOARD)


async def _load_with_details(db: AsyncSession, request_id: str) -> Optional[PurchaseRequest]:
    result = await db.execute(
        select(PurchaseRequest)
        .options(selectinload(PurchaseRequest.approvals))
        .where(PurchaseRequest.id == request_id)
    )
    return result.scalar_one_or_none()


@router.get("", response_model=List[PurchaseRequestOut], summary="List purchase requests")
async def purchase_requests_list(
    status_filter: Optional[str] = Query(None, alias="status"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_api_user),
):
    query = select(PurchaseRequest).order_by(PurchaseRequest.created_at.desc())
    if status_filter:
        query = query.where(PurchaseRequest.status == PurchaseRequestStatus(status_filter))
    result = await db.execute(query)
    return result.scalars().all()


@router.get(
    "/{request_id}", response_model=PurchaseRequestDetailOut,
    summary="Retrieve purchase request incl. approvals",
)
async def purchase_request_get(
    request_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_api_user),
):
    pr = await _load_with_details(db, request_id)
    if not pr:
        raise HTTPException(status_code=404, detail="Purchase request not found")
    return pr


@router.post(
    "", response_model=PurchaseRequestOut, status_code=status.HTTP_201_CREATED,
    summary="Create purchase request",
    description="Without requester_email, the calling user is registered as the requester "
                "themselves. With requester_email, a confirmation link is sent by email "
                "(deep-link confirmation without login).",
)
async def purchase_request_create(
    daten: PurchaseRequestCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_api_user),
):
    pr = PurchaseRequest(
        title=daten.title, justification=daten.justification, link=daten.link,
        estimated_cost_eur=daten.estimated_cost_eur,
        created_by_id=user.id,
    )

    if daten.requester_email:
        from app.auth import serializer
        pr.requester_name = daten.requester_name
        pr.requester_email = str(daten.requester_email).lower()
        pr.confirmation_token = serializer.dumps(str(daten.requester_email).lower(), salt="purchase_request")
    else:
        pr.requested_by_id = user.id

    db.add(pr)
    await db.commit()
    await db.refresh(pr)

    if pr.confirmation_token:
        from app.email_service import sende_email
        from app.config import settings
        betreff = f'Please confirm: purchase request "{pr.title}"'
        html = (
            f"<html><body><p>Hello {pr.requester_name or ''},</p>"
            f"<p>{user.name} has submitted a purchase request in {settings.app_name} on your behalf: "
            f"<strong>{pr.title}</strong></p>"
            f"<p>Please log in to review the details.</p></body></html>"
        )
        await sende_email(pr.requester_email, betreff, html, db=db)

    return pr


@router.post(
    "/{request_id}/approve", response_model=PurchaseRequestDetailOut,
    summary="Grant approval",
    description="Board/admin only. The requester may not approve their own request. "
                "Once 2 distinct approvals are reached, the status switches to APPROVED.",
)
async def purchase_request_approve(
    request_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_vorstand_api),
):
    pr = await _load_with_details(db, request_id)
    if not pr:
        raise HTTPException(status_code=404, detail="Purchase request not found")

    if pr.status != PurchaseRequestStatus.OPEN:
        raise HTTPException(status_code=409, detail=f"Einkaufswunsch ist bereits {pr.status.value}")

    if user.id in (pr.requested_by_id, pr.created_by_id):
        raise HTTPException(
            status_code=403,
            detail="The requester may not approve their own purchase request (four-eyes principle)."
        )

    if any(a.user_id == user.id for a in pr.approvals):
        raise HTTPException(status_code=409, detail="You have already approved this.")

    db.add(PurchaseRequestApproval(purchase_request_id=request_id, user_id=user.id))
    await db.flush()

    if len(pr.approvals) + 1 >= _REQUIRED_APPROVALS:
        pr.status = PurchaseRequestStatus.APPROVED
        pr.approved_at = datetime.now(timezone.utc)

    await db.commit()
    return await _load_with_details(db, request_id)


@router.post(
    "/{request_id}/reject", response_model=PurchaseRequestOut,
    summary="Reject purchase request",
    description="Board/admin only. A single rejection is enough (veto principle).",
)
async def purchase_request_reject(
    request_id: str,
    daten: PurchaseRequestRejectRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_vorstand_api),
):
    pr = await _load_with_details(db, request_id)
    if not pr:
        raise HTTPException(status_code=404, detail="Purchase request not found")

    if pr.status != PurchaseRequestStatus.OPEN:
        raise HTTPException(status_code=409, detail=f"Einkaufswunsch ist bereits {pr.status.value}")

    pr.status = PurchaseRequestStatus.REJECTED
    pr.rejection_reason = daten.rejection_reason
    pr.rejected_by_id = user.id
    pr.rejected_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(pr)
    return pr
