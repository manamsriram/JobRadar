"""Funding-queue-drop tests. Funding->companies.json promotion itself now
lives in signals/company_discovery.py — see test_company_discovery.py.

Run: pytest backend/test_scraper.py
"""
import asyncio

import pytest

import scraper
from config import MAX_CONSECUTIVE_ZERO_JOBS
from fetch import RetryBudget
from scraper import _drop_promoted_from_queue, _gather_sources


def test_drop_promoted_from_queue_removes_matching_domain():
    queue = [{"domain": "acmeai.com"}, {"domain": "other.com"}]
    companies = [{"domain": "AcmeAI.com"}]
    assert _drop_promoted_from_queue(queue, companies) == [{"domain": "other.com"}]


def test_drop_promoted_from_queue_keeps_unrelated_entries():
    queue = [{"domain": "other.com"}]
    assert _drop_promoted_from_queue(queue, []) == queue


def test_gather_sources_skips_source_past_zero_job_threshold(monkeypatch):
    monkeypatch.setattr(scraper, "fetch_yc", lambda retries: _async_result(([], True)))
    calls = []

    async def fake_scrape_company(company, budget=None):
        calls.append(company["name"])
        return [], True

    monkeypatch.setattr(scraper, "_scrape_company", fake_scrape_company)
    companies = [{"name": "Acme"}]
    health = {"company:Acme": {"consecutive_zero_jobs": MAX_CONSECUTIVE_ZERO_JOBS}}

    jobs, updates = asyncio.run(_gather_sources(companies, RetryBudget(10), health))

    assert calls == []
    assert "company:Acme" not in updates
    assert health["company:Acme"]["skip_streak"] == 1


def test_gather_sources_probes_source_after_skip_streak_expires(monkeypatch):
    monkeypatch.setattr(scraper, "fetch_yc", lambda retries: _async_result(([], True)))
    calls = []

    async def fake_scrape_company(company, budget=None):
        calls.append(company["name"])
        return [], True

    monkeypatch.setattr(scraper, "_scrape_company", fake_scrape_company)
    companies = [{"name": "Acme"}]
    # skip_streak one below the retry threshold: this cycle should probe
    # instead of skipping again, so a source that started posting again
    # doesn't stay skipped forever.
    health = {
        "company:Acme": {
            "consecutive_zero_jobs": MAX_CONSECUTIVE_ZERO_JOBS,
            "skip_streak": MAX_CONSECUTIVE_ZERO_JOBS - 1,
        }
    }

    jobs, updates = asyncio.run(_gather_sources(companies, RetryBudget(10), health))

    assert calls == ["Acme"]
    assert "company:Acme" in updates
    assert health["company:Acme"]["skip_streak"] == 0


async def _async_result(value):
    return value


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
