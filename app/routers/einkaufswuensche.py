"""
Einkaufswünsche-Router (Web-Oberfläche): Antrag stellen, Freigeben,
Ablehnen, Deep-Link-Bestätigung durch externe Antragsteller.

Vier-Augen-Prinzip: zwei unterschiedliche Vorstandsmitglieder müssen
zustimmen, bevor ein Einkaufswunsch als genehmigt gilt. Der Antragsteller
selbst darf keine der beiden Freigaben geben.
"""
import urllib.parse
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Request, Form, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models import (
    Einkaufswunsch, EinkaufswunschFreigabe, EinkaufswunschStatus, Benutzer, BenutzerRolle,
)
from app.auth import require_user, require_admin, serializer
from app.module_flags import require_modul
from app.email_service import sende_email
from app.config import settings

router = APIRouter(
    prefix="/einkaufswuensche",
    tags=["einkaufswuensche"],
    dependencies=[Depends(require_modul("einkaufswuensche"))],
)
templates = Jinja2Templates(directory="app/templates")

_NOETIGE_FREIGABEN = 2


async def _lade_mit_details(db: AsyncSession, ew_id: str) -> Optional[Einkaufswunsch]:
    result = await db.execute(
        select(Einkaufswunsch)
        .options(
            selectinload(Einkaufswunsch.angefragt_von),
            selectinload(Einkaufswunsch.erstellt_von),
            selectinload(Einkaufswunsch.abgelehnt_von),
            selectinload(Einkaufswunsch.freigaben).selectinload(EinkaufswunschFreigabe.benutzer),
        )
        .where(Einkaufswunsch.id == ew_id)
    )
    return result.scalar_one_or_none()


# ---------------------------------------------------------------------------
# Übersicht
# ---------------------------------------------------------------------------

@router.get("/", response_class=HTMLResponse)
async def uebersicht(
    request: Request,
    filter: str = "offen",
    db: AsyncSession = Depends(get_db),
):
    benutzer = await require_user(request, db)

    query = (
        select(Einkaufswunsch)
        .options(
            selectinload(Einkaufswunsch.angefragt_von),
            selectinload(Einkaufswunsch.freigaben),
        )
        .order_by(Einkaufswunsch.erstellt_am.desc())
    )

    if filter == "offen":
        query = query.where(Einkaufswunsch.status == EinkaufswunschStatus.OFFEN)
    elif filter == "genehmigt":
        query = query.where(Einkaufswunsch.status == EinkaufswunschStatus.GENEHMIGT)
    elif filter == "abgelehnt":
        query = query.where(Einkaufswunsch.status == EinkaufswunschStatus.ABGELEHNT)
    # "alle": kein Filter

    result = await db.execute(query)
    wuensche = result.scalars().all()

    return templates.TemplateResponse("einkaufswuensche/uebersicht.html", {
        "request": request, "benutzer": benutzer,
        "wuensche": wuensche, "filter": filter,
        "noetige_freigaben": _NOETIGE_FREIGABEN,
    })


# ---------------------------------------------------------------------------
# Anlegen
# ---------------------------------------------------------------------------

@router.get("/neu", response_class=HTMLResponse)
async def neu_seite(request: Request, db: AsyncSession = Depends(get_db)):
    benutzer = await require_user(request, db)
    return templates.TemplateResponse("einkaufswuensche/formular.html", {
        "request": request, "benutzer": benutzer,
    })


@router.post("/neu")
async def erstellen(
    request: Request,
    titel: str = Form(...),
    begruendung: str = Form(...),
    link: str = Form(""),
    geschaetzte_kosten_eur: str = Form(""),
    fuer_andere_person: bool = Form(False),
    anfragender_name: str = Form(""),
    anfragender_email: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    benutzer = await require_user(request, db)

    kosten = None
    if geschaetzte_kosten_eur.strip():
        try:
            kosten = float(geschaetzte_kosten_eur.replace(",", "."))
        except ValueError:
            pass

    einkaufswunsch = Einkaufswunsch(
        titel=titel.strip(),
        begruendung=begruendung.strip(),
        link=link.strip() or None,
        geschaetzte_kosten_eur=kosten,
        erstellt_von_id=benutzer.id,
    )

    if fuer_andere_person and anfragender_email.strip():
        einkaufswunsch.anfragender_name = anfragender_name.strip() or None
        einkaufswunsch.anfragender_email = anfragender_email.strip().lower()
        einkaufswunsch.bestaetigungs_token = serializer.dumps(
            anfragender_email.strip().lower(), salt="einkaufswunsch"
        )
    else:
        einkaufswunsch.angefragt_von_id = benutzer.id

    db.add(einkaufswunsch)
    await db.flush()

    if einkaufswunsch.bestaetigungs_token:
        base_url = str(request.base_url).rstrip("/")
        bestaetigungslink = f"{base_url}/einkaufswuensche/bestaetigen/{einkaufswunsch.bestaetigungs_token}"
        betreff = f"Bitte bestätigen: Einkaufswunsch „{einkaufswunsch.titel}“"
        html = f"""
        <html><body style="font-family: sans-serif;">
        <p>Hallo {einkaufswunsch.anfragender_name or ''},</p>
        <p>{benutzer.name} hat in Ihrem Namen folgenden Einkaufswunsch im {settings.app_name} erfasst:</p>
        <p><strong>{einkaufswunsch.titel}</strong><br>{einkaufswunsch.begruendung}</p>
        <p>Bitte bestätigen Sie, dass diese Angaben korrekt sind:</p>
        <p><a href="{bestaetigungslink}" style="background: #2d6a4f; color: white; padding: 10px 20px;
           text-decoration: none; border-radius: 4px;">Angaben bestätigen</a></p>
        </body></html>
        """
        await sende_email(einkaufswunsch.anfragender_email, betreff, html, db=db)

    await db.commit()
    return RedirectResponse(f"/einkaufswuensche/{einkaufswunsch.id}", status_code=302)


# ---------------------------------------------------------------------------
# Detail
# ---------------------------------------------------------------------------

@router.get("/{ew_id}", response_class=HTMLResponse)
async def detail(ew_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    benutzer = await require_user(request, db)
    ew = await _lade_mit_details(db, ew_id)
    if not ew:
        raise HTTPException(status_code=404, detail="Einkaufswunsch nicht gefunden")

    ist_vorstand = benutzer.rolle in (BenutzerRolle.ADMIN, BenutzerRolle.VORSTAND)
    hat_bereits_freigegeben = any(f.benutzer_id == benutzer.id for f in ew.freigaben)
    ist_antragsteller = ew.angefragt_von_id == benutzer.id or ew.erstellt_von_id == benutzer.id

    return templates.TemplateResponse("einkaufswuensche/detail.html", {
        "request": request, "benutzer": benutzer, "ew": ew,
        "noetige_freigaben": _NOETIGE_FREIGABEN,
        "ist_vorstand": ist_vorstand,
        "hat_bereits_freigegeben": hat_bereits_freigegeben,
        "ist_antragsteller": ist_antragsteller,
        "EinkaufswunschStatus": EinkaufswunschStatus,
    })


# ---------------------------------------------------------------------------
# Freigeben / Ablehnen (nur Vorstand/Admin)
# ---------------------------------------------------------------------------

@router.post("/{ew_id}/freigeben")
async def freigeben(ew_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    benutzer = await require_admin(request, db)
    ew = await _lade_mit_details(db, ew_id)
    if not ew:
        raise HTTPException(status_code=404)

    if ew.status != EinkaufswunschStatus.OFFEN:
        return RedirectResponse(f"/einkaufswuensche/{ew_id}", status_code=302)

    if benutzer.id in (ew.angefragt_von_id, ew.erstellt_von_id):
        raise HTTPException(
            status_code=403,
            detail="Der Antragsteller darf seinen eigenen Einkaufswunsch nicht mitfreigeben (Vier-Augen-Prinzip)."
        )

    if any(f.benutzer_id == benutzer.id for f in ew.freigaben):
        return RedirectResponse(f"/einkaufswuensche/{ew_id}", status_code=302)

    db.add(EinkaufswunschFreigabe(einkaufswunsch_id=ew_id, benutzer_id=benutzer.id))
    await db.flush()

    neue_anzahl = len(ew.freigaben) + 1  # +1 da noch nicht neu geladen
    if neue_anzahl >= _NOETIGE_FREIGABEN:
        ew.status = EinkaufswunschStatus.GENEHMIGT
        ew.genehmigt_am = datetime.now(timezone.utc)

    await db.commit()
    return RedirectResponse(f"/einkaufswuensche/{ew_id}", status_code=302)


@router.post("/{ew_id}/ablehnen")
async def ablehnen(
    ew_id: str,
    request: Request,
    ablehnungsgrund: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    benutzer = await require_admin(request, db)
    ew = await _lade_mit_details(db, ew_id)
    if not ew:
        raise HTTPException(status_code=404)

    if ew.status != EinkaufswunschStatus.OFFEN:
        return RedirectResponse(f"/einkaufswuensche/{ew_id}", status_code=302)

    ew.status = EinkaufswunschStatus.ABGELEHNT
    ew.ablehnungsgrund = ablehnungsgrund.strip()
    ew.abgelehnt_von_id = benutzer.id
    ew.abgelehnt_am = datetime.now(timezone.utc)

    await db.commit()
    return RedirectResponse(f"/einkaufswuensche/{ew_id}", status_code=302)


# ---------------------------------------------------------------------------
# Deep-Link-Bestätigung durch externe Antragsteller (KEIN Login nötig)
# ---------------------------------------------------------------------------

@router.get("/bestaetigen/{token}", response_class=HTMLResponse)
async def bestaetigen_seite(token: str, request: Request, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Einkaufswunsch).where(Einkaufswunsch.bestaetigungs_token == token))
    ew = result.scalar_one_or_none()
    if not ew:
        return templates.TemplateResponse(
            "einkaufswuensche/bestaetigung_ungueltig.html", {"request": request}
        )

    return templates.TemplateResponse("einkaufswuensche/bestaetigen.html", {
        "request": request, "ew": ew, "token": token,
    })


@router.post("/bestaetigen/{token}")
async def bestaetigen(token: str, request: Request, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Einkaufswunsch).where(Einkaufswunsch.bestaetigungs_token == token))
    ew = result.scalar_one_or_none()
    if not ew:
        return templates.TemplateResponse(
            "einkaufswuensche/bestaetigung_ungueltig.html", {"request": request}
        )

    ew.vom_anfragenden_bestaetigt = True
    ew.vom_anfragenden_bestaetigt_am = datetime.now(timezone.utc)
    await db.commit()

    return templates.TemplateResponse("einkaufswuensche/bestaetigt.html", {
        "request": request, "ew": ew,
    })
