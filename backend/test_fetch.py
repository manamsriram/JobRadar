"""Retry/backoff tests: fetch_with_retry transient-vs-permanent handling, RetryBudget.

Run: pytest backend/test_fetch.py
"""
import asyncio

import httpx
import pytest

from fetch import RetryBudget, fetch_with_retry


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
    def __init__(self, status_code=200, request=None):
        self.status_code = status_code
        self.request = request

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("bad status", request=self.request, response=self)


class _FakeClient:
    def __init__(self, statuses):
        self._statuses = list(statuses)

    async def get(self, url, **kw):
        code = self._statuses.pop(0)
        return _FakeResponse(code, request=httpx.Request("GET", url))


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


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
