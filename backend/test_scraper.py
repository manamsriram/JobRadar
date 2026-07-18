"""Funding-signal-to-company promotion tests.

Run: pytest backend/test_scraper.py
"""
import pytest

import state
from scraper import _drop_promoted_from_queue, _promote_funding_company

_ENTRY = {"company": "Acme AI", "domain": "acmeai.com", "careers_url": "https://acmeai.com/careers"}


def test_promotes_on_matched_job(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "COMPANIES_FILE", tmp_path / "companies.json")
    result = _promote_funding_company(_ENTRY, [{"matched": True}], [])
    assert result is not None
    assert state.load_companies() == result
    assert result[-1] == {
        "name": "Acme AI", "source": "custom", "careers_url": "https://acmeai.com/careers",
        "domain": "acmeai.com", "tier": 2, "requires_js": False,
    }


def test_skips_when_no_job_matched():
    assert _promote_funding_company(_ENTRY, [{"matched": False}], []) is None


def test_skips_when_missing_company_or_domain_or_url():
    assert _promote_funding_company({"company": "Acme", "domain": "acme.com"}, [{"matched": True}], []) is None
    assert _promote_funding_company({"domain": "acme.com", "careers_url": "https://acme.com/c"}, [{"matched": True}], []) is None


def test_skips_domain_duplicate_case_insensitive():
    existing = [{"name": "Other", "domain": "ACMEAI.com"}]
    assert _promote_funding_company(_ENTRY, [{"matched": True}], existing) is None


def test_skips_name_duplicate_case_insensitive():
    existing = [{"name": "acme ai", "domain": "other.com"}]
    assert _promote_funding_company(_ENTRY, [{"matched": True}], existing) is None


def test_drop_promoted_from_queue_removes_matching_domain():
    queue = [{"domain": "acmeai.com"}, {"domain": "other.com"}]
    companies = [{"domain": "AcmeAI.com"}]
    assert _drop_promoted_from_queue(queue, companies) == [{"domain": "other.com"}]


def test_drop_promoted_from_queue_keeps_unrelated_entries():
    queue = [{"domain": "other.com"}]
    assert _drop_promoted_from_queue(queue, []) == queue


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
