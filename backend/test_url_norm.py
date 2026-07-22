"""Tests for url_norm.normalize_url — tracking, fragments, ports, case.

Run: pytest backend/test_url_norm.py
"""
import pytest

from url_norm import normalize_url


def test_strips_utm_source():
    assert normalize_url("https://example.com/jobs/123?utm_source=feed") == \
        "https://example.com/jobs/123"


def test_bare_utm_param_kept():
    """`utm_source/utm_medium/utm_campaign` are the canonical UTM keys;
    bare `utm=feed` is rare and not in the tracking set (would risk
    overwriting a legitimately-named param). Stays as extraneous noise."""
    assert normalize_url("https://example.com/jobs?utm=feed") == \
        "https://example.com/jobs?utm=feed"


def test_strips_all_utm_params():
    assert normalize_url(
        "https://example.com/jobs/123?utm_source=x&utm_medium=y&utm_campaign=z"
    ) == "https://example.com/jobs/123"


def test_strips_fbclid():
    assert normalize_url("https://example.com/jobs/123?fbclid=AbC") == \
        "https://example.com/jobs/123"


def test_strips_mixed_tracking_params():
    assert normalize_url(
        "https://example.com/jobs/123?utm_source=x&fbclid=abc&legit=ok"
    ) == "https://example.com/jobs/123?legit=ok"


def test_tracking_param_case_insensitive():
    assert normalize_url("https://example.com/jobs?UTM_Source=x") == \
        "https://example.com/jobs"
    assert normalize_url("https://example.com/jobs?FbClId=abc") == \
        "https://example.com/jobs"


def test_strips_fragment():
    assert normalize_url("https://example.com/jobs/123#apply") == \
        "https://example.com/jobs/123"


def test_strips_default_https_port():
    assert normalize_url("https://example.com:443/jobs/123") == \
        "https://example.com/jobs/123"


def test_strips_default_http_port():
    assert normalize_url("http://example.com:80/jobs/123") == \
        "http://example.com/jobs/123"


def test_keeps_nondefault_port():
    assert normalize_url("https://example.com:8080/jobs/123") == \
        "https://example.com:8080/jobs/123"


def test_lowercases_scheme():
    assert normalize_url("HTTPS://example.com/jobs/123") == \
        "https://example.com/jobs/123"


def test_lowercases_host():
    assert normalize_url("https://EXAMPLE.com/jobs/123") == \
        "https://example.com/jobs/123"


def test_path_case_preserved():
    """Many sites have case-sensitive paths (GitHub blob URLs, file
    hosts, some ATS boards). Don't touch them."""
    assert normalize_url("https://example.com/Jobs?utm_source=1") == \
        "https://example.com/Jobs"
    assert normalize_url("https://github.com/User/Repo/blob/main/README.md") == \
        "https://github.com/User/Repo/blob/main/README.md"


def test_www_prefix_stripped():
    assert normalize_url("https://www.example.com/jobs/123") == \
        "https://example.com/jobs/123"


def test_trailing_slash_collapsed():
    assert normalize_url("https://example.com/jobs/123/") == \
        "https://example.com/jobs/123"


def test_root_path_kept():
    assert normalize_url("https://example.com/") == "https://example.com/"


def test_multi_slash_collapse():
    assert normalize_url("https://example.com/jobs/123///") == \
        "https://example.com/jobs/123"


def test_query_param_order_is_stable():
    # Same set, different order == same canonical form.
    a = normalize_url("https://example.com/jobs?b=2&a=1")
    b = normalize_url("https://example.com/jobs?a=1&b=2")
    assert a == b


def test_remaining_query_sorted():
    assert normalize_url("https://example.com/jobs?z=1&a=2") == \
        "https://example.com/jobs?a=2&z=1"


def test_userinfo_stripped():
    assert normalize_url("https://user:pass@example.com/jobs/123") == \
        "https://example.com/jobs/123"


def test_idempotent_across_combined_features():
    url = "https://Example.com:443/Jobs/123/?utm_source=feed#apply"
    once = normalize_url(url)
    twice = normalize_url(once)
    assert once == twice


def test_duplicate_form_collapse():
    """Two hrefs differing only by tracking params should normalize to
    one canonical URL — the dedup-helper premise used by _extract_jobs
    in scraper.py."""
    bare = "https://example.com/jobs/123"
    assert normalize_url("https://example.com/jobs/123?utm_source=feed") == bare
    assert normalize_url("https://example.com/jobs/123?fbclid=abc") == bare
    assert normalize_url("https://example.com/jobs/123?ref=x") == bare


def test_no_path():
    assert normalize_url("https://example.com") == "https://example.com/"


def test_preserves_repeated_non_tracking_param():
    # parse_qsl + urlencode(..., doseq=True) round-trips repeated keys.
    assert normalize_url("https://example.com/jobs?tag=a&tag=b") == \
        "https://example.com/jobs?tag=a&tag=b"


def test_strips_hubspot_and_branch_params():
    assert normalize_url(
        "https://example.com/jobs?_hsenc=abc&_hsmi=def&_branch_match_id=ghi"
    ) == "https://example.com/jobs"


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
