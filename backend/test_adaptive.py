"""Adaptive selector fallback tests.

Run: pytest backend/test_adaptive.py
"""
import pytest

from adaptive import fallback_hrefs, learn_prefix


def test_learn_prefix_picks_majority_pattern():
    hrefs = [
        "https://acme.com/jobs/backend-eng",
        "https://acme.com/jobs/frontend-eng",
        "https://acme.com/jobs/pm",
    ]
    assert learn_prefix(hrefs) == "/jobs"


def test_learn_prefix_none_when_no_clear_pattern():
    hrefs = ["https://acme.com/x/1", "https://acme.com/y/2"]
    assert learn_prefix(hrefs) is None


def test_learn_prefix_none_on_empty_list():
    assert learn_prefix([]) is None


def test_fallback_hrefs_matches_learned_prefix():
    all_hrefs = [
        "/jobs/backend-eng", "/careers/team", "/jobs/pm", "/about",
    ]
    assert fallback_hrefs(all_hrefs, "/jobs") == ["/jobs/backend-eng", "/jobs/pm"]


def test_fallback_hrefs_empty_when_prefix_absent():
    assert fallback_hrefs(["/about", "/contact"], "/roles") == []


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
