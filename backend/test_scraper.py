"""Funding-queue-drop tests. Funding->companies.json promotion itself now
lives in signals/company_discovery.py — see test_company_discovery.py.

Run: pytest backend/test_scraper.py
"""
import pytest

from scraper import _drop_promoted_from_queue


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
