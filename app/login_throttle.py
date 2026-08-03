"""
Brute-force throttling for the two login entry points: the web UI's
cookie login (app/routers/auth.py) and the REST API's token endpoints
(app/routers/api_auth.py). Both share these counters, so an attacker
can't sidestep the web limit by hammering /api/v1/auth/token instead.

Only FAILED attempts count, and a successful login clears the caller's
counters -- a user who mistypes their password twice shouldn't spend the
rest of the window one attempt away from a lockout.

Two limits, because they fail differently:
  * per IP + email: stops guessing one account's password.
  * per IP: stops spraying one password across many accounts, which
    would otherwise never trip the per-account limit.

There is deliberately NO per-email-only limit: that would let anyone
lock a known admin address out of their own account from any address,
turning the protection into a denial-of-service tool. The tradeoff is
that a distributed attack against a single account is only slowed by
the per-account-per-IP limit -- acceptable for this app's threat model
(a club installation, usually behind a small reverse proxy), and the
right next step there is fail2ban on the proxy, not a bigger in-memory
table here.

Built on app/rate_limit.py; see that module for why the counters are
in-memory and what that costs.
"""
from fastapi import Request

from app.rate_limit import (
    clear_key, client_ip_key, is_rate_limited, record_event,
)

# 15 minutes: long enough that guessing at any useful rate trips it,
# short enough that a locked-out member can retry within a coffee break
# instead of calling the board.
WINDOW_SECONDS = 15 * 60
MAX_FAILURES_PER_ACCOUNT = 10
MAX_FAILURES_PER_IP = 30


def _account_key(request: Request, email: str) -> str:
    return client_ip_key(request, "login_account", (email or "").strip().lower())


def _ip_key(request: Request) -> str:
    return client_ip_key(request, "login_ip")


def login_is_throttled(request: Request, email: str) -> bool:
    """True when this caller has already used up its failure budget and
    the attempt should be rejected without even checking the password."""
    return (
        is_rate_limited(_account_key(request, email), MAX_FAILURES_PER_ACCOUNT, WINDOW_SECONDS)
        or is_rate_limited(_ip_key(request), MAX_FAILURES_PER_IP, WINDOW_SECONDS)
    )


def record_failed_login(request: Request, email: str) -> None:
    record_event(_account_key(request, email), WINDOW_SECONDS)
    record_event(_ip_key(request), WINDOW_SECONDS)


def clear_login_failures(request: Request, email: str) -> None:
    clear_key(_account_key(request, email))
    clear_key(_ip_key(request))
