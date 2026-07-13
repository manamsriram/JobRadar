"""Offline unit checks for filter + dedup. Run: cd backend && python -m pytest"""
from filter import matches
from state import get_new_jobs


def _job(title, location="San Francisco, CA"):
    return {"title": title, "location": location}


def test_matches_new_grad_swe_in_sf():
    assert matches(_job("Software Engineer, New Grad")) is True


def test_excludes_senior():
    assert matches(_job("Senior Software Engineer")) is False


def test_excludes_wrong_location():
    assert matches(_job("Software Engineer", location="Tokyo, Japan")) is False


def test_excludes_non_matching_title():
    assert matches(_job("Product Designer")) is False


def test_get_new_jobs_returns_only_unseen():
    seen = {"a": {}, "b": {}}
    fetched = [{"id": "a"}, {"id": "c"}]
    assert get_new_jobs(seen, fetched) == [{"id": "c"}]
