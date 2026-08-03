# CSRF protection (double-submit cookie) and security response headers

**Context:** A security review of the whole app found that the web UI
relied on exactly one mechanism to stop cross-site requests -- the
session cookie's `SameSite=Lax` attribute -- and sent no security
response headers at all. Neither is a bug on its own; together they
meant a single change (a state-changing `GET`, an attribute tweak, a
club running Parcella on a subdomain next to its own WordPress) could
remove the app's entire defense against forged requests, with nothing
behind it.

**Decision 1: an independent CSRF token, on top of `SameSite=Lax`, not
instead of it.** `SameSite=Lax` stays exactly as it is. The token
(`app/csrf.py`) is the second, independent factor, because Lax has
three specific gaps in *this* codebase:

* Lax still sends the session cookie on top-level cross-site `GET`
  navigation. The app already has one route that commits on `GET`
  (`/finances/runs/{id}/print-bundle`, which marks invoices printed),
  so the protection is one plausible refactor away from not applying.
* A same-site attacker is not cross-site: any other host under the same
  registrable domain -- e.g. the club's own CMS on a sibling subdomain,
  which is exactly the deployment the public signup API exists for --
  is unaffected by SameSite.
* It is a browser-side promise about a cookie attribute. Anything that
  changes the attribute, or an older browser, silently removes it.

**Decision 2: double-submit cookie, with the cookie kept HttpOnly.**
A random token goes into a `csrf` cookie and is mirrored into every
state-changing form as a hidden field (Jinja global `csrf_field()`).
The *middleware* compares the two -- server-side, which is what lets the
cookie stay `HttpOnly`. The widespread JS-reads-the-cookie variant
requires a readable cookie and therefore hands the token to any
successful XSS; this variant does not. (Rejected alternative: deriving
the token from the session cookie. It has to exist before login too --
login CSRF, where the victim is silently logged into the *attacker's*
account, is a real attack, so the login POST is protected like any
other.)

**Decision 3: validation in middleware, not in a dependency.** A
`Depends(...)` on every route would be opt-in, and the failure mode of
forgetting one is silent. The middleware (`csrf_middleware` in
`app/main.py`) rejects before any router runs, so a new route is
protected by default. The matching guarantee on the template side is a
test rather than a mechanism: `tests/test_security.py` walks every
template and fails if a `method="post"` form is missing
`{{ csrf_field() }}`.

**Decision 4: `/api/**` is exempt.** Those routers authenticate with a
bearer token (`app/api_auth.py`) or a shared header token
(`app/public_api_auth.py`) -- never with an ambient cookie. A forged
cross-site request there simply arrives unauthenticated, so there is
nothing to protect, and requiring a token would break every existing API
client for no gain.

**Accepted cost:** validating a form POST means the middleware buffers
and parses the request body, and the endpoint parses it again from
Starlette's cached copy. This matters for exactly one endpoint, the
backup restore upload (200 MB cap, system-admin only, rare). The
alternative -- putting the token in the query string for multipart forms
-- would keep memory flat at the price of writing a security token into
every access log, which is worse. Note for future debugging: the
middleware must call `request.body()` *before* `request.form()`;
parsing the form alone consumes the stream and the endpoint then sees an
empty body (FastAPI answers 422 to every form POST).

**Decision 5: security headers on every response, with a deliberately
imperfect CSP.** `app/security_headers.py` sends CSP, `nosniff`,
`X-Frame-Options: DENY`, `Referrer-Policy: same-origin`, and -- outside
development only -- HSTS. The CSP keeps `'unsafe-inline'` for scripts
and styles: the templates are full of inline `<style>`/`<script>`
blocks, and nonce-ing all of them is a separate, much larger change.
Shipping the weaker policy now was chosen over shipping nothing,
because even with `'unsafe-inline'` it still stops an injected
`<script src>` to an attacker's host, `form-action 'self'` stops an
injected form from posting member data off-site, and `frame-ancestors
'none'` ends clickjacking of the admin panel. Tightening it later means
adding nonces, not redesigning this.

This matters more here than in a typical server-rendered app because
user-uploaded files are served from the same origin as the UI
(`/static/uploads/`) -- and the club logo is allowed to be an SVG, which
runs its own script when opened directly.

**Consequences:**

* Every new `method="post"` form needs `{{ csrf_field() }}`; the test
  above fails the build otherwise. `fetch()`-based POSTs send the
  `X-CSRF-Token` header instead, reading the value from the
  `<meta name="csrf-token">` tag in `base.html`.
* Tests use `CsrfAwareClient` (`tests/conftest.py`), which carries the
  token like a browser would. A test that wants to see a request
  *without* one uses the `raw_client` fixture.
* Loading Bootstrap from a different CDN, or adding one, means editing
  the CSP in `app/security_headers.py` -- otherwise it silently fails
  to load in the browser.
