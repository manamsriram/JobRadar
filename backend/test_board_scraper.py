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


def test_build_google_query_includes_domain_and_date_filter():
    q = bs._build_google_query("greenhouse.io")
    assert "site%3Agreenhouse.io" in q or "site:greenhouse.io" in q
    assert "tbs=qdr%3Ad" in q or "tbs=qdr:d" in q
    assert q.startswith("https://www.google.com/search?")


def test_build_google_query_excludes_senior_level_terms():
    q = bs._build_google_query("lever.co")
    assert "-senior" in q


_SERP_HTML = """
<html><body>
<a href="https://boards.greenhouse.io/acme/jobs/123">Software Engineer New Grad at Acme</a>
<a href="https://www.google.com/search?q=unrelated">unrelated google link</a>
<a href="https://otherdomain.com/careers/456">not the ATS domain</a>
</body></html>
"""


def test_parse_google_serp_keeps_only_matching_domain_links():
    jobs = bs._parse_google_serp(_SERP_HTML, "greenhouse.io")
    assert len(jobs) == 1
    assert jobs[0]["url"] == "https://boards.greenhouse.io/acme/jobs/123"
    assert jobs[0]["title"] == "Software Engineer New Grad at Acme"
    assert jobs[0]["source"] == "google-search"


def test_parse_google_serp_handles_empty_html():
    assert bs._parse_google_serp("", "greenhouse.io") == []
