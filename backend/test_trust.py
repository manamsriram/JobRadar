"""Trust/legitimacy scoring tests: score_posting flags, never drops.

Run: pytest backend/test_trust.py
"""
import pytest

import trust


def test_missing_url_flagged():
    assert trust.score_posting({"url": ""}, None) == "missing url"


def test_link_shortener_flagged():
    reason = trust.score_posting({"url": "https://bit.ly/abc123"}, None)
    assert reason == "link-shortener domain"


def test_custom_source_domain_mismatch_flagged():
    job = {"url": "https://sketchy-clone.example/jobs/1", "source": "custom"}
    company = {"domain": "plaid.com"}
    reason = trust.score_posting(job, company)
    assert reason == "url host sketchy-clone.example doesn't match company domain plaid.com"


def test_custom_source_matching_domain_not_flagged():
    job = {"url": "https://plaid.com/careers/openings/1", "source": "custom"}
    company = {"domain": "plaid.com"}
    assert trust.score_posting(job, company) is None


def test_custom_source_subdomain_not_flagged():
    job = {"url": "https://jobs.plaid.com/openings/1", "source": "custom"}
    company = {"domain": "plaid.com"}
    assert trust.score_posting(job, company) is None


def test_yc_source_skips_domain_check():
    job = {"url": "https://ycombinator.com/companies/x/jobs/1", "source": "yc"}
    company = {"domain": "plaid.com"}
    assert trust.score_posting(job, company) is None


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
