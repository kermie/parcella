# Universal PDF footer, and the flyer joins the shared chrome

**Context:** [ADR 0043](./0043-shared-pdf-page-chrome.md) unified the
*visual chrome* (header, colors, "Page X of Y") across every formal PDF
except the announcement flyer (`app/print_publisher.py`), which was
deliberately left out as a differently-shaped, single-page document --
and even among the documents that did share the chrome, only
invoices/reminders showed the three-column organization/register-
court/bank footer; everything else just showed the club name. Asked
directly to bring the flyer in line too, then asked to streamline the
footer itself the same way -- and, when given the choice between
"organization identity + register info only" and "the exact same
footer as invoices, bank details included," the explicit answer was
the latter: full consistency across every generated PDF.

**Decision:**

1. **The org/register/bank footer is now universal, not invoice-only.**
   `OrgFooterContext` and `org_footer_html()` moved from
   `app/invoice_pdf.py`'s local `_footer_html()` into
   `app/pdf_chrome.py`, and every generator -- invoices/reminders, the
   meeting sign-in sheet, the work-session attendee sheet, the birthday
   calendar, and the announcement flyer -- now builds its footer from
   the same `OrgFooterContext`. Each field that's blank (e.g. a club
   that hasn't entered bank details yet) simply omits that line, same
   behavior invoices already had.

2. **`load_org_footer_context()` reads `ClubSetting` directly, not
   through the finances module.** This address/register/bank data is
   club-identity/legal information, not finances-owned -- it happened
   to only be loaded by `app/routers/finances.py`'s `_pdf_context()`
   before because invoices were the only consumer. Every PDF-producing
   router needs it now regardless of which optional modules (finances
   included) a given club has enabled, so the loader lives in
   `app/pdf_chrome.py` instead and finances.py's own `_pdf_context()`
   now calls it too rather than duplicating the query.

3. **The flyer (`app/print_publisher.py`) now uses `wrap_document()`
   like every other document**, replacing its own `@top-center`/
   `@bottom-center`-based `PAGE_CSS`/`_build_html` pair -- supersedes
   ADR 0043's exclusion. It keeps its own larger body font
   (`11pt`/`1.45` line-height vs. the `10.5pt` the tabular documents
   use) as `extra_css`, since a physical pin-up notice still wants to
   read differently from a dense administrative table, and it's still
   always exactly one page (the paragraph-shortening loop is
   unaffected by the chrome swap). It now also shows organization
   identity, register info, and bank details in its footer, and
   "Page 1 of 1" -- the tradeoff of putting bank details on a document
   meant for public posting was raised explicitly before building this,
   and full consistency was still the chosen answer.

4. **`render_announcement_print_pdf()` gained a `language` parameter**
   (it never had one -- its old hardcoded chrome never needed page-
   number localization); its caller
   (`app/routers/announcements.py`) now loads the club's language the
   same way every other PDF route already does.

5. **`finances.pdf.account_holder` moved to the shared top-level `pdf`
   namespace** as `pdf.account_holder`, following the precedent ADR
   0043 already set for `page_label`/`of_label`: it's a generic PDF-
   footer string now, not finance-specific. Old key removed outright,
   no alias kept, across all 7 languages.

**Consequences:**

- `render_invoice_pdf`/`render_invoice_bundle_pdf`/`render_reminder_pdf`
  and the three simpler generators' `render_*_pdf()` functions all
  take a single `footer_context: OrgFooterContext` parameter now
  instead of a loose `club_name` (and, for the invoice functions, seven
  more individual address/bank/register params) -- every call site
  builds it via `load_org_footer_context(db, branding["club_name"])`
  right after the existing `load_branding(db)` call.
- `app/routers/finances.py`'s `_pdf_context()` dict lost its
  `club_name`/`club_address_lines`/`bank_*`/`register_*` keys (now all
  inside the one `footer_context` key); `app/invoice_delivery.py`,
  which read `pdf_context["club_name"]` directly for email subject/body
  text (not the PDF itself), now reads
  `pdf_context["footer_context"].club_name` instead.
- `wrap_document()`'s default `bottom_margin` changed from `2.2cm` to
  `2.6cm` (the height every document's three-column footer needs now),
  removing the need for the explicit override invoices and the
  birthday calendar previously passed.
