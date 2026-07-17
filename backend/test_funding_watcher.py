"""Funding watcher tests: headline parsing, host extraction, feed polling.

Run: pytest backend/test_funding_watcher.py
"""
import asyncio

import httpx
import pytest

import state
from signals import funding_watcher as fw

_FEED_XML = """<?xml version="1.0"?>
<rss><channel>
<item>
  <title>Acme AI raises $12M seed round</title>
  <link>https://techcrunch.com/2026/07/17/acme-ai-raises</link>
  <guid>tc-1</guid>
</item>
</channel></rss>
"""


class _FakeResponse:
    def __init__(self, text="", status_code=200):
        self.text = text
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("bad status", request=None, response=self)


class _FakeClient:
    def __init__(self, response):
        self._response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, **kw):
        return self._response


def test_guess_company_domain_matches_headline_pattern():
    company, domain = fw._guess_company_domain("Acme AI raises $12M seed round")
    assert company == "Acme AI"
    assert domain == "acmeai.com"


def test_guess_company_domain_no_match_returns_none():
    assert fw._guess_company_domain("Nothing interesting happened today") == (None, None)


def test_host_strips_www_and_port():
    assert fw._host("https://www.example.com:443/path") == "example.com"


def test_host_rejects_skip_list_domains():
    assert fw._host("https://twitter.com/acme") is None


def test_host_rejects_hostless_url():
    assert fw._host("not-a-url") is None


def test_host_rejects_unsafe_host(monkeypatch):
    monkeypatch.setattr(fw, "is_safe_url", lambda u: False)
    assert fw._host("https://example.com") is None


def test_check_funding_enqueues_new_entry(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "FUNDING_QUEUE_FILE", tmp_path / "funding_queue.json")
    monkeypatch.setattr(state, "SEEN_FUNDING_FILE", tmp_path / "seen_funding.json")
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: _FakeClient(_FakeResponse(_FEED_XML)))
    monkeypatch.setattr(asyncio, "sleep", lambda *a: _noop())

    new_entries = asyncio.run(fw.check_funding())

    assert len(new_entries) == 1
    assert new_entries[0]["id"] == "tc-1"
    assert new_entries[0]["domain"] == "acmeai.com"
    assert "tc-1" in state.load_seen_funding()


def test_check_funding_skips_already_seen_entries(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "FUNDING_QUEUE_FILE", tmp_path / "funding_queue.json")
    monkeypatch.setattr(state, "SEEN_FUNDING_FILE", tmp_path / "seen_funding.json")
    state.save_seen_funding({"tc-1"})
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: _FakeClient(_FakeResponse(_FEED_XML)))
    monkeypatch.setattr(asyncio, "sleep", lambda *a: _noop())

    assert asyncio.run(fw.check_funding()) == []


def test_check_funding_returns_empty_on_http_error(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "FUNDING_QUEUE_FILE", tmp_path / "funding_queue.json")
    monkeypatch.setattr(state, "SEEN_FUNDING_FILE", tmp_path / "seen_funding.json")
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: _FakeClient(_FakeResponse("", status_code=500)))
    monkeypatch.setattr(asyncio, "sleep", lambda *a: _noop())

    assert asyncio.run(fw.check_funding()) == []


async def _noop():
    return None


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
