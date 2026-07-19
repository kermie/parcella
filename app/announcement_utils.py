"""
Support functions for the announcements module.

Markdown -> HTML pipeline
-------------------------
body_markdown is the single canonical source, authored by a board
member in the Parcella UI (a Markdown editor with live preview). It is
converted to HTML with python-markdown and then run through bleach
before being cached as body_html and pushed out to any channel (own
templates, the blog API, the outgoing email).

This deliberately uses a *different* allow-list than
app/html_sanitizer.py's sanitize_email_html(). That function sanitizes
HTML from an untrusted external sender (anyone can email a ticket
inbox) and, among other things, strips all images as a defense against
tracking pixels and the img-onerror trick. Announcements are authored
by a logged-in board member, and images are a first-class part of the
content (the header image, and possibly inline images in the body), so
the threat model differs: we still sanitize (the rendered HTML is
pushed to WordPress and to every member's inbox, so it must be free of
script/on*-handler injection), but images and a couple of extra
formatting tags are allowed.
"""
import re

import bleach
import markdown as md

_SCRIPT_STYLE_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)

ALLOWED_TAGS = [
    "p", "br", "b", "i", "u", "strong", "em", "a",
    "ul", "ol", "li", "blockquote", "span",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "hr", "pre", "code", "img",
]
ALLOWED_ATTRIBUTES = {
    "a": ["href", "title"],
    "img": ["src", "alt", "title"],
}
ALLOWED_PROTOCOLS = ["http", "https", "mailto"]

# Rough estimate for the one-page print check (see docs/module-announcements.md
# for why this is deliberately approximate rather than exact): a printed
# A4 page with header/footer/image leaves room for roughly this many
# words of body text at a comfortable reading size. Refined once the
# PDF template exists and can be measured against directly.
APPROX_WORDS_PER_PRINT_PAGE = 350


def render_markdown_to_html(markdown_text: str) -> str:
    """Converts Markdown to sanitized HTML, safe to cache and to push to
    external channels (WordPress, email). Empty/None input yields ''."""
    if not markdown_text:
        return ""

    raw_html = md.markdown(
        markdown_text,
        extensions=["extra", "sane_lists", "nl2br"],
    )
    raw_html = _SCRIPT_STYLE_RE.sub("", raw_html)

    cleaned = bleach.clean(
        raw_html,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        protocols=ALLOWED_PROTOCOLS,
        strip=True,
        strip_comments=True,
    )
    cleaned = re.sub(
        r'<a\s+href="([^"]*)"([^>]*)>',
        r'<a href="\1" target="_blank" rel="noopener noreferrer"\2>',
        cleaned,
    )
    return cleaned


def likely_fits_one_print_page(markdown_text: str) -> bool:
    """Rough word-count heuristic used before the actual PDF is rendered,
    e.g. to warn the author in the UI. The real one-page decision (used
    to decide whether to auto-shorten with a QR code, per the PRINT
    channel design) is made by measuring the actual rendered PDF page
    count, not this estimate -- see app/print_publisher.py once the
    PDF channel is built."""
    word_count = len((markdown_text or "").split())
    return word_count <= APPROX_WORDS_PER_PRINT_PAGE
