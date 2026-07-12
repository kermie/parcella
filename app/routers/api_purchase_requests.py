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
from app.models import PurchaseRequest, PurchaseRequestApproval, PurchaseRequestStatus, Benutzer, BenutzerRolle
from app.api_auth import get_current_api_user, require_api_rolle
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
require_vorstand_api = require_api_rolle(BenutzerRolle.ADMIN, BenutzerRolle.VORSTAND)


async def _load_with_details(db: AsyncSession, request_id: str) -> Optional[PurchaseRequest]:
    result = await db.execute(
        select(PurchaseRequest)
        .options(selectinload(PurchaseRequest.approvals))
        .where(PurchaseRequest.id == request_id)
    )
    return result.scalar_one_or_none()


@router.get("", response_model=List[PurchaseRequestOut], summary="Purchase Requests auflisten")
async def purchase_requests_list(
    status_filter: Optional[str] = Query(None, alias="status"),
    db: AsyncSession = Depends(get_db),
    benutzer: Benutzer = Depends(get_current_api_user),
):
    query = select(PurchaseRequest).order_by(PurchaseRequest.created_at.desc())
    if status_filter:
        query = query.where(PurchaseRequest.status == PurchaseRequestStatus(status_filter))
    result = await db.execute(query)
    return result.scalars().all()


@router.get(
    "/{request_id}", response_model=PurchaseRequestDetailOut,
    summary="Purchase Request inkl. Freigaben abrufen",
)
async def purchase_request_get(
    request_id: str,
    db: AsyncSession = Depends(get_db),
    benutzer: Benutzer = Depends(get_current_api_user),
):
    pr = await _load_with_details(db, request_id)
    if not pr:
        raise HTTPException(status_code=404, detail="Einkaufswunsch nicht gefunden")
    return pr


@router.post(
    "", response_model=PurchaseRequestOut, status_code=status.HTTP_201_CREATED,
    summary="Purchase Request anlegen",
    description="Ohne requester_email wird der aufrufende Benutzer selbst als Antragsteller "
                "eingetragen. Mit requester_email wird ein Bestätigungslink per E-Mail "
                "verschickt (Deep-Link-Bestätigung ohne Login).",
)
async def purchase_request_create(
    daten: PurchaseRequestCreate,
    db: AsyncSession = Depends(get_db),
    benutzer: Benutzer = Depends(get_current_api_user),
):
    pr = PurchaseRequest(
        title=daten.title, justification=daten.justification, link=daten.link,
        estimated_cost_eur=daten.estimated_cost_eur,
        created_by_id=benutzer.id,
    )

    if daten.requester_email:
        from app.auth import serializer
        pr.requester_name = daten.requester_name
        pr.requester_email = str(daten.requester_email).lower()
        pr.confirmation_token = serializer.dumps(str(daten.requester_email).lower(), salt="purchase_request")
    else:
        pr.requested_by_id = benutzer.id

    db.add(pr)
    await db.commit()
    await db.refresh(pr)

    if pr.confirmation_token:
        from app.email_service import sende_email
        from app.config import settings
        betreff = f"Bitte bestätigen: Einkaufswunsch „{pr.title}“"
        html = (
            f"<html><body><p>Hallo {pr.requester_name or ''},</p>"
            f"<p>{benutzer.name} hat in Ihrem Namen einen Einkaufswunsch im {settings.app_name} erfasst: "
            f"<strong>{pr.title}</strong></p>"
            f"<p>Bitte melden Sie sich, um die Angaben zu prüfen.</p></body></html>"
        )
        await sende_email(pr.requester_email, betreff, html, db=db)

    return pr


@router.post(
    "/{request_id}/approve", response_model=PurchaseRequestDetailOut,
    summary="Freigabe erteilen",
    description="Nur Vorstand/Admin. Der Antragsteller darf nicht selbst freigeben. "
                "Bei Erreichen von 2 unterschiedlichen Freigaben wechselt der Status auf APPROVED.",
)
async def purchase_request_approve(
    request_id: str,
    db: AsyncSession = Depends(get_db),
    benutzer: Benutzer = Depends(require_vorstand_api),
):
    pr = await _load_with_details(db, request_id)
    if not pr:
        raise HTTPException(status_code=404, detail="Einkaufswunsch nicht gefunden")

    if pr.status != PurchaseRequestStatus.OPEN:
        raise HTTPException(status_code=409, detail=f"Einkaufswunsch ist bereits {pr.status.value}")

    if benutzer.id in (pr.requested_by_id, pr.created_by_id):
        raise HTTPException(
            status_code=403,
            detail="Der Antragsteller darf seinen eigenen Einkaufswunsch nicht mitfreigeben (Vier-Augen-Prinzip)."
        )

    if any(a.user_id == benutzer.id for a in pr.approvals):
        raise HTTPException(status_code=409, detail="Sie haben bereits freigegeben.")

    db.add(PurchaseRequestApproval(purchase_request_id=request_id, user_id=benutzer.id))
    await db.flush()

    if len(pr.approvals) + 1 >= _REQUIRED_APPROVALS:
        pr.status = PurchaseRequestStatus.APPROVED
        pr.approved_at = datetime.now(timezone.utc)

    await db.commit()
    return await _load_with_details(db, request_id)


@router.post(
    "/{request_id}/reject", response_model=PurchaseRequestOut,
    summary="Purchase Request ablehnen",
    description="Nur Vorstand/Admin. Eine einzelne Ablehnung genügt (Veto-Prinzip).",
)
async def purchase_request_reject(
    request_id: str,
    daten: PurchaseRequestRejectRequest,
    db: AsyncSession = Depends(get_db),
    benutzer: Benutzer = Depends(require_vorstand_api),
):
    pr = await _load_with_details(db, request_id)
    if not pr:
        raise HTTPException(status_code=404, detail="Einkaufswunsch nicht gefunden")

    if pr.status != PurchaseRequestStatus.OPEN:
        raise HTTPException(status_code=409, detail=f"Einkaufswunsch ist bereits {pr.status.value}")

    pr.status = PurchaseRequestStatus.REJECTED
    pr.rejection_reason = daten.rejection_reason
    pr.rejected_by_id = benutzer.id
    pr.rejected_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(pr)
    return pr
