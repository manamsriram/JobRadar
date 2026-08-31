"""Board scraper tests: pure HTML-parsing functions only — no live network
calls (see backend/scraper.py's test_scraper.py for the same convention).

Run: cd backend && python -m pytest test_board_scraper.py -v
"""
from scrapers import board_scraper as bs


_HIRINGCAFE_CARD = """
<div class="relative bg-white rounded-xl border border-gray-200 shadow">
  <div class="flex flex-col w-full">
    <div class="mt-1 mt-14 md:mt-1 md:mr-10">
      <span class="w-full font-bold text-start line-clamp-2">Software Engineer II (AI/ML)</span>
    </div>
    <div class="mt-1 flex items-center space-x-1 rounded text-xs px-1 font-medium border bg-gray-50 w-fit text-gray-700">
      <span class="line-clamp-2">Plano or Charlotte</span>
    </div>
  </div>
  <div class="flex flex-col mt-4 mb-2 space-y-2.5 text-sm w-full">
    <div class="flex mb-4 mt-2 md:my-0 w-full items-center space-x-4 md:space-x-3 lg:space-x-2">
      <span class="line-clamp-3 font-light">
        <span class="font-bold">Bank of America</span>: Provides global banking services.
      </span>
    </div>
  </div>
  <a href="https://hiringcafe.com/job/software-engineer-ii-ai-ml-bank-of-america-plano-texas-7hwrzwj7zoza1qft">Job Posting</a>
</div>
"""


def test_parse_hiringcafe_extracts_title_company_location_url():
    jobs = bs._parse_hiringcafe(_HIRINGCAFE_CARD)
    assert len(jobs) == 1
    job = jobs[0]
    assert job["title"] == "Software Engineer II (AI/ML)"
    assert job["company"] == "Bank of America"
    assert job["location"] == "Plano or Charlotte"
    assert job["url"] == "https://hiringcafe.com/job/software-engineer-ii-ai-ml-bank-of-america-plano-texas-7hwrzwj7zoza1qft"
    assert job["source"] == "hiringcafe"
    assert job["id"]


def test_parse_hiringcafe_skips_card_without_job_link():
    jobs = bs._parse_hiringcafe('<div class="relative bg-white rounded-xl border">no link here</div>')
    assert jobs == []


def test_parse_hiringcafe_handles_empty_html():
    assert bs._parse_hiringcafe("") == []


def test_parse_hiringcafe_resolves_relative_job_url():
    card = _HIRINGCAFE_CARD.replace(
        'href="https://hiringcafe.com/job/software-engineer-ii-ai-ml-bank-of-america-plano-texas-7hwrzwj7zoza1qft"',
        'href="/job/software-engineer-ii-ai-ml-bank-of-america-plano-texas-7hwrzwj7zoza1qft"',
    )
    jobs = bs._parse_hiringcafe(card, base_url="https://hiringcafe.com/search?q=swe")
    assert jobs[0]["url"] == "https://hiringcafe.com/job/software-engineer-ii-ai-ml-bank-of-america-plano-texas-7hwrzwj7zoza1qft"


_NEWGRAD_ROW = """
<table>
<tbody>
<tr class="index_tableRow___byxr">
  <td><span class="index_indexCell__2L7Ty">1</span></td>
  <td><span class="index_positionTitle__xrG_i">Network Analyst</span></td>
  <td><span>1 hour ago</span></td>
  <td><a class="index_airtableApplyLink__Dob0_" href="https://jobright.ai/jobs/info/6a26f59d7d827633afff7ad2">Apply</a></td>
  <td><span>On Site</span></td>
  <td><span class="index_cellText__hfa_t">Chattanooga, TN</span></td>
  <td><span class="index_cellText__hfa_t">Peraton</span></td>
</tr>
</tbody>
</table>
"""


def test_parse_newgrad_rows_extracts_title_company_location_url():
    jobs = bs._parse_newgrad_rows(_NEWGRAD_ROW)
    assert len(jobs) == 1
    job = jobs[0]
    assert job["title"] == "Network Analyst"
    assert job["company"] == "Peraton"
    assert job["location"] == "Chattanooga, TN"
    assert job["url"] == "https://jobright.ai/jobs/info/6a26f59d7d827633afff7ad2"
    assert job["source"] == "newgrad-jobs"


def test_parse_newgrad_rows_skips_row_missing_apply_link():
    row = _NEWGRAD_ROW.replace(
        '<a class="index_airtableApplyLink__Dob0_" href="https://jobright.ai/jobs/info/6a26f59d7d827633afff7ad2">Apply</a>',
        "",
    )
    assert bs._parse_newgrad_rows(row) == []


def test_parse_newgrad_rows_handles_empty_html():
    assert bs._parse_newgrad_rows("") == []


def test_search_url_includes_query_and_past_24h_filter():
    url = bs._search_url("(swe) (site:greenhouse.io)")
    assert "site%3Agreenhouse.io" in url or "site:greenhouse.io" in url
    assert "tbs=qdr%3Ad" in url or "tbs=qdr:d" in url
    assert url.startswith("https://www.google.com/search?")


def test_build_queries_excludes_senior_level_terms():
    q = bs._build_google_queries(["swe"], ["lever.co"])[0]
    assert "-senior" in q


_SERP_HTML = """
<html><body>
<a href="https://boards.greenhouse.io/acme/jobs/123">Software Engineer New Grad at Acme</a>
<a href="https://www.google.com/search?q=unrelated">unrelated google link</a>
<a href="https://otherdomain.com/careers/456">not the ATS domain</a>
</body></html>
"""


def test_parse_google_serp_keeps_only_matching_domain_links():
    jobs = bs._parse_google_serp(_SERP_HTML, ["greenhouse.io"])
    assert len(jobs) == 1
    assert jobs[0]["url"] == "https://boards.greenhouse.io/acme/jobs/123"
    assert jobs[0]["title"] == "Software Engineer New Grad at Acme"
    assert jobs[0]["source"] == "google-search"


def test_parse_google_serp_handles_empty_html():
    assert bs._parse_google_serp("", ["greenhouse.io"]) == []


import asyncio

import pytest


@pytest.mark.parametrize("fetcher", [bs.fetch_handshake, bs.fetch_jobright, bs.fetch_simplify])
def test_login_gated_stubs_raise_not_implemented(fetcher):
    # _run()'s dispatch (board_scraper.py) specifically catches
    # NotImplementedError to skip unfinished boards without aborting the
    # run — if a stub's exception type ever drifted, that catch would stop
    # working silently.
    with pytest.raises(NotImplementedError):
        asyncio.run(fetcher("https://example.com"))


def test_login_page_raises_clear_error_when_session_not_captured(monkeypatch, tmp_path):
    monkeypatch.setattr(bs, "AUTH_STATE_PATH", str(tmp_path / "auth_state.json"))
    with pytest.raises(FileNotFoundError, match="save_login_session.py"):
        asyncio.run(bs._login_page(None))


# ---- Google boolean search ----

def test_title_groups_cover_every_title_exactly_once():
    titles = [f"t{i}" for i in range(34)]
    groups = bs._title_groups(titles, per_group=5)
    flat = [t for g in groups for t in g]
    assert flat == titles
    assert len(groups) == 7


def test_every_planned_query_fits_googles_word_cap():
    """The whole point of sharding: Google silently drops everything past
    32 words, so no generated query may exceed the budget."""
    for hour in range(24):
        for q in bs.plan_google_queries(hour=hour):
            assert len(q.split()) <= bs.GOOGLE_MAX_QUERY_TOKENS, q


def test_planned_queries_cover_all_ats_domains():
    domains_seen = set()
    for q in bs.plan_google_queries(hour=0):
        domains_seen |= {d for d in bs.ATS_DOMAINS if f"site:{d}" in q}
    assert domains_seen == set(bs.ATS_DOMAINS)


def test_title_group_rotates_across_the_day():
    first = bs.plan_google_queries(hour=0)[0]
    later = bs.plan_google_queries(hour=3)[0]
    assert first != later


def test_build_queries_splits_when_domains_overflow_budget():
    many = [f"domain{i}.example.com" for i in range(30)]
    queries = bs._build_google_queries(["swe"], many)
    assert len(queries) > 1
    assert all(len(q.split()) <= bs.GOOGLE_MAX_QUERY_TOKENS for q in queries)


def test_serp_parser_keeps_only_target_ats_links():
    html = """
    <a href="https://jobs.lever.co/acme/123">Software Engineer, New Grad</a>
    <a href="https://example.com/blog">Some unrelated article</a>
    <a href="https://boards.greenhouse.io/acme/jobs/456">Backend Engineer I</a>
    <a href="https://jobs.lever.co/acme/123">Software Engineer, New Grad</a>
    <a href="https://jobs.lever.co/acme/789">Hi</a>
    """
    jobs = bs._parse_google_serp(html, ["lever.co", "greenhouse.io"])
    urls = [j["url"] for j in jobs]
    assert urls == [
        "https://jobs.lever.co/acme/123",
        "https://boards.greenhouse.io/acme/jobs/456",
    ]
    assert all(j["source"] == "google-search" for j in jobs)


def test_captcha_detection():
    assert bs._is_captcha("https://www.google.com/sorry/index?continue=x", "")
    assert bs._is_captcha("https://www.google.com/search?q=x", "<a href='/sorry/index'>")
    assert not bs._is_captcha("https://www.google.com/search?q=x", "<html>results</html>")


def test_backoff_blocks_then_expires():
    from datetime import datetime, timedelta, timezone
    now = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)
    health = bs.mark_google_blocked({}, now=now)
    assert bs.is_google_backing_off(health, now=now + timedelta(hours=1))
    assert not bs.is_google_backing_off(
        health, now=now + timedelta(hours=bs.GOOGLE_BLOCK_BACKOFF_HOURS + 1)
    )


def test_corrupt_blocked_until_does_not_wedge_source_off():
    assert not bs.is_google_backing_off({"google-search": {"blocked_until": "not-a-date"}})


def test_clear_google_block_is_safe_when_never_blocked():
    assert bs.clear_google_block({}) == {}
    health = bs.clear_google_block(bs.mark_google_blocked({}))
    assert "blocked_until" not in health["google-search"]
