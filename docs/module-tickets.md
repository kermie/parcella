# Module: Ticket System

Support ticket system, modeled loosely after
[Freescout](https://github.com/freescout-help-desk/freescout).
Built in three stages -- this page describes **stage 1**
(data model + manual ticket management) plus the two stages that
followed.

Module flag: `tickets`

## Stage overview

1. **Data model + manual ticket management** (done) -- tickets, history,
   assignment, status, member matching. No automatic email fetching yet.
2. **Email integration** (done) -- IMAP inbox configuration,
   background polling (every 2 min.), incoming mail becomes
   tickets/messages, outgoing replies are sent via SMTP.
3. **Spam interface** (done) -- built-in heuristics plus an optional
   external API, configurable under `/admin/settings`.

## Stage 3: spam filter

**Two combined layers.** Built-in heuristics run immediately, with no
external service: sender-domain blocklist, keyword blocklist, number of
links in the text. An optional external API (`app/spam_filter.py`,
`_externe_pruefung()`) is only used if a URL is configured -- it merely
needs to return `{"spam_score": 0.0-1.0}` as JSON, so that any service
(Akismet, a self-hosted filter, a small adapter in front of a paid
service) can be connected without touching caller code. The final score
is the maximum of the heuristic and the external score; if the external
call fails, it silently falls back to the heuristics -- an outage of the
external service must never block ticket creation.

**Transparency instead of silently sorting things out.** Tickets marked
as spam are not deleted, only hidden from the default "Active" filter. A
dedicated "Suspected" filter tab (with a count badge) still shows them,
including the score and a traceable justification (`spam_reasoning`, e.g.
"keywords found: casino, prize"). Every ticket can be cleared as
"not spam" (false positive) with a single click -- important because
heuristics are never perfect, and an association should never accidentally
lose a genuine inquiry forever.

**Spam checking only runs on new tickets, not replies.** If someone
replies to an already-existing (thread-matched) ticket, no new spam check
runs -- this avoids unnecessary (potentially paid) external calls and is
also correct in substance: a conversation already recognized as
legitimate does not need to be re-evaluated with every reply.

**New dependency:** `httpx` (for the optional external API) was added to
`requirements.txt` -- requires `docker compose build`, not just
`restart`.

## Stage 2: email integration

**One inbox for both IMAP fetching and SMTP sending**, separate from the
general club SMTP configuration (which is only used for invitation
emails). Configured under `/admin/settings`, in the "Ticket inbox
(IMAP/SMTP)" card. Both passwords are stored encrypted (see
`app/crypto_utils.py`) -- the special-case handling for passwords on save
(empty = leave unchanged) was generalized for this
(`if key.endswith("_password")` instead of one hard-coded special case).

**Background polling without a new service.** An `asyncio` task, started
in the `lifespan` function of `main.py`, calls `process_incoming_mails()`
every 2 minutes. No Celery, no Redis, no separate container -- fits the
project's "small and robust" philosophy. There is also a manual
"Fetch inbox now" button for immediate testing, without waiting for the
next cycle.

**A single inbox for everything -- at the association's request.**
Originally, the ticket-inbox configuration was completely separate from
the general SMTP configuration (its own host/port/user/password fields).
The association deliberately simplified this: "If I want to use a ticket
system sensibly, it only needs a single email address, a single inbox.
Anything else I'd handle on the server and set up forwarding if needed."
The SMTP credentials (host, port, user, password) are therefore now
maintained **once** (`app/email_service.py`, `lade_smtp_konfiguration()`)
and reused for both invitations **and** ticket replies
(`app/ticket_mailer.py` imports that function directly instead of
duplicating its own SMTP fields). Only for IMAP fetching (receiving) are
there additional fields (`imap_host`, `imap_port`, `imap_ssl`) -- the IMAP
user/password are identical to the SMTP credentials, since it's the same
inbox.

**Initial sync skips existing emails.** An inbox that has been in use for
a long time can contain thousands of emails. Without special handling, the
very first fetch would try to retrieve **all** of them individually via
`UID FETCH` -- in practice this caused a `socket error: EOF` (connection
dropped by the mail server due to too many commands in one session). The
fix: if `ticket_imap_letzte_uid` is not yet set, the first fetch only
**determines the current highest UID** (a single `SEARCH`, no `FETCH`)
and stores it as the starting point -- without processing a single email.
From the next cycle onward, only newly arriving emails are processed.
This also matches the expected behavior: nobody wants a years-old mail
archive to suddenly show up in full as tickets in the system.

**Lesson:** for any kind of "since last time" processing (IMAP, but this
also applies to other polling scenarios), explicitly handle the special
case of "there has never been a 'last time'" instead of implicitly
interpreting it as "everything since the beginning of time".

**IMAP runs synchronously in a thread, not async.** There is no mature
async IMAP library among the standard dependencies. Instead of adding a
new one, `app/ticket_mailer.py` uses Python's built-in tools (`imaplib`,
`email`) synchronously, executed via `asyncio.to_thread(...)` so the
event loop isn't blocked in the meantime.

**Operational data lives in the existing club-settings table.** The most
recently processed IMAP UID and the last error end up as
`ticket_imap_letzte_uid` / `ticket_imap_letzter_fehler` in the same
key-value table that also stores module flags and SMTP settings -- no
additional table/migration just for this purpose.

**Threading via message ID, with a fallback.** Incoming replies are first
matched via the `In-Reply-To`/`References` headers against stored
`message_id` values of previous `TicketMessage` entries. If that fails
(e.g. because the customer writes a new email instead of replying, but
with the same subject), the fallback is to search open tickets by sender
address + normalized subject (with the "Re:"/"Fwd:" prefix stripped). If
no match is found, a new ticket is created.

**Closed tickets automatically reopen on a new reply** -- the status
jumps back to `ASSIGNED` (if still assigned) or `UNASSIGNED`.

**Spam checking is already called, even though it's still a no-op.**
`pruefe_auf_spam()` from stage 1 is already called for every incoming
email, and the result is stored in `spam_suspected`/`spam_score` -- only
the actual check logic is still empty. In stage 3, therefore, only this
one function needs to be swapped out, with no changes to callers.

## Data model (stage 1)

```
tickets            – a request: subject, status, assignment, sender,
                      optional member link
ticket_messages     – the conversation history of a ticket (incoming/
                      outgoing/internal)
```

## Key decisions

**Access rights via the existing `UserRole`**, not a new, independent
permission system -- the association plans to extend the roles later
anyway (e.g. with a real "extended board" role). A separate ticket
permission system would only have complicated that extension.

**Status as an explicit state machine**, not implicitly derived from the
assignment:
```
UNASSIGNED -> ASSIGNED -> CLOSED
     ↘ DEFERRED (until date) ↗
```
When a ticket is assigned, the status automatically jumps to `ASSIGNED`;
when the assignment is cleared, it goes back to `UNASSIGNED`.

**"Deferred until" is purely computed, not a background job.** A ticket
with status `DEFERRED` whose date has been reached is not automatically
switched to another status in the database. Instead, the `Ticket.is_due`
property computes this live on every view (`status == DEFERRED and
deferred_until <= today`). This avoids a background job that would only
exist for this purpose -- the one background job that's actually needed
(email polling) arrives in stage 2 anyway.

**Member matching by email address is deliberately cautious.** Similar to
the accident-insurance logic: if the sender address can be **uniquely**
matched to a member, that happens automatically. If multiple members
share the same address (e.g. couples), the automation makes **no
decision** -- the UI shows all candidates for manual selection
(`find_members_by_email()` in `app/ticket_utils.py`).

**Assignment notifications use the existing SMTP infrastructure**, not
the ticket inbox that only arrives in stage 2. The general club SMTP
configuration (see the water/electricity-spanning `app/email_service.py`)
is already sufficient for this -- another example of earlier
infrastructure decisions paying off.

**Spam fields already exist in the database** (`spam_suspected`,
`spam_score`), even though the actual check (`app/spam_filter.py`) is
still a pure no-op. This avoids another migration in stage 3 -- only the
check function itself needs to be swapped out.

**Change history is reused.** Status and assignment changes are logged
via the existing generic `ChangeTracker` (`entity_type="Ticket"`) -- no
dedicated history table needed.

## REST API

Complete API from the start (`/api/v1/tickets`), following the "API-first
is now mandatory" rule (see Architecture Decisions). Creating tickets,
changing status/assignment, adding messages -- all also possible
programmatically, e.g. for a later automation of the email import in
stage 2 (which would then likely reuse the same functions internally as
the API, rather than duplicating its own logic).
