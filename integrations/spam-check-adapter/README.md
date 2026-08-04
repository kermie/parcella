# Spam-check adapter (reference implementation)

Parcella's ticket system can run every incoming email through an
external spam-check API before the built-in heuristics decide whether
to flag it (see `app/spam_filter.py` and
`docs/module-tickets.md`). That API is a small, generic, vendor-neutral
contract on purpose (see `docs/ADR/0066-spam-filter-external-api-back-to-a-generic-contract.md`
for why) -- Parcella never talks to a specific spam-check provider
directly. Instead, you point it at a thin adapter that speaks this
contract on one side and whatever real provider you want on the other.

`example_adapter.py` in this folder is that adapter, in its simplest
possible form: it doesn't call a real provider at all (see the comment
in `score_message()`), it just proves the contract wires up end to end.
**Do not run it as-is in production** -- replace `score_message()` with
a call to Akismet, apilayer, a self-hosted filter like rspamd, or
whatever else you actually want, then point Parcella at it.

## The contract

**Request** -- Parcella POSTs here for every incoming ticket email:

```
POST <spam_api_url>
Authorization: Bearer <spam_api_key>      (only sent if a key is configured)
Content-Type: application/json

{"sender_email": "someone@example.com", "subject": "...", "content": "..."}
```

**Response** -- your adapter must reply with:

```
200 OK
Content-Type: application/json

{"spam_score": 0.7}
```

`spam_score` is a float from `0.0` (definitely not spam) to `1.0`
(definitely spam). Parcella takes the *maximum* of this score and its
own built-in heuristic score, so your adapter only needs to add signal
on top of what the heuristics already catch -- it doesn't need to be
perfect on its own.

Anything else -- a timeout, a non-2xx status, a response that doesn't
parse, a missing or non-numeric `spam_score` -- is treated by Parcella
as "no external signal available" and it silently falls back to the
heuristics alone. An outage or misconfiguration of your adapter can
never block ticket creation.

## Wiring it into Parcella

1. Run your adapted version of `example_adapter.py` somewhere Parcella
   can reach it (same Docker network, a small always-on service, a
   serverless function -- anything that can receive a POST).
2. In Parcella, go to Administration -> Settings -> "Spam filter
   (ticket system)" and set:
   - **Spam: external check API URL** -- your adapter's URL, e.g.
     `http://spam-adapter:8090/check`
   - **Spam: external API key** -- optional; if set, Parcella sends it
     as `Authorization: Bearer <key>` and your adapter should check it.

## Running the example as-is (to test the wiring only)

```
python3 example_adapter.py 8090
```

No dependencies beyond the Python standard library. It flags a message
as spam only if "viagra" or "casino" appears in the subject/content --
enough to prove the request/response shape matches what Parcella
expects, nothing more.
