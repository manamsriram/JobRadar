"""Careers-page discovery tests: signals.careers_discovery.discover_careers_url.

Run: pytest backend/test_careers_discovery.py
"""
import asyncio

import httpx
import pytest

from signals import careers_discovery as cd


class _FakeResponse:
    def __init__(self, status_code, url):
        self.status_code = status_code
        self.url = url
        self.is_redirect = False
        self.next_request = None
        self.headers = {}


class _FakeClient:
    """Returns 200 only for `hit_path`; 404 for every other candidate path."""

    def __init__(self, hit_path=None, raise_on=None):
        self._hit_path = hit_path
        self._raise_on = raise_on

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, **kw):
        if self._raise_on and self._raise_on in url:
            raise httpx.ConnectError("boom")
        if self._hit_path and url.endswith(self._hit_path):
            return _FakeResponse(200, url)
        return _FakeResponse(404, url)


def test_discover_careers_url_returns_first_hit(monkeypatch):
    monkeypatch.setattr(cd, "is_safe_url", lambda u: True)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: _FakeClient(hit_path="/jobs"))

    result = asyncio.run(cd.discover_careers_url("acme.com"))

    assert result == "https://acme.com/jobs"


def test_discover_careers_url_prepends_scheme_when_missing(monkeypatch):
    monkeypatch.setattr(cd, "is_safe_url", lambda u: True)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: _FakeClient(hit_path="/careers"))

    result = asyncio.run(cd.discover_careers_url("acme.com"))

    assert result.startswith("https://acme.com")


def test_discover_careers_url_returns_none_when_all_paths_miss(monkeypatch):
    monkeypatch.setattr(cd, "is_safe_url", lambda u: True)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: _FakeClient(hit_path=None))

    assert asyncio.run(cd.discover_careers_url("acme.com")) is None


def test_discover_careers_url_returns_none_when_unsafe(monkeypatch):
    monkeypatch.setattr(cd, "is_safe_url", lambda u: False)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: _FakeClient(hit_path="/jobs"))

    assert asyncio.run(cd.discover_careers_url("169.254.169.254")) is None


def test_discover_careers_url_survives_request_errors(monkeypatch):
    monkeypatch.setattr(cd, "is_safe_url", lambda u: True)
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: _FakeClient(hit_path="/join", raise_on="/careers")
    )

    result = asyncio.run(cd.discover_careers_url("acme.com"))

    assert result == "https://acme.com/join"


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
