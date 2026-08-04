# Spam filter external API: tied to apilayer.com for now

**Note: superseded.** apilayer.com's API stopped working, with no
fallback since the integration was hard-wired to its specific
contract -- exactly the risk this ADR flagged below. See
[ADR 0066](./0066-spam-filter-external-api-back-to-a-generic-contract.md)
for the generic contract + reference adapter that replaced it. Left
as-is here rather than edited, so the reasoning that led to the
apilayer-specific version in the first place stays visible.

**Context:** `app/spam_filter.py` (ticket system stage 3) combines
built-in heuristics with an optional external spam-check API,
configured under `/admin/settings` as a URL + API key. The module's
original docstring described this as a generic contract -- POST JSON
`{"absender_email", "betreff", "inhalt"}`, expect back
`{"spam_score": 0.0-1.0}` -- with the idea that "any service can be
hooked up via a small adapter."

**What actually happened:** the first real-world test (pointing it at
apilayer.com's Spam Check API, https://apilayer.com/marketplace/spamchecker-api)
failed with `401 Unauthorized`. Investigation showed apilayer's actual
contract is nothing like the generic one that was assumed: it wants an
`apikey` header (not `Authorization: Bearer`), a plain-text body (not
JSON), and returns `{"score": <number>, "is_spam": bool}` (not
`spam_score` on a 0-1 scale). No real spam-check service was ever
confirmed to match the original generic shape -- it was speculative,
not built against anything real.

**Decision (for now):** `_external_check` speaks apilayer.com's actual
API directly -- `apikey` header, plain-text `subject + content` body,
and treats `is_spam` as the external signal (mapped to 1.0/0.0),
deliberately not apilayer's raw `score`, since that already accounts
for the `threshold=` query param the admin sets in the URL itself.
This makes the integration vendor-specific rather than the originally
imagined "generic" one.

**Why apilayer specifically, and why not a proper adapter/abstraction
now:** it's what was already configured and testable, and building a
provider abstraction (or a separately hosted adapter service) is more
infrastructure than is worth it before the project has a single real
deployment. Self-hosting something fully open source (rspamd) was
considered and explicitly deferred for the same reason -- it needs a
new container wired into `docker-compose.yml`, which isn't worth doing
until there's a production target to justify it.

**This is flagged as a pre-open-source loose end, not a finished
design.** apilayer.com is a closed, commercial API -- a poor default
for a project intended to be open source, where self-hosters shouldn't
be steered toward one paid vendor with no alternative. Before the
project goes live/public, revisit this: either (a) reintroduce a
genuinely generic contract backed by a documented reference adapter
(so apilayer, Akismet, self-hosted rspamd, etc. are all pluggable
without touching core code), or (b) explicitly support a short list of
providers (apilayer + at least one free/open-source option like
rspamd) with provider-specific code paths, clearly documented as such
in `/admin/settings`. Whichever direction, `/admin/settings`'s help
text for `spam_api_url`/`spam_api_key` should stop being vendor-silent
once this is settled.
