"""Hunter.io on-demand contact lookup tests: resolve_domain, per-company
contact cache (growing list), budget, find_contact / get_company_contacts.
Run: pytest backend/test_enricher.py
"""
import asyncio

import httpx
import pytest

import enricher
import state


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


@pytest.fixture(autouse=True)
def _isolate_data(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "DATA_DIR", tmp_path)
    monkeypatch.setattr(enricher, "CONTACTS_CACHE_FILE", tmp_path / "company_contacts.json")
    monkeypatch.setattr(enricher, "BUDGET_FILE", tmp_path / "hunter_budget.json")
    monkeypatch.setattr(state, "COMPANIES_FILE", tmp_path / "companies.json")


# ---- resolve_domain ----
def test_resolve_domain_curated_hit(monkeypatch):
    monkeypatch.setattr(state, "load_companies", lambda: [{"name": "Acme", "domain": "acme.com"}])
    assert enricher.resolve_domain("Acme", None) == ("acme.com", False)


def test_resolve_domain_url_derived(monkeypatch):
    monkeypatch.setattr(state, "load_companies", lambda: [])
    domain, guessed = enricher.resolve_domain("NotCurated", "https://www.notcurated.com/jobs/123")
    assert domain == "notcurated.com"
    assert guessed is False


def test_resolve_domain_rejects_ats_host(monkeypatch):
    monkeypatch.setattr(state, "load_companies", lambda: [])
    domain, guessed = enricher.resolve_domain("SomeCo", "https://boards.greenhouse.io/someco/jobs/1")
    assert domain == "someco.com"
    assert guessed is True


def test_resolve_domain_rejects_job_aggregator_host(monkeypatch):
    # Regression: an uncurated company's levels.fyi/YC listing URL must never
    # be trusted as that company's own domain (found contact at the wrong
    # company when Robinhood's levels.fyi listing resolved to levels.fyi).
    monkeypatch.setattr(state, "load_companies", lambda: [])
    domain, guessed = enricher.resolve_domain(
        "Robinhood", "https://www.levels.fyi/jobs?jobId=143957004484780742"
    )
    assert domain == "robinhood.com"
    assert guessed is True


def test_resolve_domain_slug_fallback(monkeypatch):
    monkeypatch.setattr(state, "load_companies", lambda: [])
    domain, guessed = enricher.resolve_domain("Some Co!", None)
    assert domain == "someco.com"
    assert guessed is True


# ---- get_company_contacts (cache-only read) ----
def test_get_company_contacts_empty_when_never_researched(monkeypatch):
    monkeypatch.setattr(state, "load_companies", lambda: [{"name": "Acme", "domain": "acme.com"}])
    result = enricher.get_company_contacts("Acme", None)
    assert result == {"contacts": [], "domain_guessed": False}


def test_get_company_contacts_returns_prior_finds_across_jobs(monkeypatch):
    monkeypatch.setattr(state, "load_companies", lambda: [{"name": "Acme", "domain": "acme.com"}])
    enricher._append_cached("acme.com", [{"name": "Ada", "email": "ada@acme.com"}], False)

    # A second, unrelated job at the same company sees the same contact for free.
    result = enricher.get_company_contacts("Acme", None)
    assert result["contacts"] == [{"name": "Ada", "email": "ada@acme.com"}]


# ---- find_contact: appends a new contact, doesn't refetch existing ----
def test_find_contact_appends_and_persists_across_calls(monkeypatch):
    monkeypatch.setattr(state, "load_companies", lambda: [{"name": "Acme", "domain": "acme.com"}])
    contacts_by_offset = {0: {"name": "Ada"}, 1: {"name": "Bob"}}

    async def fake_fetch(domain, offset):
        return contacts_by_offset.get(offset)

    monkeypatch.setattr(enricher, "_fetch_contact_at_offset", fake_fetch)

    first = asyncio.run(enricher.find_contact("Acme", None))
    assert first["contacts"] == [{"name": "Ada"}]
    assert first["new_contact"] is True

    second = asyncio.run(enricher.find_contact("Acme", None))
    assert second["contacts"] == [{"name": "Ada"}, {"name": "Bob"}]

    # Another job at the same company immediately sees both via the cache-only read.
    assert enricher.get_company_contacts("Acme", None)["contacts"] == second["contacts"]


def test_find_contact_no_new_contact_available(monkeypatch):
    monkeypatch.setattr(state, "load_companies", lambda: [{"name": "Acme", "domain": "acme.com"}])
    monkeypatch.setattr(enricher, "_fetch_contact_at_offset", lambda domain, offset: asyncio.sleep(0, result=None))

    result = asyncio.run(enricher.find_contact("Acme", None))

    assert result["contacts"] == []
    assert result["new_contact"] is False
    assert enricher.get_cached("acme.com") is None  # empty result stays uncached/retryable


# ---- budget ----
def test_zero_result_does_not_decrement_budget(monkeypatch):
    monkeypatch.setenv("HUNTER_API_KEY", "test-key")
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: _FakeClient(_FakeResponse({"data": {"emails": []}}))
    )
    asyncio.run(enricher._fetch_contact_at_offset("acme.com", 0))
    assert enricher.budget_remaining() is True
    assert state._read_json(enricher.BUDGET_FILE, {}) == {}


def test_nonempty_result_records_one_credit(monkeypatch):
    monkeypatch.setenv("HUNTER_API_KEY", "test-key")
    emails = [{"first_name": "Ada", "last_name": "L", "position": "CEO", "value": "ada@acme.com"}]
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: _FakeClient(_FakeResponse({"data": {"emails": emails}}))
    )
    asyncio.run(enricher._fetch_contact_at_offset("acme.com", 0))
    data = state._read_json(enricher.BUDGET_FILE, {})
    assert sum(data.values()) == 1


def test_find_contact_quota_exhausted(monkeypatch):
    monkeypatch.setattr(state, "load_companies", lambda: [{"name": "Acme", "domain": "acme.com"}])
    monkeypatch.setattr(enricher, "budget_remaining", lambda: False)

    result = asyncio.run(enricher.find_contact("Acme", None))

    assert result == {"error": "quota_exhausted"}


def test_fetch_contact_returns_none_without_api_key(monkeypatch):
    monkeypatch.delenv("HUNTER_API_KEY", raising=False)
    assert asyncio.run(enricher._fetch_contact_at_offset("acme.com", 0)) is None


def test_fetch_contact_returns_none_on_http_error(monkeypatch):
    monkeypatch.setenv("HUNTER_API_KEY", "test-key")
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: _FakeClient(_FakeResponse({}, status_code=500))
    )
    assert asyncio.run(enricher._fetch_contact_at_offset("acme.com", 0)) is None


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
