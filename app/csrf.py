"""
CSRF protection for the cookie-authenticated web UI.

WHY, given SameSite=Lax: the session cookie (app/routers/auth.py) is
SameSite=Lax, which already stops a cross-site form POST from carrying
it. That was the app's ONLY defense, and it is a single point of failure
in a few ways worth writing down:
  * Lax still sends the cookie on top-level cross-site GET navigation,
    so the protection silently disappears the day a state-changing GET
    route is added (there is already one route that commits on GET).
  * A same-site attacker -- any other host under the same registrable
    domain, e.g. a club's own WordPress on a sibling subdomain -- is not
    cross-site as far as SameSite is concerned.
  * Older browsers, and any future change to the cookie attributes,
    remove it entirely.
This module adds the independent second factor, so neither mechanism has
to be right on its own.

MECHANISM: double-submit cookie. A random token is stored in a `csrf`
cookie and mirrored into every form as a hidden field (Jinja global
`csrf_field()`); the middleware compares the two on every unsafe
request. The comparison happens server-side, so -- unlike the classic
JS-reads-the-cookie variant -- the cookie itself stays HttpOnly and a
successful XSS still can't read the token out of it.

The token is deliberately NOT derived from the session cookie: it has to
exist before anyone logs in (the login form is a POST too, and login CSRF
is how an attacker gets a victim to act in the ATTACKER's account).

SCOPE: /api/** is exempt. Those routers authenticate with a bearer token
(app/api_auth.py) or a shared header token (app/public_api_auth.py),
never with an ambient cookie, so a cross-site request simply arrives
unauthenticated -- there is nothing for CSRF to abuse, and requiring a
token there would break every existing API client.

COST: validating a form POST means the middleware buffers the request
body in memory and parses it, and the endpoint then parses it again from
Starlette's cached copy. For the uploads in this app (avatars 2 MB,
announcement images 5 MB, backup restore 200 MB -- the only large one,
and a rare, admin-only operation) that's accepted overhead in exchange
for not having to invent a second, header-based path for file uploads
that plain HTML forms cannot use. The alternative -- putting the token
in the query string for multipart forms only -- would keep memory flat
but write a security token into every access log.
"""
import secrets

from fastapi import Request
from jinja2 import pass_context
from markupsafe import Markup, escape

COOKIE_NAME = "csrf"
FORM_FIELD = "csrf_token"
HEADER_NAME = "X-CSRF-Token"

# Only these methods change state; GET/HEAD/OPTIONS/TRACE are exempt by
# definition (RFC 9110 "safe methods").
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# Paths whose auth is not cookie-based -- see the module docstring.
EXEMPT_PREFIXES = ("/api/",)

# Lives as long as the browser session; not tied to the login session, so
# it survives login/logout without the form on screen going stale.
COOKIE_MAX_AGE = 12 * 60 * 60


def new_token() -> str:
    return secrets.token_urlsafe(32)


def is_exempt(path: str) -> bool:
    return path.startswith(EXEMPT_PREFIXES)


def tokens_match(submitted: str, expected: str) -> bool:
    if not submitted or not expected:
        return False
    return secrets.compare_digest(submitted, expected)


async def submitted_token(request: Request) -> str:
    """The token the client sent: header first (used by the few fetch()
    calls in the templates), then the hidden form field."""
    from_header = request.headers.get(HEADER_NAME)
    if from_header:
        return from_header

    content_type = request.headers.get("content-type", "")
    if not content_type.startswith(("application/x-www-form-urlencoded", "multipart/form-data")):
        return ""

    # body() before form(): reading the body caches it, both on this
    # Request and in Starlette's middleware wrapper, so the endpoint
    # further in still receives it. Parsing with form() alone consumes
    # the stream and the endpoint would see an empty body (FastAPI then
    # answers 422 for every form POST -- how this was found).
    await request.body()
    form = await request.form()
    value = form.get(FORM_FIELD)
    return value if isinstance(value, str) else ""


def token_for(request: Request) -> str:
    """The token the csrf_middleware put on this request."""
    return getattr(request.state, "csrf_token", "")


@pass_context
def jinja_csrf_token(context) -> str:
    """Registered as a Jinja global: `{{ csrf_token() }}` -- the raw
    value, for the <meta> tag that fetch() calls read."""
    request = context.get("request")
    return token_for(request) if request else ""


@pass_context
def jinja_csrf_field(context) -> Markup:
    """Registered as a Jinja global: `{{ csrf_field() }}` -- the hidden
    input every state-changing form needs."""
    request = context.get("request")
    token = token_for(request) if request else ""
    return Markup(f'<input type="hidden" name="{FORM_FIELD}" value="{escape(token)}">')
