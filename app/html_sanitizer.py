"""
Sanitizes HTML from incoming ticket emails so it can be rendered safely
in the browser.

WHY THIS MATTERS: the content comes from any arbitrary external sender
to the ticket mailbox address -- anyone can send an email there. That's
a classic stored-XSS setup if the HTML content were rendered unfiltered
(e.g. <script>, <img onerror=...>, javascript: links, hidden tracking).
Nothing from this source is ever output unfiltered with "|safe".

Two layers of defense:
1. When the email is ingested (app/ticket_mailer.py), it's already
   sanitized HERE before anything lands in the database.
2. The Jinja filter `sanitize_html` (see app/templating.py) sanitizes
   again on render -- cheap and harmless on already-clean HTML, but a
   second safety net in case some future code path lets unchecked
   content reach a template.
"""
import re

import bleach

# <script>/<style> must be removed COMPLETELY (tag AND content) --
# bleach.clean() only strips disallowed tags themselves and keeps the
# text between them (correct for e.g. a stripped <div>, but wrong for
# <script>/<style>, whose content isn't human-readable text). So these
# are removed upfront via regex instead.
_SCRIPT_STYLE_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)

# Deliberately NO images allowed: prevents both tracking pixels (the
# sender would otherwise learn when/whether the message was opened) and
# the classic <img onerror=...> trick as extra attack surface.
# Deliberately NO class/style attribute allowed: prevents CSS-based
# tricks (e.g. invisible text, spoofed UI elements) and keeps rendering
# consistent with the rest of the page.
ALLOWED_TAGS = [
    "p", "br", "b", "i", "u", "strong", "em", "a",
    "ul", "ol", "li", "blockquote", "span", "div",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "table", "thead", "tbody", "tr", "td", "th",
    "hr", "pre", "code",
]
ALLOWED_ATTRIBUTES = {
    "a": ["href", "title"],
}
ALLOWED_PROTOCOLS = ["http", "https", "mailto"]


def sanitize_email_html(html: str) -> str:
    """Sanitizes HTML from an incoming ticket email for safe rendering.
    Empty/None input yields an empty string."""
    if not html:
        return ""

    html = _SCRIPT_STYLE_RE.sub("", html)

    cleaned = bleach.clean(
        html,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        protocols=ALLOWED_PROTOCOLS,
        strip=True,
        strip_comments=True,
    )

    # Open external links in a new tab without letting the target reach
    # the ticket list via window.opener -- the content comes from an
    # untrusted sender.
    cleaned = re.sub(
        r'<a\s+href="([^"]*)"([^>]*)>',
        r'<a href="\1" target="_blank" rel="noopener noreferrer"\2>',
        cleaned,
    )
    return cleaned
