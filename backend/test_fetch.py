"""Retry/backoff tests: fetch_with_retry transient-vs-permanent handling, RetryBudget.

Run: pytest backend/test_fetch.py
"""
import asyncio

import httpx
import pytest

import fetch
from fetch import RetryBudget, fetch_with_retry


@pytest.fixture(autouse=True)
def _skip_ssrf_check(monkeypatch):
    # These tests exercise retry/backoff logic against example.test (RFC 2606,
    # doesn't resolve) via a fake client — SSRF resolution itself is covered
    # by test_net_safety.py.
    monkeypatch.setattr(fetch, "is_safe_url", lambda url: True)


def test_retry_budget_caps_at_remaining():
    budget = RetryBudget(3)
    assert budget.take(2) == 2
    assert budget.take(2) == 1
    assert budget.take(2) == 0


def test_retry_budget_never_goes_negative():
    budget = RetryBudget(0)
    assert budget.take(2) == 0
    assert budget.remaining == 0


class _FakeResponse:
    def __init__(self, status_code=200, request=None, next_request=None):
        self.status_code = status_code
        self.request = request
        self.next_request = next_request

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("bad status", request=self.request, response=self)


class _FakeClient:
    def __init__(self, statuses, redirect_to=None):
        self._statuses = list(statuses)
        self._redirect_to = redirect_to

    async def get(self, url, **kw):
        code = self._statuses.pop(0)
        next_request = httpx.Request("GET", self._redirect_to) if self._redirect_to else None
        return _FakeResponse(code, request=httpx.Request("GET", url), next_request=next_request)

    async def send(self, request, **kw):
        code = self._statuses.pop(0)
        return _FakeResponse(code, request=request)


def test_fetch_with_retry_succeeds_first_try(monkeypatch):
    monkeypatch.setattr(asyncio, "sleep", lambda *a: _noop())
    client = _FakeClient([200])
    r = asyncio.run(fetch_with_retry(client, "https://example.test/"))
    assert r.status_code == 200


def test_fetch_with_retry_retries_5xx_then_succeeds(monkeypatch):
    monkeypatch.setattr(asyncio, "sleep", lambda *a: _noop())
    client = _FakeClient([503, 200])
    r = asyncio.run(fetch_with_retry(client, "https://example.test/", retries=2))
    assert r.status_code == 200


def test_fetch_with_retry_never_retries_4xx(monkeypatch):
    monkeypatch.setattr(asyncio, "sleep", lambda *a: _noop())
    client = _FakeClient([404, 200])  # second response should never be consumed
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(fetch_with_retry(client, "https://example.test/", retries=2))
    assert client._statuses == [200]


def test_fetch_with_retry_gives_up_after_budget(monkeypatch):
    monkeypatch.setattr(asyncio, "sleep", lambda *a: _noop())
    client = _FakeClient([503])
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(fetch_with_retry(client, "https://example.test/", retries=0))


async def _noop():
    return None


def test_fetch_with_retry_rejects_unsafe_url(monkeypatch):
    monkeypatch.setattr(fetch, "is_safe_url", lambda url: False)
    client = _FakeClient([200])
    with pytest.raises(httpx.UnsupportedProtocol):
        asyncio.run(fetch_with_retry(client, "unsafe-placeholder"))


def test_fetch_with_retry_follows_safe_redirect(monkeypatch):
    monkeypatch.setattr(asyncio, "sleep", lambda *a: _noop())
    client = _FakeClient([302, 200], redirect_to="https://example.test/landed")
    r = asyncio.run(fetch_with_retry(client, "https://example.test/"))
    assert r.status_code == 200


def test_fetch_with_retry_rejects_unsafe_redirect_target(monkeypatch):
    monkeypatch.setattr(asyncio, "sleep", lambda *a: _noop())

    def only_first_host_safe(url):
        return "169.254.169.254" not in url

    monkeypatch.setattr(fetch, "is_safe_url", only_first_host_safe)
    client = _FakeClient([302], redirect_to="http://169.254.169.254/latest/meta-data/")
    with pytest.raises(httpx.UnsupportedProtocol):
        asyncio.run(fetch_with_retry(client, "https://example.test/"))


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
