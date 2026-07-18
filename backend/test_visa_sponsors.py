"""Visa-sponsor seed merge tests.

Run: pytest backend/test_visa_sponsors.py
"""
import asyncio

import pytest

import state
from signals import visa_sponsors as vs


def _seed(tmp_path, monkeypatch, seeds):
    seeds_file = tmp_path / "visa_sponsor_seeds.json"
    companies_file = tmp_path / "companies.json"
    monkeypatch.setattr(state, "VISA_SPONSOR_SEEDS_FILE", seeds_file)
    monkeypatch.setattr(state, "COMPANIES_FILE", companies_file)
    state._write_json_atomic(seeds_file, seeds)
    return companies_file


def _stub_live(monkeypatch, live: bool = True):
    """Most tests aren't exercising the liveness check itself — stub it to
    the given verdict so they don't make real network calls."""
    async def fake(url):
        return live
    monkeypatch.setattr(vs, "_url_is_live", fake)


def test_merge_adds_new_seed_company_with_hardcoded_url(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch, [
        {"name": "Acme Corp", "source": "visa-sponsor", "careers_url": "https://acme.com/careers",
         "domain": "acme.com", "tier": 1, "requires_js": True},
    ])
    _stub_live(monkeypatch, True)

    result = asyncio.run(vs.merge_seed_companies([]))

    assert result == [{
        "name": "Acme Corp", "source": "visa-sponsor", "careers_url": "https://acme.com/careers",
        "domain": "acme.com", "tier": 1, "requires_js": True,
    }]
    assert state.load_companies() == result


def test_merge_skips_company_already_present_by_domain(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch, [
        {"name": "Acme Corp", "careers_url": "https://acme.com/careers", "domain": "acme.com"},
    ])
    _stub_live(monkeypatch, True)
    existing = [{"name": "Something Else", "domain": "acme.com"}]

    result = asyncio.run(vs.merge_seed_companies(existing))

    assert result is None


def test_merge_skips_company_already_present_by_name(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch, [
        {"name": "Acme Corp", "careers_url": "https://acme.com/careers", "domain": "acme.com"},
    ])
    _stub_live(monkeypatch, True)
    existing = [{"name": "Acme Corp", "domain": "different-domain.com"}]

    result = asyncio.run(vs.merge_seed_companies(existing))

    assert result is None


def test_merge_falls_back_to_discovery_when_careers_url_missing(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch, [
        {"name": "Acme Corp", "careers_url": None, "domain": "acme.com"},
    ])
    _stub_live(monkeypatch, True)

    async def fake_discover(domain):
        assert domain == "acme.com"
        return "https://acme.com/jobs"

    monkeypatch.setattr(vs, "discover_careers_url", fake_discover)

    result = asyncio.run(vs.merge_seed_companies([]))

    assert result[0]["careers_url"] == "https://acme.com/jobs"


def test_merge_drops_seed_when_discovery_fails(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch, [
        {"name": "Acme Corp", "careers_url": None, "domain": "acme.com"},
    ])
    _stub_live(monkeypatch, True)

    async def fake_discover(domain):
        return None

    monkeypatch.setattr(vs, "discover_careers_url", fake_discover)

    result = asyncio.run(vs.merge_seed_companies([]))

    assert result is None


def test_merge_drops_seed_when_hardcoded_url_is_dead(tmp_path, monkeypatch):
    """A hardcoded seed careers_url that no longer resolves (ATS moved it,
    typo, etc.) must not be blindly trusted into companies.json."""
    _seed(tmp_path, monkeypatch, [
        {"name": "Acme Corp", "careers_url": "https://acme.com/careers", "domain": "acme.com"},
    ])
    _stub_live(monkeypatch, False)

    result = asyncio.run(vs.merge_seed_companies([]))

    assert result is None
    assert state.load_companies() == []


def test_merge_returns_none_and_does_not_write_when_nothing_new(tmp_path, monkeypatch):
    companies_file = _seed(tmp_path, monkeypatch, [
        {"name": "Acme Corp", "careers_url": "https://acme.com/careers", "domain": "acme.com"},
    ])
    _stub_live(monkeypatch, True)
    existing = [{"name": "Acme Corp", "domain": "acme.com"}]

    result = asyncio.run(vs.merge_seed_companies(existing))

    assert result is None
    assert not companies_file.exists()


def test_url_is_live_rejects_unsafe_url(monkeypatch):
    monkeypatch.setattr(vs, "is_safe_url", lambda u: False)
    assert asyncio.run(vs._url_is_live("https://example.com")) is False


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
