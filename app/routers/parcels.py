"""
Parzellen-Router: Liste, Anlegen, Bearbeiten, Zuordnungen, CSV-Import/Export.
"""
import csv
import io
from datetime import date, datetime, timezone
from typing import Optional
from urllib.parse import quote as urlquote

from fastapi import APIRouter, Request, Form, Depends, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_
from sqlalchemy.orm import selectinload

from app.database import get_db, active_member_filter
from app.models import (
    Parcel, ParcelStatus, MemberParcel, Member, ChangeHistory
)
from app.auth import require_user, require_admin
from app.i18n import t_for
from app.change_tracker import ChangeTracker
from app.module_flags import require_module
from app.cloud_storage import get_nextcloud_provider, CloudStorageError
from app.parcel_cloud_folders import (
    get_active_folder, set_active_folder, deactivate_if_vacant, InvalidCloudPathError,
)

router = APIRouter(prefix="/parcels", tags=["parcels"])
from app.templating import templates


async def _get_parcel_with_details(db: AsyncSession, parcel_id: str) -> Optional[Parcel]:
    result = await db.execute(
        select(Parcel)
        .options(
            selectinload(Parcel.member_assignments).selectinload(MemberParcel.member)
        )
        .where(Parcel.id == parcel_id)
    )
    return result.scalar_one_or_none()


@router.get("/", response_class=HTMLResponse)
async def parcels_list(
    request: Request,
    search: str = "",
    status_filter: str = "",
    db: AsyncSession = Depends(get_db),
):
    user = await require_user(request, db)

    query = (
        select(Parcel)
        .options(
            selectinload(Parcel.member_assignments).selectinload(MemberParcel.member)
        )
        .order_by(Parcel.plot_number)
    )

    if search:
        query = query.where(Parcel.plot_number.ilike(f"%{search}%"))

    if status_filter and status_filter in [s.value for s in ParcelStatus]:
        query = query.where(Parcel.status == status_filter)

    result = await db.execute(query)
    parcels = result.scalars().all()

    return templates.TemplateResponse(
        "parcels/list.html",
        {
            "request": request,
            "user": user,
            "parcels": parcels,
            "search": search,
            "status_filter": status_filter,
            "ParcelStatus": ParcelStatus,
        },
    )


@router.get("/new", response_class=HTMLResponse)
async def parcel_new_page(request: Request, db: AsyncSession = Depends(get_db)):
    user = await require_user(request, db)
    return templates.TemplateResponse(
        "parcels/form.html",
        {"request": request, "user": user, "parcel": None},
    )


@router.post("/new")
async def parcel_create(
    request: Request,
    plot_number: str = Form(...),
    area_sqm: str = Form(""),
    notes: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    await require_user(request, db)

    # Check for a duplicate plot number
    existing = await db.execute(
        select(Parcel).where(Parcel.plot_number == plot_number.strip().upper())
    )
    if existing.scalar_one_or_none():
        user_result = await require_user(request, db)
        return templates.TemplateResponse(
            "parcels/form.html",
            {
                "request": request,
                "user": user_result,
                "parcel": None,
                "error": t_for(request, "parcels.form.duplicate_plot_number_error", plot_number=plot_number),
            },
            status_code=400,
        )

    flaeche = None
    if area_sqm.strip():
        try:
            flaeche = float(area_sqm.replace(",", "."))
        except ValueError:
            pass

    parcel = Parcel(
        plot_number=plot_number.strip().upper(),
        area_sqm=flaeche,
        notes=notes.strip() or None,
    )
    db.add(parcel)
    await db.commit()

    return RedirectResponse(f"/parcels/{parcel.id}", status_code=302)


@router.get("/{parcel_id}", response_class=HTMLResponse)
async def parcel_detail(
    parcel_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await require_user(request, db)
    parcel = await _get_parcel_with_details(db, parcel_id)

    if not parcel:
        raise HTTPException(status_code=404, detail=t_for(request, "parcels.errors.parcel_not_found"))

    # All members, for assignment
    mitglieder_result = await db.execute(
        select(Member)
        .where(active_member_filter())
        .order_by(Member.last_name, Member.first_name)
    )
    all_members = mitglieder_result.scalars().all()
    # Compact list for the searchable select field in the template
    # (JSON-serializable, instead of juggling map/zip in Jinja)
    all_members_json = [{"id": m.id, "name": m.full_name} for m in all_members]

    # Change history of field values
    aenderungen_result = await db.execute(
        select(ChangeHistory)
        .options(selectinload(ChangeHistory.changed_by))
        .where(
            ChangeHistory.entity_type == "Parcel",
            ChangeHistory.entity_id == parcel_id,
        )
        .order_by(ChangeHistory.changed_at.desc())
    )
    aenderungen = aenderungen_result.scalars().all()

    module_flags = getattr(request.state, "module_flags", {})
    cloud_storage_enabled = bool(module_flags.get("cloud_storage")) and user.role.value in ("admin", "board")
    cloud_folder = None
    cloud_files = None
    cloud_error = None
    if cloud_storage_enabled:
        cloud_folder = await get_active_folder(db, parcel_id)
        if cloud_folder:
            provider = await get_nextcloud_provider(db)
            if provider is None:
                cloud_error = t_for(request, "parcels.cloud_storage.not_configured")
            else:
                try:
                    cloud_files = await provider.list_files(cloud_folder.relative_path)
                except CloudStorageError as e:
                    cloud_error = str(e)
                finally:
                    await provider.aclose()

    return templates.TemplateResponse(
        "parcels/detail.html",
        {
            "request": request,
            "user": user,
            "parcel": parcel,
            "all_members": all_members,
            "all_members_json": all_members_json,
            "aenderungen": aenderungen,
            "ParcelStatus": ParcelStatus,
            "cloud_storage_enabled": cloud_storage_enabled,
            "cloud_folder": cloud_folder,
            "cloud_files": cloud_files,
            "cloud_error": cloud_error,
        },
    )


@router.get("/{parcel_id}/edit", response_class=HTMLResponse)
async def parcel_edit_page(
    parcel_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await require_user(request, db)
    parcel = await _get_parcel_with_details(db, parcel_id)

    if not parcel:
        raise HTTPException(status_code=404)

    return templates.TemplateResponse(
        "parcels/form.html",
        {"request": request, "user": user, "parcel": parcel},
    )


@router.post("/{parcel_id}/edit")
async def parcel_update(
    parcel_id: str,
    request: Request,
    plot_number: str = Form(...),
    area_sqm: str = Form(""),
    status: str = Form("ACTIVE"),
    termination_note: str = Form(""),
    notes: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    user = await require_user(request, db)
    parcel = await _get_parcel_with_details(db, parcel_id)

    if not parcel:
        raise HTTPException(status_code=404)

    tracker = ChangeTracker(
        parcel, "Parcel",
        ["plot_number", "area_sqm", "status", "termination_note", "notes"]
    )

    flaeche = None
    if area_sqm.strip():
        try:
            flaeche = float(area_sqm.replace(",", "."))
        except ValueError:
            pass

    parcel.plot_number = plot_number.strip().upper()
    parcel.area_sqm = flaeche
    parcel.notes = notes.strip() or None

    if status in [s.value for s in ParcelStatus]:
        parcel.status = ParcelStatus(status)

    parcel.termination_note = termination_note.strip() or None

    await tracker.commit(db, user.id)
    await db.commit()
    return RedirectResponse(f"/parcels/{parcel_id}", status_code=302)


@router.post("/{parcel_id}/permanently-delete")
async def parcel_permanently_delete(
    parcel_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Irrevocably deletes a parcel from the database -- unlike the
    "Deleted" status (soft-delete), which preserves history. Intended
    for accidentally created test/demo records, not for normal operation.
    """
    await require_user(request, db)

    parcel = await _get_parcel_with_details(db, parcel_id)
    if not parcel:
        raise HTTPException(status_code=404)

    await db.delete(parcel)
    await db.commit()
    import urllib.parse
    meldung = urllib.parse.quote(
        t_for(request, "parcels.list.delete_permanently_message", plot_number=parcel.plot_number)
    )
    return RedirectResponse(f"/parcels/?meldung={meldung}", status_code=302)


# ---------------------------------------------------------------------------
# Mitglieder-Zuordnung
# ---------------------------------------------------------------------------

@router.post("/{parcel_id}/member/assign")
async def member_assign(
    parcel_id: str,
    request: Request,
    member_id: str = Form(...),
    is_invoice_address: bool = Form(False),
    assigned_from: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    await require_user(request, db)

    # Bereits (auch historisch) zugeordnet?
    existing = await db.execute(
        select(MemberParcel).where(
            MemberParcel.parcel_id == parcel_id,
            MemberParcel.member_id == member_id,
        )
    )
    assignment = existing.scalar_one_or_none()

    if assignment:
        if assignment.assigned_until is None:
            # Already actively assigned, nothing to do
            return RedirectResponse(f"/parcels/{parcel_id}", status_code=302)
        # Reactivate a former (ended) assignment instead of creating a duplicate
        assignment.assigned_until = None
        assignment.assigned_from = date.fromisoformat(assigned_from) if assigned_from else date.today()
        assignment.is_invoice_address = is_invoice_address
    else:
        assignment = MemberParcel(
            parcel_id=parcel_id,
            member_id=member_id,
            is_invoice_address=is_invoice_address,
            assigned_from=date.fromisoformat(assigned_from) if assigned_from else None,
        )
        db.add(assignment)

    await db.commit()
    return RedirectResponse(f"/parcels/{parcel_id}", status_code=302)


@router.get("/{parcel_id}/member/{assignment_id}/edit", response_class=HTMLResponse)
async def member_assignment_edit_page(
    parcel_id: str,
    assignment_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = await require_user(request, db)

    result = await db.execute(
        select(MemberParcel)
        .options(selectinload(MemberParcel.member))
        .where(MemberParcel.id == assignment_id, MemberParcel.parcel_id == parcel_id)
    )
    assignment = result.scalar_one_or_none()
    if not assignment:
        raise HTTPException(status_code=404, detail=t_for(request, "parcels.errors.assignment_not_found"))

    parcel = await _get_parcel_with_details(db, parcel_id)

    return templates.TemplateResponse(
        "parcels/assignment_form.html",
        {
            "request": request,
            "user": user,
            "assignment": assignment,
            "parcel": parcel,
        },
    )


@router.post("/{parcel_id}/member/{assignment_id}/edit")
async def member_assignment_update(
    parcel_id: str,
    assignment_id: str,
    request: Request,
    is_invoice_address: bool = Form(False),
    assigned_from: str = Form(""),
    assigned_until: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    await require_user(request, db)

    result = await db.execute(
        select(MemberParcel).where(
            MemberParcel.id == assignment_id, MemberParcel.parcel_id == parcel_id
        )
    )
    assignment = result.scalar_one_or_none()
    if not assignment:
        raise HTTPException(status_code=404, detail=t_for(request, "parcels.errors.assignment_not_found"))

    assignment.assigned_from = date.fromisoformat(assigned_from) if assigned_from.strip() else None
    assignment.assigned_until = date.fromisoformat(assigned_until) if assigned_until.strip() else None
    # A former tenant (assigned_until set) can never be the invoice address.
    assignment.is_invoice_address = is_invoice_address if assignment.assigned_until is None else False

    await db.commit()
    await deactivate_if_vacant(db, parcel_id)
    return RedirectResponse(f"/parcels/{parcel_id}", status_code=302)


@router.post("/{parcel_id}/member/{assignment_id}/remove")
async def member_remove(
    parcel_id: str,
    assignment_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Ends a tenant assignment (sets assigned_until), but does NOT delete
    it from the database -- so the history stays intact (who was a
    tenant of this parcel, from when to when).
    """
    await require_user(request, db)
    result = await db.execute(
        select(MemberParcel).where(
            MemberParcel.id == assignment_id,
            MemberParcel.parcel_id == parcel_id,
        )
    )
    assignment = result.scalar_one_or_none()
    if assignment and assignment.assigned_until is None:
        assignment.assigned_until = date.today()
        assignment.is_invoice_address = False
        await db.commit()
        await deactivate_if_vacant(db, parcel_id)
    return RedirectResponse(f"/parcels/{parcel_id}", status_code=302)


@router.post("/{parcel_id}/member/{assignment_id}/delete-history")
async def former_assignment_delete(
    parcel_id: str,
    assignment_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Fully deletes a historical (already-ended) tenant entry from the
    database -- unlike /remove above, which only sets assigned_until
    and deliberately preserves history. For genuine data cleanup (a
    typo in the assignment, an accidental duplicate assignment, etc.),
    not for a normal "tenant change." Admin/board only. Only for
    already-ended assignments (assigned_until set) -- an active
    assignment must first be ended via /remove, so this endpoint can't
    be used as a bypass for that.
    """
    await require_admin(request, db)
    result = await db.execute(
        select(MemberParcel).where(
            MemberParcel.id == assignment_id,
            MemberParcel.parcel_id == parcel_id,
        )
    )
    assignment = result.scalar_one_or_none()
    if not assignment:
        raise HTTPException(status_code=404, detail=t_for(request, "parcels.errors.assignment_not_found"))
    if assignment.assigned_until is None:
        raise HTTPException(
            status_code=400,
            detail=t_for(request, "parcels.detail.cannot_delete_active_assignment"),
        )

    await db.delete(assignment)
    await db.commit()
    return RedirectResponse(f"/parcels/{parcel_id}", status_code=302)


# ---------------------------------------------------------------------------
# Cloud storage connector (Nextcloud): board/admin browse, upload, and
# download for a parcel's configured folder. See app/cloud_storage.py
# and app/parcel_cloud_folders.py. Board/admin only -- member access to
# the actual files is granted separately, directly in Nextcloud.
# ---------------------------------------------------------------------------

@router.post("/{parcel_id}/cloud-folder", dependencies=[Depends(require_module("cloud_storage"))])
async def parcel_cloud_folder_set(
    parcel_id: str,
    request: Request,
    relative_path: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    user = await require_admin(request, db)
    try:
        await set_active_folder(db, parcel_id, relative_path, set_by_user_id=user.id)
    except InvalidCloudPathError as e:
        message = urlquote(str(e))
        return RedirectResponse(f"/parcels/{parcel_id}?cloud_error={message}", status_code=303)
    return RedirectResponse(f"/parcels/{parcel_id}?cloud_folder_saved=1", status_code=303)


@router.post("/{parcel_id}/cloud-folder/upload", dependencies=[Depends(require_module("cloud_storage"))])
async def parcel_cloud_file_upload(
    parcel_id: str,
    request: Request,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    await require_admin(request, db)

    folder = await get_active_folder(db, parcel_id)
    if not folder:
        raise HTTPException(status_code=404, detail=t_for(request, "parcels.detail.cloud_no_active_folder"))

    provider = await get_nextcloud_provider(db)
    if provider is None:
        raise HTTPException(status_code=400, detail=t_for(request, "parcels.detail.cloud_not_configured"))

    try:
        content = await file.read()
        await provider.upload_file(folder.relative_path, file.filename, content)
    except CloudStorageError as e:
        message = urlquote(str(e))
        return RedirectResponse(f"/parcels/{parcel_id}?cloud_error={message}", status_code=303)
    finally:
        await provider.aclose()

    return RedirectResponse(f"/parcels/{parcel_id}?cloud_upload_ok=1", status_code=303)


@router.get("/{parcel_id}/cloud-folder/download", dependencies=[Depends(require_module("cloud_storage"))])
async def parcel_cloud_file_download(
    parcel_id: str,
    request: Request,
    filename: str,
    db: AsyncSession = Depends(get_db),
):
    await require_admin(request, db)

    folder = await get_active_folder(db, parcel_id)
    if not folder:
        raise HTTPException(status_code=404, detail=t_for(request, "parcels.detail.cloud_no_active_folder"))

    provider = await get_nextcloud_provider(db)
    if provider is None:
        raise HTTPException(status_code=400, detail=t_for(request, "parcels.detail.cloud_not_configured"))

    try:
        content = await provider.download_file(folder.relative_path, filename)
    except CloudStorageError as e:
        raise HTTPException(status_code=502, detail=str(e))
    finally:
        await provider.aclose()

    return Response(
        content=content, media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{urlquote(filename)}"'},
    )


# ---------------------------------------------------------------------------
# CSV-Export
# ---------------------------------------------------------------------------

@router.get("/export/csv")
async def parcels_export_csv(request: Request, db: AsyncSession = Depends(get_db)):
    await require_user(request, db)

    result = await db.execute(
        select(Parcel)
        .options(
            selectinload(Parcel.member_assignments).selectinload(MemberParcel.member)
        )
        .order_by(Parcel.plot_number)
    )
    parcels = result.scalars().all()

    output = io.StringIO()
    writer = csv.writer(output, delimiter=";")
    writer.writerow([
        "Gartennummer", "Fläche (qm)", "Status",
        "Kündigungsnotiz",
        "Mitglieder (Rechnungsadresse zuerst)", "Notizen"
    ])

    for p in parcels:
        mitglieder_str = "; ".join(
            f"{z.member.full_name}{'*' if z.is_invoice_address else ''}"
            for z in sorted(p.member_assignments, key=lambda z: (not z.is_invoice_address, z.member.full_name))
        )
        writer.writerow([
            p.plot_number,
            str(p.area_sqm).replace(".", ",") if p.area_sqm else "",
            p.status.value,
            p.termination_note or "",
            mitglieder_str,
            p.notes or "",
        ])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=parcels.csv"},
    )


# ---------------------------------------------------------------------------
# CSV-Import
# ---------------------------------------------------------------------------

@router.post("/import/csv")
async def parcels_import_csv(
    request: Request,
    datei: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    await require_user(request, db)

    inhalt = await datei.read()
    try:
        text = inhalt.decode("utf-8-sig")  # BOM-safe
    except UnicodeDecodeError:
        text = inhalt.decode("latin-1")

    try:
        delimiter = csv.Sniffer().sniff(text[:2048], delimiters=";,").delimiter
    except csv.Error:
        delimiter = ";"

    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    if reader.fieldnames:
        reader.fieldnames = [f.strip() if f else f for f in reader.fieldnames]

    erstellt = 0
    uebersprungen = 0
    fehlende_gartennummer = 0

    for zeile in reader:
        plot_number = (zeile.get("Gartennummer") or "").strip().upper()
        if not plot_number:
            fehlende_gartennummer += 1
            continue

        existing = await db.execute(
            select(Parcel).where(Parcel.plot_number == plot_number)
        )
        if existing.scalar_one_or_none():
            uebersprungen += 1
            continue

        flaeche = None
        flaeche_str = (zeile.get("Fläche (qm)") or "").replace(",", ".").strip()
        if flaeche_str:
            try:
                flaeche = float(flaeche_str)
            except ValueError:
                pass

        status_str = (zeile.get("Status") or "ACTIVE").strip().upper()
        status = ParcelStatus.ACTIVE
        if status_str in [s.value for s in ParcelStatus]:
            status = ParcelStatus(status_str)

        parcel = Parcel(
            plot_number=plot_number,
            area_sqm=flaeche,
            status=status,
            notes=(zeile.get("Notizen") or "").strip() or None,
        )
        db.add(parcel)
        erstellt += 1

    await db.commit()

    meldung = t_for(request, "parcels.list.csv_import_summary", created=erstellt, skipped=uebersprungen)
    if fehlende_gartennummer:
        meldung += t_for(request, "parcels.list.csv_import_missing_plot_number", count=fehlende_gartennummer)

    import urllib.parse
    return RedirectResponse(
        f"/parcels/?meldung={urllib.parse.quote(meldung)}",
        status_code=302,
    )
