"""
Tests for the work session attendee sheet
(app/session_attendee_sheet.py, the /work-hours/sessions/{id}/attendee-sheet
route): parcel, member, hours, assigned tasks (blank if none), and a
signature line, per registered participant.

Uses the web UI's cookie-based session login (not the JWT API), since
this route returns a PDF file rather than JSON.
"""
import io
from datetime import date

from pypdf import PdfReader


async def web_login(client, email: str, password: str = "testpasswort123") -> None:
    response = await client.post("/auth/login", data={"email": email, "password": password})
    assert response.status_code in (302, 303)


def _pdf_text(pdf_bytes: bytes) -> str:
    reader = PdfReader(io.BytesIO(pdf_bytes))
    return "\n".join(page.extract_text() for page in reader.pages)


def _normalized(text: str) -> str:
    """See tests/test_members_signin_sheet.py for why: WeasyPrint's
    font subsetting can make pypdf's text extraction insert stray
    spaces around certain letter pairs."""
    return "".join(text.split())


async def _create_session_with_participants(session):
    from app.models import (
        Member, Parcel, MemberParcel, WorkSession, SessionParticipation,
        SessionType, WorkTask,
    )

    work_session = WorkSession(
        title="Herbstarbeitseinsatz", type=SessionType.STANDARD,
        date=date(2026, 10, 3), time_from="09:00", time_until="13:00",
        hours_per_participant=4.0,
    )
    session.add(work_session)

    member_with_task = Member(first_name="Anna", last_name="Muster")
    parcel_1 = Parcel(plot_number="01")
    member_no_task = Member(first_name="Bernd", last_name="Beispiel")
    parcel_2 = Parcel(plot_number="02")
    session.add_all([member_with_task, parcel_1, member_no_task, parcel_2])
    await session.flush()

    session.add(MemberParcel(member_id=member_with_task.id, parcel_id=parcel_1.id))
    session.add(MemberParcel(member_id=member_no_task.id, parcel_id=parcel_2.id))
    await session.flush()

    participation_with_task = SessionParticipation(session_id=work_session.id, member_id=member_with_task.id)
    participation_no_task = SessionParticipation(
        session_id=work_session.id, member_id=member_no_task.id, hours_completed=2.5,
    )
    session.add_all([participation_with_task, participation_no_task])
    await session.flush()

    session.add(WorkTask(
        title="Laub harken", session_id=work_session.id,
        assigned_participation_id=participation_with_task.id,
    ))
    await session.commit()

    return work_session.id


async def test_attendee_sheet_lists_parcel_hours_and_tasks(client, admin_user):
    await web_login(client, "admin@example.com")

    from app.database import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        session_id = await _create_session_with_participants(session)

    response = await client.get(f"/work-hours/sessions/{session_id}/attendee-sheet")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"

    text = _pdf_text(response.content)
    normalized = _normalized(text)

    assert "Herbstarbeitseinsatz" in text
    assert _normalized("Anna Muster") in normalized
    assert _normalized("Bernd Beispiel") in normalized
    assert "Laub harken" in text

    # Anna has a task assigned and no hours override -> falls back to
    # the session default (4.0). Bernd has an hours override (2.5) and
    # no task assigned -- the task field for his row must be blank, not
    # contain any stray task title.
    assert "4" in text
    assert "2" in text and "5" in text  # 2.5, possibly locale-formatted as "2,5"


async def test_attendee_sheet_leaves_tasks_blank_when_none_assigned(client, admin_user):
    await web_login(client, "admin@example.com")

    from app.database import AsyncSessionLocal
    from app.models import Member, Parcel, MemberParcel, WorkSession, SessionParticipation, SessionType

    async with AsyncSessionLocal() as session:
        work_session = WorkSession(title="Fruehjahrsputz", type=SessionType.STANDARD, date=date(2026, 4, 1))
        session.add(work_session)
        member = Member(first_name="Carla", last_name="Ohne-Aufgabe")
        parcel = Parcel(plot_number="03")
        session.add_all([member, parcel])
        await session.flush()
        session.add(MemberParcel(member_id=member.id, parcel_id=parcel.id))
        await session.flush()
        session.add(SessionParticipation(session_id=work_session.id, member_id=member.id))
        await session.commit()
        session_id = work_session.id

    response = await client.get(f"/work-hours/sessions/{session_id}/attendee-sheet")
    assert response.status_code == 200

    # No WorkTask exists for this session at all -- confirms the field
    # is simply empty rather than erroring or fabricating a value.
    text = _pdf_text(response.content)
    normalized = _normalized(text)
    assert _normalized("Carla Ohne-Aufgabe") in normalized


async def test_attendee_sheet_shows_parcel_for_future_dated_termination(client, admin_user):
    """Issue #130: a parcel assignment terminated with a future end date
    hasn't taken effect yet -- the parcel number must still show up on
    the attendee sheet, not be silently dropped."""
    await web_login(client, "admin@example.com")

    from datetime import timedelta
    from app.database import AsyncSessionLocal
    from app.models import Member, Parcel, MemberParcel, WorkSession, SessionParticipation, SessionType

    async with AsyncSessionLocal() as session:
        work_session = WorkSession(title="Fruehjahrsputz", type=SessionType.STANDARD, date=date(2026, 4, 1))
        session.add(work_session)
        member = Member(first_name="Dana", last_name="Notice-Given")
        parcel = Parcel(plot_number="04")
        session.add_all([member, parcel])
        await session.flush()
        session.add(MemberParcel(
            member_id=member.id, parcel_id=parcel.id,
            assigned_until=date.today() + timedelta(days=30), is_invoice_address=False,
        ))
        await session.flush()
        session.add(SessionParticipation(session_id=work_session.id, member_id=member.id))
        await session.commit()
        session_id = work_session.id

    response = await client.get(f"/work-hours/sessions/{session_id}/attendee-sheet")
    assert response.status_code == 200

    text = _pdf_text(response.content)
    normalized = _normalized(text)
    assert _normalized("Dana Notice-Given") in normalized
    assert "04" in text


async def test_attendee_sheet_404_for_unknown_session(client, admin_user):
    await web_login(client, "admin@example.com")
    response = await client.get("/work-hours/sessions/00000000-0000-0000-0000-000000000000/attendee-sheet")
    assert response.status_code == 404


async def test_attendee_sheet_table_headers_follow_configured_language(client, admin_user):
    """Issue #165: the "Parcel"/"Member"/"Hours"/"Tasks assigned"/
    "Signature" table headers were hardcoded English literals in
    app/session_attendee_sheet.py, never routed through translate() --
    unlike every other string in the PDF, which already correctly
    followed the club's configured language."""
    await web_login(client, "admin@example.com")

    from app.database import AsyncSessionLocal
    from app.models import ClubSetting

    async with AsyncSessionLocal() as session:
        session.add(ClubSetting(key="language", value="de", description="test"))
        session_id = await _create_session_with_participants(session)

    response = await client.get(f"/work-hours/sessions/{session_id}/attendee-sheet")
    assert response.status_code == 200
    text = _pdf_text(response.content).upper()
    assert "PARZELLE" in text
    assert "MITGLIED" in text
    assert "STUNDEN" in text
    assert "ZUGEWIESENE AUFGABEN" in text
    assert "UNTERSCHRIFT" in text
    assert "SIGNATURE" not in text
