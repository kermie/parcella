# Shared PDF page chrome (header/footer/@page) across every generator

**Context:** this app renders several different PDF documents --
annual invoices/reminders (`app/invoice_pdf.py`), a general-meeting
sign-in sheet (`app/meeting_signin_sheet.py`), a work-session attendee
sheet (`app/session_attendee_sheet.py`), the birthday calendar
(`app/birthday_calendar_pdf.py`, issue #99), and an announcement flyer
(`app/print_publisher.py`). Up through issue #99, each non-invoice
generator independently duplicated its own `PAGE_CSS`/`_build_html`
pair -- the invoice PDF's DIN-style fixed header (logo at the true
physical page edge, club name centered across the full sheet) was
never shared, only visually similar in places and outright different
in others (e.g. the meeting sign-in sheet's header sat inside the
printable area, not at the true page edge, and its "Page X of Y" text
was hardcoded in English rather than localized).

**Decision:** extracted the shared chrome into `app/pdf_chrome.py`
(`page_css()`, `header_html()`, `wrap_document()`), after being asked
directly to give every PDF "the same header and footer... from now
on." `app/invoice_pdf.py`, `app/session_attendee_sheet.py`, and
`app/meeting_signin_sheet.py` all now call into it, supplying only
their own body content and `extra_css` (invoice/reminder keep their
own three-column bank/register footer via `_footer_html`; the other
two just show the club name). `app/birthday_calendar_pdf.py`
(currently on its own not-yet-merged branch for issue #99) gets the
same treatment as a same-branch follow-up once that work lands.

**Not applied to `app/print_publisher.py`** (the announcement flyer):
that document is deliberately single-page with a different, centered
header and no page numbering at all -- a physical notice meant to be
pinned up, not a multi-page administrative document. Its own module
docstring already explains this is intentional, not an oversight, so
it was left alone rather than forced into a shared shape it was never
designed for.

**Consequences:**

- The localized "Page X of Y" translation keys moved from
  `finances.pdf.page_label`/`of_label` to a new shared top-level `pdf`
  namespace (`pdf.page_label`/`pdf.of_label`) across all 7 languages,
  since it's a generic PDF-chrome concept, not a finance-specific one.
  The old finance-scoped keys were removed outright rather than kept
  as a duplicate/alias.
- `render_session_attendee_sheet_pdf()` and
  `render_meeting_signin_sheet_pdf()` both gained a `language`
  parameter (previously neither took one at all, since their
  hardcoded-English "Page X of Y" never needed it) -- their callers
  (`app/routers/work_hours.py`, `app/routers/members.py`) now load the
  club's configured language the same way the finances/calendar
  routers already do, purely so the shared chrome's page numbering can
  actually be localized for them too.
- Fixed a small pre-existing inconsistency while touching this code:
  both sheets previously forced `file_to_data_uri(logo_path,
  "image/png")` regardless of the logo's real type; the shared
  `header_html()` lets it infer the MIME type from the file extension
  instead, matching what the invoice PDF already did correctly.
- The table column headers inside each sheet's own body content
  (`"Parcel"`, `"Member"`, `"Hours"`, ...) are still hardcoded English
  -- that pre-existing i18n gap is unrelated to page chrome and was
  deliberately left alone rather than scope-creeping this change into
  a full translation pass of those two files.
