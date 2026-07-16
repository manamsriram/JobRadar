"""Filter tests: new-grad title matches, seniority excluded, US-only enforced.

Run: pytest backend/test_filter.py  (or python backend/test_filter.py for asserts)
"""
from datetime import datetime, timezone

from filter import matches

_FRESH = datetime.now(timezone.utc).isoformat()


def _job(**kw) -> dict:
    base = {"title": "Software Engineer", "location": "San Jose, CA",
            "posted_at": _FRESH, "description": ""}
    base.update(kw)
    return base


def test_newgrad_title_matches():
    assert matches(_job(title="Software Engineer", location="San Jose, CA"))
    assert matches(_job(title="Backend Engineer", location="Remote, US"))


def test_seniority_excluded():
    assert not matches(_job(title="Senior Software Engineer"))
    assert not matches(_job(title="Staff Software Engineer"))
    assert not matches(_job(title="Software Engineer III"))


def test_non_title_rejected():
    assert not matches(_job(title="Product Manager"))
    assert not matches(_job(title="Sales Representative"))


def test_non_us_location_rejected():
    assert not matches(_job(location="London, UK"))
    assert not matches(_job(location="Remote - Bengaluru, India"))
    assert not matches(_job(location="Toronto, Canada"))


def test_us_and_remote_accepted():
    assert matches(_job(location="Milpitas, CA"))
    assert matches(_job(location="Remote (US)"))
    assert matches(_job(location="Remote"))  # plain remote, no non-US marker


def test_stale_posting_rejected():
    assert not matches(_job(posted_at="2020-01-01T00:00:00+00:00"))


def test_desc_exclude_drops_senior_body():
    assert not matches(_job(description="Requires 8+ years of experience."))


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("all filter tests passed")
