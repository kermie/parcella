"""
Issue #99: a printable birthday calendar PDF covering an entire year
(/calendar/birthdays/pdf), grouped by month -- unlike the birthdays web
page, which only lists the next 90 days for the dashboard-style
"upcoming" use case (see app/birthdays.py's upcoming_birthdays vs
birthdays_for_year).

Uses the web UI's cookie-based session login, like the other PDF-sheet
tests, since this route returns a PDF file rather than JSON.
"""
import io
from datetime import date

from pypdf import PdfReader

from app.database import AsyncSessionLocal
from app.models import Member
from tests.conftest import login, auth_header


async def web_login(client, email: str, password: str = "testpasswort123") -> None:
    response = await client.post("/auth/login", data={"email": email, "password": password})
    assert response.status_code in (302, 303)


def _pdf_text(pdf_bytes: bytes) -> str:
    reader = PdfReader(io.BytesIO(pdf_bytes))
    return "\n".join(page.extract_text() for page in reader.pages)


def _normalized(text: str) -> str:
    """WeasyPrint's font subsetting can make pypdf's text extraction
    insert stray spaces around certain letter pairs (see
    tests/test_members_signin_sheet.py)."""
    return "".join(text.split())


async def test_birthday_calendar_pdf_groups_by_month_in_calendar_order(client, admin_user):
    await web_login(client, "admin@example.com")

    async with AsyncSessionLocal() as session:
        # Deliberately created in a different order than the calendar
        # (March, then January) and with birth years far apart, to
        # prove the PDF sorts by (month, day) -- not creation order,
        # and not literal chronological date_of_birth.
        session.add_all([
            Member(first_name="Maria", last_name="Maerz", date_of_birth=date(1970, 3, 15)),
            Member(first_name="Jan", last_name="Januar", date_of_birth=date(1995, 1, 5)),
        ])
        await session.commit()

    response = await client.get("/calendar/birthdays/pdf?year=2026")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"

    text = _pdf_text(response.content)
    normalized = _normalized(text)

    assert _normalized("Jan Januar") in normalized
    assert _normalized("Maria Maerz") in normalized

    # January's entry must appear before March's entry in reading order.
    jan_index = normalized.find(_normalized("Januar"))
    mar_index = normalized.find(_normalized("Maerz"))
    assert jan_index != -1 and mar_index != -1
    assert jan_index < mar_index, "January birthdays must be listed before March birthdays"

    # Turning age is computed against the requested year (2026), not
    # today's actual year or the member's literal birth year gap.
    assert "31" in text  # Jan Januar: 2026 - 1995
    assert "56" in text  # Maria Maerz: 2026 - 1970


async def test_birthday_calendar_pdf_defaults_to_current_year_and_handles_empty(client, admin_user):
    await web_login(client, "admin@example.com")

    response = await client.get("/calendar/birthdays/pdf")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    # No members with a birth date on file at all -- must render
    # without erroring rather than crash on an empty entries list.


async def test_birthday_calendar_pdf_shows_the_universal_org_bank_footer(client, admin_user):
    """The org/register/bank footer (docs/ADR/0045) is no longer
    invoice-only -- a non-invoice, non-finances PDF must show it too,
    proving load_org_footer_context() works independently of the
    finances module."""
    token = await login(client, "admin@example.com")
    headers = auth_header(token)
    for key, value in [
        ("bank_iban", "DE89370400440532013000"),
        ("registergericht", "Amtsgericht Musterstadt"),
        ("vereinsnummer", "VR 12345"),
    ]:
        response = await client.put(f"/api/v1/club-settings/{key}", json={"value": value}, headers=headers)
        assert response.status_code == 200, response.text

    await web_login(client, "admin@example.com")

    response = await client.get("/calendar/birthdays/pdf")
    assert response.status_code == 200

    normalized = _normalized(_pdf_text(response.content))
    assert _normalized("IBAN DE89370400440532013000") in normalized
    assert _normalized("Amtsgericht Musterstadt") in normalized
    assert _normalized("VR 12345") in normalized
