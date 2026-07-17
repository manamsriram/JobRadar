"""Hunter.io enrichment tests: enricher.find_contacts.

Run: pytest backend/test_enricher.py
"""
import asyncio

import httpx
import pytest

import enricher


class _FakeResponse:
    def __init__(self, data, status_code=200):
        self._data = data
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("bad status", request=None, response=self)

    def json(self):
        return self._data


class _FakeClient:
    def __init__(self, response):
        self._response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, **kw):
        return self._response


def test_find_contacts_returns_empty_without_api_key(monkeypatch):
    monkeypatch.delenv("HUNTER_API_KEY", raising=False)
    assert asyncio.run(enricher.find_contacts("acme.com")) == []


def test_find_contacts_filters_to_target_titles(monkeypatch):
    monkeypatch.setenv("HUNTER_API_KEY", "test-key")
    emails = [
        {"first_name": "Ada", "last_name": "L", "position": "Recruiter", "value": "ada@acme.com"},
        {"first_name": "Bob", "last_name": "M", "position": "Sales", "value": "bob@acme.com"},
    ]
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: _FakeClient(_FakeResponse({"data": {"emails": emails}}))
    )

    contacts = asyncio.run(enricher.find_contacts("acme.com"))

    assert len(contacts) == 1
    assert contacts[0]["email"] == "ada@acme.com"


def test_find_contacts_caps_at_three(monkeypatch):
    monkeypatch.setenv("HUNTER_API_KEY", "test-key")
    emails = [
        {"first_name": f"P{i}", "last_name": "L", "position": "Hiring Manager", "value": f"p{i}@acme.com"}
        for i in range(5)
    ]
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: _FakeClient(_FakeResponse({"data": {"emails": emails}}))
    )

    assert len(asyncio.run(enricher.find_contacts("acme.com"))) == 3


def test_find_contacts_returns_empty_on_http_error(monkeypatch):
    monkeypatch.setenv("HUNTER_API_KEY", "test-key")
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: _FakeClient(_FakeResponse({}, status_code=500))
    )

    assert asyncio.run(enricher.find_contacts("acme.com")) == []


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
