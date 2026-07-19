"""signals/company_discovery.py tests.

Run: pytest backend/test_company_discovery.py
"""
import asyncio

import pytest

import state
from signals import company_discovery as cd

_ENTRY = {"company": "Acme AI", "domain": "acmeai.com", "careers_url": "https://acmeai.com/careers"}


def _run(coro):
    return asyncio.run(coro)


# ---- funding_promotion ----

def test_promotes_on_matched_job(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "COMPANIES_FILE", tmp_path / "companies.json")
    companies, added = _run(cd._from_funding([], [(_ENTRY, [{"matched": True}])], budget=5))
    assert added == 1
    assert state.load_companies() == companies
    row = companies[-1]
    assert row["name"] == "Acme AI"
    assert row["domain"] == "acmeai.com"
    assert row["careers_url"] == "https://acmeai.com/careers"
    assert row["source"] == "auto-discovered"
    assert row["tier"] == 3
    assert row["requires_js"] is False
    assert row["discovery_method"] == "funding_promotion"
    assert "discovered_at" in row


def test_funding_skips_when_no_job_matched():
    companies, added = _run(cd._from_funding([], [(_ENTRY, [{"matched": False}])], budget=5))
    assert added == 0
    assert companies == []


def test_funding_skips_when_missing_company_or_domain_or_url():
    entry = {"company": "Acme", "domain": "acme.com"}  # no careers_url
    companies, added = _run(cd._from_funding([], [(entry, [{"matched": True}])], budget=5))
    assert added == 0


def test_funding_skips_domain_duplicate_case_insensitive():
    existing = [{"name": "Other", "domain": "ACMEAI.com"}]
    companies, added = _run(cd._from_funding(existing, [(_ENTRY, [{"matched": True}])], budget=5))
    assert added == 0


def test_funding_skips_name_duplicate_case_insensitive():
    existing = [{"name": "acme ai", "domain": "other.com"}]
    companies, added = _run(cd._from_funding(existing, [(_ENTRY, [{"matched": True}])], budget=5))
    assert added == 0


def test_funding_respects_budget(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "COMPANIES_FILE", tmp_path / "companies.json")
    entries = [
        ({"company": f"Co{i}", "domain": f"co{i}.com", "careers_url": f"https://co{i}.com/careers"},
         [{"matched": True}])
        for i in range(3)
    ]
    companies, added = _run(cd._from_funding([], entries, budget=2))
    assert added == 2
    assert len(companies) == 2


# ---- yc_frequency ----

def test_yc_frequency_needs_two_cycles(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "DISCOVERY_COUNTS_FILE", tmp_path / "counts.json")
    monkeypatch.setattr(state, "COMPANIES_FILE", tmp_path / "companies.json")

    async def _fake_discover(domain):
        return f"https://{domain}/careers"

    async def _fake_has_links(url):
        return True

    monkeypatch.setattr(cd, "discover_careers_url", _fake_discover)
    monkeypatch.setattr(cd, "_has_job_links", _fake_has_links)

    yc_jobs = [{"company": "Widgetly", "source": "yc"}]
    companies, added = _run(cd._from_yc_frequency([], yc_jobs, budget=5))
    assert added == 0  # first cycle only sets the count to 1

    companies, added = _run(cd._from_yc_frequency([], yc_jobs, budget=5))
    assert added == 1
    assert companies[-1]["discovery_method"] == "yc_frequency"
    assert companies[-1]["tier"] == 3


def test_yc_frequency_skips_already_known_name(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "DISCOVERY_COUNTS_FILE", tmp_path / "counts.json")
    existing = [{"name": "Widgetly Inc", "domain": "widgetly.com"}]
    yc_jobs = [{"company": "Widgetly", "source": "yc"}]
    companies, added = _run(cd._from_yc_frequency(existing, yc_jobs, budget=5))
    assert added == 0


# ---- url_domain_extraction ----

def test_url_domain_extraction_skips_aggregator_hosts():
    jobs = [{"url": "https://www.linkedin.com/jobs/view/123", "company": "Acme"}]
    companies, added = _run(cd._from_url_domains([], jobs, budget=5))
    assert added == 0


def test_url_domain_extraction_greenhouse_uses_board_slug(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "COMPANIES_FILE", tmp_path / "companies.json")

    async def _fake_has_links(url):
        return True

    monkeypatch.setattr(cd, "_has_job_links", _fake_has_links)

    jobs = [{"url": "https://boards.greenhouse.io/acme/jobs/12345", "company": "Acme"}]
    companies, added = _run(cd._from_url_domains([], jobs, budget=5))
    assert added == 1
    row = companies[-1]
    assert row["domain"] == "boards.greenhouse.io/acme"
    assert row["careers_url"] == "https://boards.greenhouse.io/acme"
    assert row["discovery_method"] == "url_domain_extraction"


def test_url_domain_extraction_plain_domain_uses_careers_discovery(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "COMPANIES_FILE", tmp_path / "companies.json")

    async def _fake_discover(domain):
        return f"https://{domain}/careers"

    async def _fake_has_links(url):
        return False  # exercises requires_js=True

    monkeypatch.setattr(cd, "discover_careers_url", _fake_discover)
    monkeypatch.setattr(cd, "_has_job_links", _fake_has_links)

    jobs = [{"url": "https://acme.com/jobs/backend-engineer", "company": "Acme"}]
    companies, added = _run(cd._from_url_domains([], jobs, budget=5))
    assert added == 1
    assert companies[-1]["domain"] == "acme.com"
    assert companies[-1]["requires_js"] is True


def test_url_domain_extraction_skips_known_domain():
    existing = [{"name": "Acme", "domain": "acme.com"}]
    jobs = [{"url": "https://acme.com/jobs/1", "company": "Acme"}]
    companies, added = _run(cd._from_url_domains(existing, jobs, budget=5))
    assert added == 0


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
