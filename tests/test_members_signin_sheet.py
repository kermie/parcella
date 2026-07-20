"""
Tests for the general-meeting sign-in sheet (app/meeting_signin_sheet.py,
routes on app/routers/members.py): a PDF listing current members,
grouped by parcel, each with a signature line.

Uses the web UI's cookie-based session login (not the JWT API), since
these routes render HTML/return a PDF file rather than JSON -- same
reasoning as tests/test_calendar.py and tests/test_announcements.py.
"""
import io

from pypdf import PdfReader

from tests.conftest import login, auth_header


async def web_login(client, email: str, password: str = "testpasswort123") -> None:
    response = await client.post("/auth/login", data={"email": email, "password": password})
    assert response.status_code in (302, 303)


async def _create_resident(session, *, plot_number: str, first_name: str, last_name: str) -> None:
    from app.models import Member, Parcel, MemberParcel

    member = Member(first_name=first_name, last_name=last_name)
    parcel = Parcel(plot_number=plot_number)
    session.add_all([member, parcel])
    await session.flush()
    session.add(MemberParcel(member_id=member.id, parcel_id=parcel.id))


def _pdf_text(pdf_bytes: bytes) -> str:
    reader = PdfReader(io.BytesIO(pdf_bytes))
    return "\n".join(page.extract_text() for page in reader.pages)


def _normalized(text: str) -> str:
    """Collapses all whitespace. WeasyPrint's font subsetting can make
    pypdf's text extraction insert stray spaces around certain letter
    pairs (a text-extraction quirk, not a rendering bug -- confirmed by
    visually inspecting the actual PDF), so exact substring checks on
    raw extracted text are unreliable for names."""
    return "".join(text.split())


async def test_signin_sheet_form_shows_default_headline(client, admin_user):
    await web_login(client, "admin@example.com")

    response = await client.get("/members/signin-sheet")
    assert response.status_code == 200
    assert "General meeting on" in response.text


async def test_signin_sheet_groups_by_parcel_with_custom_headline(client, admin_user):
    await web_login(client, "admin@example.com")

    from app.database import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        await _create_resident(session, plot_number="03", first_name="Anna", last_name="Muster")
        await _create_resident(session, plot_number="01", first_name="Bernd", last_name="Beispiel")
        await session.commit()

    # Two members sharing one parcel.
    async with AsyncSessionLocal() as session:
        from app.models import Member, Parcel, MemberParcel
        from sqlalchemy import select

        result = await session.execute(select(Parcel).where(Parcel.plot_number == "01"))
        parcel = result.scalar_one()
        second_member = Member(first_name="Carla", last_name="Co-Tenant")
        session.add(second_member)
        await session.flush()
        session.add(MemberParcel(member_id=second_member.id, parcel_id=parcel.id))
        await session.commit()

    response = await client.post("/members/signin-sheet", data={"headline": "Herbstversammlung 2026"})
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"

    text = _pdf_text(response.content)
    normalized = _normalized(text)
    assert "Herbstversammlung 2026" in text
    assert _normalized("Anna Muster") in normalized
    assert _normalized("Bernd Beispiel") in normalized
    assert _normalized("Carla Co-Tenant") in normalized
    # Parcel 01 (with two members) must sort before parcel 03.
    assert normalized.index(_normalized("Bernd Beispiel")) < normalized.index(_normalized("Anna Muster"))


async def test_signin_sheet_excludes_former_residents(client, admin_user):
    await web_login(client, "admin@example.com")

    from app.database import AsyncSessionLocal
    from app.models import Member, Parcel, MemberParcel
    from datetime import date

    async with AsyncSessionLocal() as session:
        member = Member(first_name="Former", last_name="Tenant")
        parcel = Parcel(plot_number="99")
        session.add_all([member, parcel])
        await session.flush()
        session.add(MemberParcel(member_id=member.id, parcel_id=parcel.id, assigned_until=date.today()))
        await session.commit()

    response = await client.post("/members/signin-sheet", data={"headline": "Test"})
    assert response.status_code == 200
    text = _pdf_text(response.content)
    assert "Former Tenant" not in text


async def test_signin_sheet_blank_headline_falls_back_to_default(client, admin_user):
    await web_login(client, "admin@example.com")

    response = await client.post("/members/signin-sheet", data={"headline": ""})
    assert response.status_code == 200
    text = _pdf_text(response.content)
    assert "General meeting on" in text
