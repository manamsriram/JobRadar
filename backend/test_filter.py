"""Offline unit checks for filter + dedup. Run: cd backend && python -m pytest"""
from datetime import datetime, timedelta, timezone

from filter import matches
from state import get_new_jobs


def _iso(days_ago: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


def _job(title, location="San Francisco, CA", posted_at=None):
    return {"title": title, "location": location, "posted_at": posted_at}


def test_matches_new_grad_swe_in_sf():
    assert matches(_job("Software Engineer, New Grad")) is True


def test_excludes_senior():
    assert matches(_job("Senior Software Engineer")) is False


def test_excludes_wrong_location():
    assert matches(_job("Software Engineer", location="Tokyo, Japan")) is False


def test_excludes_non_matching_title():
    assert matches(_job("Product Designer")) is False


def test_excludes_experience_and_level_in_title():
    assert matches(_job("Software Engineer, 3+ years")) is False
    assert matches(_job("Software Engineer II")) is False
    assert matches(_job("Experienced Software Engineer")) is False


def test_recency_window():
    # No date → treated as fresh; recent passes; older than 3 days fails.
    assert matches(_job("Software Engineer")) is True
    assert matches(_job("Software Engineer", posted_at=_iso(1))) is True
    assert matches(_job("Software Engineer", posted_at=_iso(10))) is False


def test_get_new_jobs_returns_only_unseen():
    seen = {"a": {}, "b": {}}
    fetched = [{"id": "a"}, {"id": "c"}]
    assert get_new_jobs(seen, fetched) == [{"id": "c"}]
