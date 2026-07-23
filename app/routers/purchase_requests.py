"""
Purchase Requests router (web UI): submit a request, approve, reject,
deep-link confirmation by external requesters.

Four-eyes principle: two different board members must agree before a
PurchaseRequest counts as approved. The requester themselves may not
give either of the two approvals.
"""
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Request, Form, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models import (
    PurchaseRequest, PurchaseRequestApproval, PurchaseRequestStatus, User,
)
from app.auth import require_admin, serializer
from app.permissions import require_permission
from app.i18n import t_for
from app.module_flags import require_module
from app.email_service import send_email
from app.config import settings

router = APIRouter(
    prefix="/purchase-requests",
    tags=["purchase-requests"],
    dependencies=[Depends(require_module("purchase_requests"))],
)
from app.templating import templates

_REQUIRED_APPROVALS = 2


async def _load_with_details(db: AsyncSession, request_id: str) -> Optional[PurchaseRequest]:
    result = await db.execute(
        select(PurchaseRequest)
        .options(
            selectinload(PurchaseRequest.requested_by),
            selectinload(PurchaseRequest.created_by),
            selectinload(PurchaseRequest.rejected_by),
            selectinload(PurchaseRequest.approvals).selectinload(PurchaseRequestApproval.user),
        )
        .where(PurchaseRequest.id == request_id)
    )
    return result.scalar_one_or_none()


# ---------------------------------------------------------------------------
# Overview
# ---------------------------------------------------------------------------

@router.get("/", response_class=HTMLResponse)
async def purchase_requests_overview(
    request: Request,
    filter: str = "open",
    db: AsyncSession = Depends(get_db),
):
    user = await require_permission(request, db, "purchase_requests", "read")

    query = (
        select(PurchaseRequest)
        .options(
            selectinload(PurchaseRequest.requested_by),
            selectinload(PurchaseRequest.approvals),
        )
        .order_by(PurchaseRequest.created_at.desc())
    )

    if filter == "open":
        query = query.where(PurchaseRequest.status == PurchaseRequestStatus.OPEN)
    elif filter == "approved":
        query = query.where(PurchaseRequest.status == PurchaseRequestStatus.APPROVED)
    elif filter == "rejected":
        query = query.where(PurchaseRequest.status == PurchaseRequestStatus.REJECTED)
    # "all": no filter

    result = await db.execute(query)
    purchase_requests = result.scalars().all()

    return templates.TemplateResponse("purchase_requests/overview.html", {
        "request": request, "user": user,
        "purchase_requests": purchase_requests, "filter": filter,
        "required_approvals": _REQUIRED_APPROVALS,
    })


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------

@router.get("/new", response_class=HTMLResponse)
async def purchase_request_new_page(request: Request, db: AsyncSession = Depends(get_db)):
    user = await require_permission(request, db, "purchase_requests", "write")
    return templates.TemplateResponse("purchase_requests/form.html", {
        "request": request, "user": user,
    })


@router.post("/new")
async def purchase_request_create(
    request: Request,
    title: str = Form(...),
    justification: str = Form(...),
    link: str = Form(""),
    estimated_cost_eur: str = Form(""),
    for_other_person: bool = Form(False),
    requester_name: str = Form(""),
    requester_email: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    user = await require_permission(request, db, "purchase_requests", "write")

    estimated_cost = None
    if estimated_cost_eur.strip():
        try:
            estimated_cost = float(estimated_cost_eur.replace(",", "."))
        except ValueError:
            pass

    purchase_request = PurchaseRequest(
        title=title.strip(),
        justification=justification.strip(),
        link=link.strip() or None,
        estimated_cost_eur=estimated_cost,
        created_by_id=user.id,
    )

    if for_other_person and requester_email.strip():
        purchase_request.requester_name = requester_name.strip() or None
        purchase_request.requester_email = requester_email.strip().lower()
        purchase_request.confirmation_token = serializer.dumps(
            requester_email.strip().lower(), salt="purchase_request"
        )
    else:
        purchase_request.requested_by_id = user.id

    db.add(purchase_request)
    await db.flush()

    if purchase_request.confirmation_token:
        base_url = str(request.base_url).rstrip("/")
        confirmation_link = f"{base_url}/purchase-requests/confirm/{purchase_request.confirmation_token}"
        subject = t_for(request, "email.purchase_request_confirm.subject", title=purchase_request.title)
        html = f"""
        <html><body style="font-family: sans-serif;">
        <p>{t_for(request, "email.purchase_request_confirm.greeting", name=purchase_request.requester_name or '')}</p>
        <p>{t_for(request, "email.purchase_request_confirm.body", admin_name=user.name, app_name=settings.app_name)}</p>
        <p><strong>{purchase_request.title}</strong><br>{purchase_request.justification}</p>
        <p>{t_for(request, "email.purchase_request_confirm.instruction")}</p>
        <p><a href="{confirmation_link}" style="background: #2d6a4f; color: white; padding: 10px 20px;
           text-decoration: none; border-radius: 4px;">{t_for(request, "email.purchase_request_confirm.button")}</a></p>
        </body></html>
        """
        await send_email(purchase_request.requester_email, subject, html, db=db)

    await db.commit()
    return RedirectResponse(f"/purchase-requests/{purchase_request.id}", status_code=302)


# ---------------------------------------------------------------------------
# Detail
# ---------------------------------------------------------------------------

@router.get("/{request_id}", response_class=HTMLResponse)
async def purchase_request_detail(request_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    user = await require_permission(request, db, "purchase_requests", "read")
    pr = await _load_with_details(db, request_id)
    if not pr:
        raise HTTPException(status_code=404, detail=t_for(request, "errors.purchase_request_not_found"))

    is_board_member = getattr(request.state, "is_full_access", False)
    has_already_approved = any(a.user_id == user.id for a in pr.approvals)
    is_requester = pr.requested_by_id == user.id or pr.created_by_id == user.id

    return templates.TemplateResponse("purchase_requests/detail.html", {
        "request": request, "user": user, "pr": pr,
        "required_approvals": _REQUIRED_APPROVALS,
        "is_board_member": is_board_member,
        "has_already_approved": has_already_approved,
        "is_requester": is_requester,
        "PurchaseRequestStatus": PurchaseRequestStatus,
    })


# ---------------------------------------------------------------------------
# Approve / Reject (board/admin only)
# ---------------------------------------------------------------------------

@router.post("/{request_id}/approve")
async def purchase_request_approve(request_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    user = await require_admin(request, db)
    pr = await _load_with_details(db, request_id)
    if not pr:
        raise HTTPException(status_code=404)

    if pr.status != PurchaseRequestStatus.OPEN:
        return RedirectResponse(f"/purchase-requests/{request_id}", status_code=302)

    if user.id in (pr.requested_by_id, pr.created_by_id):
        raise HTTPException(
            status_code=403,
            detail=t_for(request, "errors.requester_cannot_self_approve")
        )

    if any(a.user_id == user.id for a in pr.approvals):
        return RedirectResponse(f"/purchase-requests/{request_id}", status_code=302)

    db.add(PurchaseRequestApproval(purchase_request_id=request_id, user_id=user.id))
    await db.flush()

    new_count = len(pr.approvals) + 1  # +1 since not yet reloaded
    if new_count >= _REQUIRED_APPROVALS:
        pr.status = PurchaseRequestStatus.APPROVED
        pr.approved_at = datetime.now(timezone.utc)

    await db.commit()
    return RedirectResponse(f"/purchase-requests/{request_id}", status_code=302)


@router.post("/{request_id}/reject")
async def purchase_request_reject(
    request_id: str,
    request: Request,
    rejection_reason: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    user = await require_admin(request, db)
    pr = await _load_with_details(db, request_id)
    if not pr:
        raise HTTPException(status_code=404)

    if pr.status != PurchaseRequestStatus.OPEN:
        return RedirectResponse(f"/purchase-requests/{request_id}", status_code=302)

    pr.status = PurchaseRequestStatus.REJECTED
    pr.rejection_reason = rejection_reason.strip()
    pr.rejected_by_id = user.id
    pr.rejected_at = datetime.now(timezone.utc)

    await db.commit()
    return RedirectResponse(f"/purchase-requests/{request_id}", status_code=302)


# ---------------------------------------------------------------------------
# Deep-link confirmation by external requesters (NO login needed)
# ---------------------------------------------------------------------------

@router.get("/confirm/{token}", response_class=HTMLResponse)
async def confirm_page(token: str, request: Request, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(PurchaseRequest).where(PurchaseRequest.confirmation_token == token))
    pr = result.scalar_one_or_none()
    if not pr:
        return templates.TemplateResponse(
            "purchase_requests/confirmation_invalid.html", {"request": request}
        )

    return templates.TemplateResponse("purchase_requests/confirm.html", {
        "request": request, "pr": pr, "token": token,
    })


@router.post("/confirm/{token}")
async def confirm(token: str, request: Request, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(PurchaseRequest).where(PurchaseRequest.confirmation_token == token))
    pr = result.scalar_one_or_none()
    if not pr:
        return templates.TemplateResponse(
            "purchase_requests/confirmation_invalid.html", {"request": request}
        )

    pr.confirmed_by_requester = True
    pr.confirmed_by_requester_at = datetime.now(timezone.utc)
    await db.commit()

    return templates.TemplateResponse("purchase_requests/confirmed.html", {
        "request": request, "pr": pr,
    })
