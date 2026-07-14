"""Offline unit checks for filter + dedup. Run: cd backend && python -m pytest"""
from datetime import datetime, timedelta, timezone

from filter import matches
from state import get_new_jobs


def _iso(days_ago: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


def _job(title, location="San Francisco, CA", posted_at=None, description=""):
    return {
        "title": title, "location": location,
        "posted_at": posted_at, "description": description,
    }


def test_matches_new_grad_swe_in_sf():
    assert matches(_job("Software Engineer, New Grad")) is True


def test_excludes_senior():
    assert matches(_job("Senior Software Engineer")) is False


def test_excludes_wrong_location():
    assert matches(_job("Software Engineer", location="Tokyo, Japan")) is False


def test_excludes_non_us_remote():
    # Bare "remote"/"hybrid" must not smuggle in non-US roles.
    for loc in ("India - Remote", "Remote - Brussels", "Seoul, South Korea (Hybrid)",
                "Sydney (Hybrid)", "Toronto, CAN-Remote", "London, UK"):
        assert matches(_job("Software Engineer", location=loc)) is False


def test_keeps_us_remote_and_multi_city():
    for loc in ("Remote - US", "US-Remote", "Remote", "Remote in the US",
                "Chicago, Toronto, Atlanta", "SF, New York, Seattle, Dublin"):
        assert matches(_job("Software Engineer", location=loc)) is True


def test_excludes_non_matching_title():
    assert matches(_job("Product Designer")) is False


def test_excludes_experience_and_level_in_title():
    assert matches(_job("Software Engineer, 3+ years")) is False
    assert matches(_job("Software Engineer II")) is False
    assert matches(_job("Experienced Software Engineer")) is False


def test_recency_window():
    # No date → treated as fresh; within 14 days passes; older fails.
    assert matches(_job("Software Engineer")) is True
    assert matches(_job("Software Engineer", posted_at=_iso(10))) is True
    assert matches(_job("Software Engineer", posted_at=_iso(20))) is False


def test_excludes_senior_experience_in_description():
    # Clean title, but body demands 5+ years → dropped.
    assert matches(_job("Software Engineer", description="Requires 5+ years of experience")) is False
    # New-grad body with no strong signal → kept.
    assert matches(_job("Software Engineer", description="New grad friendly, 0-2 years")) is True


def test_get_new_jobs_returns_only_unseen():
    seen = {"a": {}, "b": {}}
    fetched = [{"id": "a"}, {"id": "c"}]
    assert get_new_jobs(seen, fetched) == [{"id": "c"}]
