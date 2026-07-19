# Announcements module

Lets a board member write a single piece of club news once and push it
out to up to three channels: a blog draft on the club's CMS (e.g.
WordPress), a member email, and a printable one-page PDF notice for the
allotment area. This first delivery covers only the **foundation** --
authoring the content and its data model. The three delivery channels
are built in later phases on top of this.

## Data model

```
announcements             -- one row per piece of club news
announcement_deliveries   -- one row per (announcement, channel) send attempt
```

`Announcement` fields worth calling out:

- `body_markdown` is the single canonical source text. It is used
  as-is for **both** the blog draft and the email -- there is
  deliberately no separate per-channel text override for those two,
  since the product requirement was "same container" for blog and
  email.
- `body_html` is derived from `body_markdown` (Markdown -> HTML ->
  sanitized) and cached at save time, so it isn't re-rendered on every
  read. It is never hand-edited directly; editing always happens on
  `body_markdown`.
- `print_text_override` is the one deliberate exception to "same
  container everywhere". It starts empty (meaning: print the full
  text). Once the PDF channel is built, it will be auto-filled with a
  shortened version whenever the full text doesn't fit on one printed
  page (plus a QR code linking back to the blog post) -- but it always
  remains a real, freely hand-editable field afterward, not just a
  computed preview.
- `image_filename` is a single header/featured image, reused across
  all three channels (WordPress featured image, inline in the email,
  and in the PDF's layout).

`AnnouncementDelivery` tracks channel state rather than duplicating
content per channel: one row per (announcement, channel), upserted
rather than appended, so retrying a failed send updates the existing
row instead of creating a growing history of attempts.
`external_reference` holds whatever pointer a later phase needs back
from the channel -- currently only meaningful for BLOG, which will
store the published post's public URL once the blog channel exists (the
PRINT channel needs that URL for its QR code, which is why blog is
expected to run before print, though this isn't enforced).

## Key decisions

**Markdown as the authoring format, not a WYSIWYG rich-text editor.**
Both were viable (WYSIWYG/Quill or TipTap, both permissively licensed
and AGPL-compatible) but Markdown was chosen: lighter dependency
footprint, diff-friendly, portable if the club ever migrates off
Parcella, and it keeps `body_html` genuinely *derived* data rather than
canonical markup. The editor is EasyMDE (MIT-licensed), loaded via CDN
only on the announcement form page, with live preview.

**A separate sanitizer profile from the ticket-email sanitizer.**
`app/html_sanitizer.py`'s `sanitize_email_html()` strips all images,
because that content comes from an arbitrary external sender (anyone
can email a ticket inbox) and images there are a tracking-pixel/XSS
risk. Announcements are authored by a logged-in board member and images
are a first-class part of the content, so `app/announcement_utils.py`
uses its own allow-list (`render_markdown_to_html()`) that permits
`<img>` and a couple of extra formatting tags, while still stripping
script/style content and any `on*` handlers -- the rendered HTML is
pushed to WordPress and to every member's inbox, so it still can't be
trusted blindly just because the author is internal.

**Module flag defaults to `False`.** Same reasoning as
`public_signup_api`: this module will eventually hold outbound
credentials (a WordPress application password) and, once the email
channel is built, can send a message to every member with
`email_info = true`. A club must opt in deliberately.

**Restricted to admin/board (`require_admin`), not a general
member-facing feature.** Authoring content that gets pushed to a public
blog and to every member's inbox isn't something every logged-in member
should be able to trigger.

**No REST API for this module (so far).** Unlike most data-bearing
modules in this project, there's no `api_announcements.py` yet. This
isn't a scope decision the way Calendar's is -- it's simply not needed
until an external system needs to read/write announcements
programmatically, which so far only the (not-yet-built) WordPress
publisher will, and that's an outbound call Parcella makes, not an
inbound API surface.

## What's not built yet

- **Blog channel**: a `BlogPublisher` abstraction with a
  `WordPressPublisher` implementation (WordPress REST API, application
  password stored per club, draft-only posts), extending the existing
  Integrations page.
- **Email channel**: reuses the existing HTML email infrastructure
  (`app/ticket_mailer.py`'s approach) to send to all members with
  `email_info = true`, with batching and a per-recipient send log.
  distinct from `AnnouncementDelivery`, which tracks channel-level
  status, not per-recipient status.
- **Print channel**: a WeasyPrint-based HTML->PDF pipeline with the
  club's branding (logo/name from `app/branding.py`) as header/footer,
  a check-and-shorten loop against `print_text_override`, and a QR code
  to the blog post when shortening happens.
  `app/announcement_utils.likely_fits_one_print_page()` is a rough
  word-count heuristic used today only as a UI hint; the actual
  one-page decision in that phase will measure the real rendered PDF
  page count instead.
- Actually wiring up "send to channel X" buttons/endpoints and updating
  `AnnouncementDelivery` rows accordingly.
