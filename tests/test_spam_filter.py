"""
Tests for the ticket system's spam filter (app/spam_filter.py).

The external API check used to be hard-wired to apilayer.com's Spam
Check API (apikey header, plain-text body, {"is_spam": bool} response)
-- see docs/ADR/0038 for why, and docs/ADR/0066 for why it moved back
to a generic contract (apilayer stopped working, and nothing else
could speak its vendor-specific shape). These tests pin down the
generic contract: POST JSON {sender_email, subject, content}, Bearer
auth if a key is configured, expect back {"spam_score": 0.0-1.0}.

No real external service is reachable from this test environment, so
the external check is exercised against an httpx.MockTransport (same
approach as WordPressPublisher/NextcloudProvider in
tests/test_announcements.py / tests/test_cloud_storage.py) -- but since
_external_check builds its own httpx.AsyncClient internally rather
than accepting an injectable one, the client class itself is
monkeypatched for the duration of each test.
"""
from app.database import AsyncSessionLocal
from app.models import ClubSetting
from app.crypto_utils import encrypt
from app.spam_filter import check_for_spam, _heuristic_score


def _mock_transport(status=200, json_body=None, malformed=False):
    import httpx as httpx_module

    def handler(request: httpx_module.Request) -> httpx_module.Response:
        if malformed:
            return httpx_module.Response(status, content=b"not json")
        return httpx_module.Response(status, json=json_body if json_body is not None else {})

    return httpx_module.MockTransport(handler)


def _patch_external_client(monkeypatch, transport):
    import httpx as httpx_module

    # Capture the real class before patching -- "app.spam_filter.httpx"
    # is the same shared httpx module object, so calling
    # httpx_module.AsyncClient(...) from inside the replacement below
    # would otherwise call the patched version and recurse forever.
    real_async_client = httpx_module.AsyncClient

    def fake_async_client(**kwargs):
        return real_async_client(transport=transport)

    monkeypatch.setattr("app.spam_filter.httpx.AsyncClient", fake_async_client)


async def _set_settings(**kv) -> None:
    async with AsyncSessionLocal() as session:
        for key, value in kv.items():
            session.add(ClubSetting(key=key, value=value, description="test"))
        await session.commit()


# ---------------------------------------------------------------------------
# Heuristics (no external service involved)
# ---------------------------------------------------------------------------

def test_heuristic_score_flags_blocklisted_domain():
    score, reasons = _heuristic_score(
        "spammer@bad-domain.example", "Hello", "Just a normal message",
        domain_blocklist=["bad-domain.example"], keyword_blocklist=[],
    )
    assert score >= 0.6
    assert any("bad-domain.example" in r for r in reasons)


def test_heuristic_score_flags_keywords_and_link_count():
    content = " ".join(f"http://spam{i}.example" for i in range(5))
    score, reasons = _heuristic_score(
        "someone@example.com", "WIN A PRIZE", content,
        domain_blocklist=[], keyword_blocklist=["prize"],
    )
    assert score > 0
    assert any("prize" in r.lower() for r in reasons)
    assert any("links" in r for r in reasons)


async def test_check_for_spam_uses_heuristics_only_without_api_url():
    await _set_settings(spam_keyword_blocklist="casino", spam_schwellenwert="0.1")

    async with AsyncSessionLocal() as db:
        result = await check_for_spam("someone@example.com", "Casino night", "Come play", db)

    assert result.is_spam_suspected is True
    assert "casino" in (result.reasoning or "").lower()


# ---------------------------------------------------------------------------
# External check: generic contract
# ---------------------------------------------------------------------------

async def test_external_check_sends_generic_contract_and_uses_returned_score(monkeypatch):
    import httpx as httpx_module

    captured = {}

    def handler(request: httpx_module.Request) -> httpx_module.Response:
        import json as json_module
        captured["headers"] = dict(request.headers)
        captured["body"] = json_module.loads(request.content)
        return httpx_module.Response(200, json={"spam_score": 0.9})

    _patch_external_client(monkeypatch, httpx_module.MockTransport(handler))

    await _set_settings(
        spam_api_url="https://spam-check.example/check",
        spam_api_key=encrypt("secret-key"),
        spam_schwellenwert="0.5",
    )

    async with AsyncSessionLocal() as db:
        result = await check_for_spam("someone@example.com", "Hi", "totally normal message", db)

    assert captured["body"] == {
        "sender_email": "someone@example.com", "subject": "Hi", "content": "totally normal message",
    }
    assert captured["headers"]["authorization"] == "Bearer secret-key"
    assert result.score == 0.9
    assert result.is_spam_suspected is True


async def test_external_check_failure_falls_back_to_heuristics(monkeypatch):
    import httpx as httpx_module

    def handler(request: httpx_module.Request) -> httpx_module.Response:
        return httpx_module.Response(500, text="internal error")

    _patch_external_client(monkeypatch, httpx_module.MockTransport(handler))

    await _set_settings(
        spam_api_url="https://spam-check.example/check",
        spam_keyword_blocklist="casino",
        spam_schwellenwert="0.1",
    )

    async with AsyncSessionLocal() as db:
        result = await check_for_spam("someone@example.com", "Casino night", "Come play", db)

    # External call failed -- must still resolve via heuristics, not raise.
    assert result.is_spam_suspected is True
    assert "casino" in (result.reasoning or "").lower()


async def test_external_check_malformed_response_is_treated_as_no_signal(monkeypatch):
    import httpx as httpx_module

    def handler(request: httpx_module.Request) -> httpx_module.Response:
        return httpx_module.Response(200, json={"unexpected": "shape"})

    _patch_external_client(monkeypatch, httpx_module.MockTransport(handler))

    await _set_settings(spam_api_url="https://spam-check.example/check", spam_schwellenwert="0.5")

    async with AsyncSessionLocal() as db:
        result = await check_for_spam("someone@example.com", "Hi", "totally normal message", db)

    assert result.is_spam_suspected is False
    assert result.score == 0.0
