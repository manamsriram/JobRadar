# Job Board Aggregation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Scrape Sriram's filtered views on Handshake, Jobright, Simplify, HiringCafe, the Jobright new-grad minisite, and a Google ATS-domain boolean search, and funnel every result through JobRadar's existing match/AI-gate/alert pipeline so applying happens from one place.

**Architecture:** A new `backend/scrapers/board_scraper.py` module (separate from the existing `playwright_scraper.py`, which owns company-career-page scraping) adds one fetch function per board, all producing the same job-dict shape already used everywhere else in the codebase. A new daily GitHub Actions workflow runs it and POSTs to the existing `/api/ingest` endpoint — no new backend pipeline. A cross-source dedup check is added to `state.py` so the same posting seen on two boards doesn't create two rows. A small frontend widget surfaces per-source health (already tracked, just not shown anywhere today).

**Tech Stack:** Python (httpx, BeautifulSoup, Playwright — all already dependencies), pytest, React/TypeScript (existing frontend stack), GitHub Actions.

## Global Constraints

- Follow existing repo conventions exactly: docstring style (module-level explaining *why*, not *what*), `_UA`/`_LAUNCH_ARGS` constants duplicated per scraper file (matches `playwright_scraper.py`'s existing pattern — no premature shared-util extraction for 3 lines), atomic JSON writes via `state._write_json_atomic*`, best-effort/non-blocking error handling on every network call (catch, log, return `[]`/`""`, never raise past the scrape boundary).
- New Python code goes in `backend/`; run tests from there (`cd backend && python -m pytest <file> -v`, using the repo's `.venv`).
- No new dependencies — `httpx`, `beautifulsoup4`, `playwright` are already in `backend/requirements.txt`.
- Every network-touching function must have a pure, unit-testable parsing/query-building counterpart (`_parse_x(html) -> list[dict]`, `_build_x_query(...) -> str`) — this is how these get tested without live network calls in CI.
- Job dicts everywhere use this exact shape (matches every existing scraper): `{"id": str, "title": str, "company": str, "location": str, "url": str, "source": str, "posted_at": None, "description": ""}`.

---

### Task 1: Cross-source dedup helper in `state.py`

**Files:**
- Modify: `backend/state.py` (add function near `get_new_jobs`, backend/state.py:75-77)
- Test: `backend/test_state.py` (add near existing `test_get_new_jobs_*` tests, backend/test_state.py:16-24)

**Interfaces:**
- Produces: `state.find_cross_source_duplicate(seen: dict, job: dict) -> str | None` — returns the existing job's id in `seen` if `job` matches on `(company, title, location)` case-insensitively, else `None`. Empty `company` or `title` never match (too weak a key).

- [ ] **Step 1: Write the failing tests**

```python
def test_find_cross_source_duplicate_matches_on_company_title_location():
    seen = {"a": {"company": "Acme", "title": "Software Engineer", "location": "Remote"}}
    job = {"company": "acme", "title": "software engineer", "location": "remote"}
    assert state.find_cross_source_duplicate(seen, job) == "a"


def test_find_cross_source_duplicate_no_match_returns_none():
    seen = {"a": {"company": "Acme", "title": "Software Engineer", "location": "Remote"}}
    job = {"company": "Acme", "title": "Data Scientist", "location": "Remote"}
    assert state.find_cross_source_duplicate(seen, job) is None


def test_find_cross_source_duplicate_ignores_blank_company_or_title():
    seen = {"a": {"company": "", "title": "", "location": "Remote"}}
    job = {"company": "", "title": "", "location": "Remote"}
    assert state.find_cross_source_duplicate(seen, job) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest test_state.py -k find_cross_source_duplicate -v`
Expected: FAIL with `AttributeError: module 'state' has no attribute 'find_cross_source_duplicate'`

- [ ] **Step 3: Implement**

Add directly below `get_new_jobs` (backend/state.py:75-77):

```python
def find_cross_source_duplicate(seen: dict, job: dict) -> str | None:
    """Same posting scraped from two different boards has two different
    URLs (hence two different ids), so the id-based lookup in get_new_jobs
    won't catch it. This is a same-cycle O(n) scan over `seen` keyed on
    (company, title, location) instead — acceptable at this dataset size
    (hundreds, not millions, of rows)."""
    company = (job.get("company") or "").strip().lower()
    title = (job.get("title") or "").strip().lower()
    if not company or not title:
        return None
    location = (job.get("location") or "").strip().lower()
    key = (company, title, location)
    for jid, existing in seen.items():
        existing_key = (
            (existing.get("company") or "").strip().lower(),
            (existing.get("title") or "").strip().lower(),
            (existing.get("location") or "").strip().lower(),
        )
        if existing_key == key:
            return jid
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest test_state.py -k find_cross_source_duplicate -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/state.py backend/test_state.py
git commit -m "feat: add cross-source duplicate detection to state store"
```

---

### Task 2: Wire dedup into ingest and poll pipelines

**Files:**
- Modify: `backend/main.py:194-222` (`/api/ingest` loop)
- Modify: `backend/scraper.py:258-300` (`_process`)

**Interfaces:**
- Consumes: `state.find_cross_source_duplicate(seen, job) -> str | None` (Task 1)
- Produces: matched jobs whose `(company, title, location)` already exists in `seen` get merged (their source appended to the existing entry's `sources` list) instead of inserted as a new row.

- [ ] **Step 1: Modify `/api/ingest`'s loop** (backend/main.py, inside the `for job in state.get_new_jobs(seen, incoming):` loop, right before `seen[job["id"]] = job; added += 1`)

```python
        # Unmatched jobs are dropped immediately rather than persisted and
        # purged later — they're re-evaluated (cheaply) if the source still
        # lists them on the next ingest.
        if not job["matched"]:
            continue
        dup_id = state.find_cross_source_duplicate(seen, job)
        if dup_id:
            sources = seen[dup_id].setdefault("sources", [seen[dup_id]["source"]])
            if job["source"] not in sources:
                sources.append(job["source"])
            continue
        seen[job["id"]] = job
        added += 1
```

- [ ] **Step 2: Same change in `scraper.py`'s `_process`** (backend/scraper.py, replace the tail of the loop starting at `if not job["matched"]:` around line 292)

```python
        # Unmatched jobs are dropped immediately rather than persisted and
        # purged later — they're cheaply re-evaluated next cycle if the
        # source still lists them.
        if not job["matched"]:
            continue
        dup_id = state.find_cross_source_duplicate(seen, job)
        if dup_id:
            sources = seen[dup_id].setdefault("sources", [seen[dup_id]["source"]])
            if job["source"] not in sources:
                sources.append(job["source"])
            continue
        company_name = job.get("company") or ""
        company = by_name.get(company_name.lower())
        job["low_confidence"] = trust.score_posting(job, company)
        if not seed_mode:
            _push_live(job)
            _pending_alerts.append(job)
        seen[job["id"]] = job
```

- [ ] **Step 3: Manual verification** (no existing test coverage for `main.py`'s endpoints or `scraper.py`'s `_process` — matches the codebase's existing convention of leaving these two integration points untested; verify by hand)

Run: `cd backend && python -m pytest -q` (confirm nothing else broke)
Expected: all existing tests still pass.

Then start the backend locally (`cd backend && uvicorn main:app --reload`) and POST two jobs with the same company/title/location but different `id`/`source` to `/api/ingest` with a valid `INGEST_TOKEN`:

```bash
curl -s -X POST http://localhost:8000/api/ingest \
  -H "X-Ingest-Token: $INGEST_TOKEN" -H "Content-Type: application/json" \
  -d '[{"id":"x1","title":"Software Engineer New Grad","company":"Acme","location":"Remote","url":"https://a.example/1","source":"hiringcafe"},
       {"id":"x2","title":"software engineer new grad","company":"acme","location":"remote","url":"https://a.example/2","source":"handshake"}]'
```

Expected: `{"ingested": 1}` (second job merged, not inserted), and `GET /api/jobs` shows one entry with `"sources": ["hiringcafe", "handshake"]`.

- [ ] **Step 4: Commit**

```bash
git add backend/main.py backend/scraper.py
git commit -m "feat: merge cross-source duplicate postings instead of double-inserting"
```

---

### Task 3: HiringCafe scraper

**Files:**
- Create: `backend/scrapers/board_scraper.py`
- Create: `data/job_boards.json`
- Test: `backend/test_board_scraper.py`

**Interfaces:**
- Produces: `board_scraper._parse_hiringcafe(html: str) -> list[dict]` (pure), `board_scraper.fetch_hiringcafe(url: str) -> list[dict]` (async, network). Later tasks add more functions to this same file.

- [ ] **Step 1: Create `data/job_boards.json`**

```json
[
  {"name": "handshake", "url": "", "requires_login": true, "source": "handshake"},
  {"name": "jobright", "url": "", "requires_login": true, "source": "jobright"},
  {"name": "simplify", "url": "", "requires_login": true, "source": "simplify"},
  {"name": "hiringcafe", "url": "", "requires_login": false, "source": "hiringcafe"}
]
```

Sriram fills in each `url` with the filtered search URL copied from his own browser session before this goes live (README should note this — see Task 8).

- [ ] **Step 2: Write the failing test** (`backend/test_board_scraper.py`, new file)

```python
"""Board scraper tests: pure HTML-parsing functions only — no live network
calls (see backend/scraper.py's test_scraper.py for the same convention).

Run: cd backend && python -m pytest test_board_scraper.py -v
"""
import board_scraper as bs


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
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd backend && python -m pytest test_board_scraper.py -v`
Expected: FAIL — `board_scraper` module doesn't exist yet.

- [ ] **Step 4: Implement `board_scraper.py`**

```python
"""Scrapers for aggregator/board search-result pages Sriram has filtered
himself in each board's own UI (Handshake, Jobright, Simplify, HiringCafe),
plus the public Jobright new-grad minisite and a Google boolean search
across ATS domains.

Kept separate from playwright_scraper.py (which scrapes individual company
career pages) — distinct responsibility, distinct config file
(data/job_boards.json vs data/companies.json), distinct GitHub Actions
schedule (daily, not every 30 min — see the module docstring in
.github/workflows/job_boards_scraper.yml for why).

Every fetch_* function is best-effort: a failure returns [] and is logged,
never raised, matching every other scraper in this codebase — one bad
board should never block the others in the same run.
"""
import hashlib
import os
import random

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_LAUNCH_ARGS = ["--no-sandbox", "--disable-dev-shm-usage", "--disable-http2"]


def _uid(prefix: str, key: str) -> str:
    return f"{prefix}_{hashlib.md5(key.encode()).hexdigest()[:12]}"


# ---- HiringCafe ----
# Selectors confirmed 2026-07-31 via live DOM inspection (agent-browser) —
# HiringCafe uses plain Tailwind utility classes with no semantic hooks, so
# this is "what's stable today", not a documented API. If this starts
# returning 0 jobs, re-inspect the live page before assuming the saved URL
# broke — the card wrapper is `div.relative.bg-white.rounded-xl.border`
# (confirmed 1:1 against visible listing count), title is the one
# `span.font-bold.line-clamp-2` inside it, location is a *plain*
# `span.line-clamp-2` (no font-bold — that's what distinguishes it from
# title), company is the first `span.font-bold` nested inside
# `span.line-clamp-3.font-light`.
def _parse_hiringcafe(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    jobs = []
    for card in soup.select("div.relative.bg-white.rounded-xl.border"):
        link = card.select_one("a[href*='/job/']")
        title_el = card.select_one("span.font-bold.line-clamp-2")
        if not link or not link.get("href") or not title_el:
            continue
        title = title_el.get_text(strip=True)
        if len(title) < 5:
            continue
        location = ""
        for span in card.select("span.line-clamp-2"):
            classes = span.get("class") or []
            if "font-bold" not in classes:
                location = span.get_text(strip=True)
                break
        company_el = card.select_one("span.line-clamp-3.font-light span.font-bold")
        company = company_el.get_text(strip=True) if company_el else "Unknown"
        href = link["href"]
        jobs.append({
            "id": _uid("hiringcafe", href),
            "title": title, "company": company, "location": location,
            "url": href, "source": "hiringcafe", "posted_at": None, "description": "",
        })
    return jobs


async def fetch_hiringcafe(url: str) -> list[dict]:
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(args=_LAUNCH_ARGS)
            page = await browser.new_page(user_agent=_UA)
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            try:
                await page.wait_for_selector(
                    "div.relative.bg-white.rounded-xl.border", timeout=10000
                )
            except Exception:
                pass
            await page.wait_for_timeout(random.randint(1500, 3000))
            html = await page.content()
            await browser.close()
    except Exception as e:
        print(f"[board_scraper] error scraping hiringcafe: {e}")
        return []
    return _parse_hiringcafe(html)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && python -m pytest test_board_scraper.py -v`
Expected: PASS (3 tests)

- [ ] **Step 6: Commit**

```bash
git add backend/scrapers/board_scraper.py backend/test_board_scraper.py data/job_boards.json
git commit -m "feat: add HiringCafe board scraper"
```

---

### Task 4: Jobright new-grad minisite scraper

**Files:**
- Modify: `backend/scrapers/board_scraper.py` (append)
- Modify: `backend/test_board_scraper.py` (append)

**Interfaces:**
- Produces: `board_scraper._parse_newgrad_rows(html: str) -> list[dict]` (pure), `board_scraper.fetch_newgrad_minisite() -> list[dict]` (async, scrolls a virtualized table)

- [ ] **Step 1: Write the failing test** (append to `test_board_scraper.py`)

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest test_board_scraper.py -k newgrad -v`
Expected: FAIL — `_parse_newgrad_rows` not defined.

- [ ] **Step 3: Implement** (append to `board_scraper.py`)

```python
# ---- Jobright new-grad minisite ----
# Public, no login (confirmed 2026-07-31) — this is what newgrad-jobs.com
# itself embeds in an iframe. Columns confirmed via live inspection, in
# order: index, Position Title, Date, Apply link, Work Model, Location,
# Company, Salary, Company Size, Company Industry, Qualifications, H1B
# Sponsored, Is New Grad. CSS-module classes carry a build-hash suffix that
# changes across Jobright deploys, so matched by substring (same technique
# playwright_scraper.py's fetch_levels() already uses for Levels.fyi).
NEWGRAD_MINISITE_URL = "https://jobright.ai/minisites-jobs/newgrad/us/swe?embed=true"


def _parse_newgrad_rows(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    jobs = []
    for row in soup.select("tr[class*='tableRow']"):
        cells = row.find_all("td")
        if len(cells) < 7:
            continue
        title_el = cells[1].select_one("[class*='positionTitle']")
        apply_el = cells[3].select_one("a[class*='airtableApplyLink']")
        if not title_el or not apply_el or not apply_el.get("href"):
            continue
        title = title_el.get_text(strip=True)
        if len(title) < 5:
            continue
        href = apply_el["href"]
        location = cells[5].get_text(strip=True)
        company = cells[6].get_text(strip=True) or "Unknown"
        jobs.append({
            "id": _uid("newgrad", href),
            "title": title, "company": company, "location": location,
            "url": href, "source": "newgrad-jobs", "posted_at": None, "description": "",
        })
    return jobs


async def fetch_newgrad_minisite() -> list[dict]:
    """The results table is virtualized (react-window-style — only visible
    rows exist in the DOM at any moment, confirmed via a `transform:
    translateY(...)` style on the table). A single page.content() read
    would only capture whatever happened to be on-screen, so this scrolls
    and merges by job id across rounds, stopping once 3 consecutive rounds
    add nothing new (or after 30 rounds regardless, as a hard bound against
    an infinite scroll)."""
    all_jobs: dict[str, dict] = {}
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(args=_LAUNCH_ARGS)
            page = await browser.new_page(user_agent=_UA)
            await page.goto(NEWGRAD_MINISITE_URL, wait_until="domcontentloaded", timeout=30000)
            try:
                await page.wait_for_selector("tr[class*='tableRow']", timeout=10000)
            except Exception:
                pass
            stale_rounds = 0
            for _ in range(30):
                html = await page.content()
                before = len(all_jobs)
                for j in _parse_newgrad_rows(html):
                    all_jobs[j["id"]] = j
                stale_rounds = stale_rounds + 1 if len(all_jobs) == before else 0
                if stale_rounds >= 3:
                    break
                await page.mouse.wheel(0, 800)
                await page.wait_for_timeout(random.randint(400, 800))
            await browser.close()
    except Exception as e:
        print(f"[board_scraper] error scraping newgrad minisite: {e}")
    return list(all_jobs.values())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest test_board_scraper.py -k newgrad -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/scrapers/board_scraper.py backend/test_board_scraper.py
git commit -m "feat: add Jobright new-grad minisite scraper"
```

---

### Task 5: Google boolean search across ATS domains

**Files:**
- Modify: `backend/scrapers/board_scraper.py` (append)
- Modify: `backend/test_board_scraper.py` (append)

**Interfaces:**
- Consumes: `config.ROLE_FILTERS["titles"]` (backend/config.py:5-21)
- Produces: `board_scraper._build_google_query(domain: str) -> str` (pure), `board_scraper._parse_google_serp(html: str, domain: str) -> list[dict]` (pure), `board_scraper.fetch_google_boolean(domain: str) -> list[dict]` (async, best-effort — expected to return `[]` most days, see spec's confirmed-CAPTCHA risk note), `board_scraper.ATS_DOMAINS: list[str]`

- [ ] **Step 1: Write the failing tests** (append to `test_board_scraper.py`)

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest test_board_scraper.py -k google -v`
Expected: FAIL — functions not defined.

- [ ] **Step 3: Implement** (append to `board_scraper.py`, add `httpx` and `urlencode` imports at the top alongside the existing ones)

Add to the imports at the top of the file:
```python
from urllib.parse import urlencode

import httpx
```

Then append:

```python
# ---- Google boolean search across ATS domains ----
# Modeled directly on briansjobsearch.com's own approach (confirmed
# 2026-07-31 by driving its UI): one query PER DOMAIN using Google's native
# tbs=qdr:d (past-24h) filter, rather than one big OR-chain across all
# domains — Google truncates long queries and a multi-domain OR chain
# returned unreliable results in manual testing.
#
# CONFIRMED RISK: a single manual request to Google for this exact kind of
# query was redirected straight to google.com/sorry (CAPTCHA wall) on the
# first attempt, no warm-up. Sriram's explicit call: ship this anyway,
# best-effort — expect it to return [] most days from a GitHub Actions IP.
# Never let a blocked/failed query raise past this function.
ATS_DOMAINS = [
    "lever.co", "greenhouse.io", "ashbyhq.com", "app.dover.io", "breezy.hr",
    "careerpuck.com", "jobs.smartrecruiters.com", "apply.workable.com",
    "jobs.jobvite.com", "careers.bullhorn.com", "workwithus.pinpointhq.com",
    "jobs.hrmdirect.com", "applytojob.com", "recruitee.com",
]
_GOOGLE_EXCLUDE_TERMS = ["senior", "staff", "principal", "lead", "manager", "director"]


def _build_google_query(domain: str) -> str:
    from config import ROLE_FILTERS
    titles = " OR ".join(
        f'"{t}"' if " " in t else t for t in ROLE_FILTERS["titles"]
    )
    excludes = " ".join(f"-{w}" for w in _GOOGLE_EXCLUDE_TERMS)
    q = f"({titles}) site:{domain} {excludes}"
    return "https://www.google.com/search?" + urlencode({"q": q, "tbs": "qdr:d"})


def _parse_google_serp(html: str, domain: str) -> list[dict]:
    """Doesn't depend on Google's SERP HTML structure at all (unverifiable
    — blocked before rendering during design) — scans every <a href> and
    keeps only links whose URL contains the target ATS domain. Link text
    becomes the job's title as-is; unlike other sources, _process() never
    re-fetches/replaces title from the job's own page (only description),
    so a noisy SERP snippet can persist as the shown title. Accepted
    trade-off — the existing regex title filter still runs against it."""
    soup = BeautifulSoup(html, "html.parser")
    jobs, seen = [], set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if domain not in href or not href.startswith("http") or href in seen:
            continue
        title = a.get_text(strip=True)
        if len(title) < 5:
            continue
        seen.add(href)
        jobs.append({
            "id": _uid("google", href),
            "title": title, "company": "Unknown", "location": "",
            "url": href, "source": "google-search", "posted_at": None, "description": "",
        })
    return jobs


async def fetch_google_boolean(domain: str) -> list[dict]:
    url = _build_google_query(domain)
    try:
        async with httpx.AsyncClient(headers={"User-Agent": _UA}, follow_redirects=True) as client:
            r = await client.get(url, timeout=15)
            r.raise_for_status()
    except httpx.HTTPError as e:
        print(f"[board_scraper] google search failed for {domain}: {e}")
        return []
    if "google.com/sorry" in str(r.url):
        print(f"[board_scraper] google search blocked (CAPTCHA) for {domain}")
        return []
    return _parse_google_serp(r.text, domain)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest test_board_scraper.py -k google -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/scrapers/board_scraper.py backend/test_board_scraper.py
git commit -m "feat: add Google boolean search scraper across ATS domains"
```

---

### Task 6: Login-gated board stubs + session context helper

**Files:**
- Modify: `backend/scrapers/board_scraper.py` (append)

**Interfaces:**
- Produces: `board_scraper.AUTH_STATE_PATH: str`, `board_scraper.fetch_handshake(url: str) -> list[dict]`, `board_scraper.fetch_jobright(url: str) -> list[dict]`, `board_scraper.fetch_simplify(url: str) -> list[dict]` — all three raise `NotImplementedError` until their own follow-up task fills in real selectors discovered after logging in.

- [ ] **Step 1: Implement** (append to `board_scraper.py` — no test for this task; every function here intentionally raises, nothing to assert against yet)

```python
# ---- Login-gated boards ----
# Handshake, Jobright (personalized search — distinct from the public
# minisite above), and Simplify all require Sriram's own login to see his
# filtered results, and none were reachable during design (no session to
# inspect with). Each is a stub raising NotImplementedError with the exact
# discovery procedure — filled in as its own task once a real session
# exists (see scripts/save_login_session.py):
#   1. Run scripts/save_login_session.py once to produce AUTH_STATE_PATH.
#   2. `agent-browser open <the board's filtered search URL>` — this reuses
#      the saved session via `agent-browser connect` or a fresh session
#      logged in by hand, either works for one-off inspection.
#   3. `agent-browser eval "..."` to find the job-card container, then
#      title/company/location/link selectors within it — same technique
#      used to derive fetch_hiringcafe's selectors (see git history /
#      docs/superpowers/specs/2026-07-31-job-boards-design.md for the
#      worked example).
#   4. Replace the matching stub below with a real _parse_x + fetch_x pair,
#      following fetch_hiringcafe's shape exactly.
#
# Resolved relative to this file (not the process cwd): the GitHub Actions
# workflow runs this module with working-directory: backend, but the
# secret-restore step that writes the session file runs at the repo root
# (actions' default) — a cwd-relative "data/auth_state.json" would point
# at two different files between those two steps. Anchoring on __file__
# keeps both sides pointed at the same repo-root data/auth_state.json
# regardless of which directory the process was launched from.
AUTH_STATE_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "data", "auth_state.json"
)


async def _login_page(p):
    """Playwright page pre-loaded with the saved login session. Raises
    FileNotFoundError with a clear message if no session has been captured
    yet, instead of a confusing Playwright error deep in browser startup."""
    if not os.path.exists(AUTH_STATE_PATH):
        raise FileNotFoundError(
            f"{AUTH_STATE_PATH} not found — run scripts/save_login_session.py first"
        )
    browser = await p.chromium.launch(args=_LAUNCH_ARGS)
    context = await browser.new_context(storage_state=AUTH_STATE_PATH, user_agent=_UA)
    return browser, await context.new_page()


async def fetch_handshake(url: str) -> list[dict]:
    raise NotImplementedError(
        "Handshake selectors not yet determined (login-gated, unreachable "
        "during design). See the module docstring above this section for "
        "the discovery procedure."
    )


async def fetch_jobright(url: str) -> list[dict]:
    raise NotImplementedError(
        "Jobright selectors not yet determined (login-gated, unreachable "
        "during design). See the module docstring above this section for "
        "the discovery procedure."
    )


async def fetch_simplify(url: str) -> list[dict]:
    raise NotImplementedError(
        "Simplify selectors not yet determined (login-gated, unreachable "
        "during design). See the module docstring above this section for "
        "the discovery procedure."
    )
```

- [ ] **Step 2: Run full test suite to confirm nothing else broke**

Run: `cd backend && python -m pytest -q`
Expected: all tests pass (these new functions aren't called anywhere yet).

- [ ] **Step 3: Commit**

```bash
git add backend/scrapers/board_scraper.py
git commit -m "feat: stub login-gated board scrapers pending session capture"
```

---

### Task 7: Local login-session capture script

**Files:**
- Create: `scripts/save_login_session.py`

**Interfaces:**
- Produces: `data/auth_state.json` on disk when run — consumed by `board_scraper._login_page` (Task 6) and by the GitHub Actions workflow (Task 8) via a repo secret.

- [ ] **Step 1: Implement**

```python
"""One-time LOCAL capture of a logged-in browser session for the
login-gated job boards (Handshake, Jobright, Simplify). Never run this in
CI — it needs a human to type a real password into a real browser window.

Usage: cd backend && python ../scripts/save_login_session.py

Opens a real (non-headless) Chromium window to each board's login page in
turn. Log in by hand in that window, then return to the terminal and press
Enter to move to the next board. After the last one, the combined session
(cookies + localStorage for all three) is written to data/auth_state.json.

Re-run this whenever a login board's scrape starts coming back empty
(state.record_health's consecutive_zero_jobs will flag it — see the
SourceHealth dashboard widget) — that means the saved session expired.

Note: Handshake's login page sits behind a Cloudflare bot challenge for
automated requests; running non-headless with a real person present
(as this script does) normally clears it without extra steps.
"""
import asyncio
import os
import sys

from playwright.async_api import async_playwright

BOARDS = [
    ("Handshake", "https://app.joinhandshake.com/login"),
    ("Jobright", "https://jobright.ai/login"),
    ("Simplify", "https://simplify.jobs/auth/login"),
]
OUTPUT = os.path.join(os.path.dirname(__file__), "..", "data", "auth_state.json")


async def main() -> None:
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()
        for name, url in BOARDS:
            await page.goto(url)
            input(f"Log into {name} in the opened browser window, then press Enter here to continue...")
        await context.storage_state(path=OUTPUT)
        await browser.close()
    print(f"Saved session to {OUTPUT}")
    print("Next: upload its contents as the AUTH_STATE GitHub Actions secret.")


if __name__ == "__main__":
    if sys.stdin is None or not sys.stdin.isatty():
        raise SystemExit("This script needs an interactive terminal — run it locally, not in CI.")
    asyncio.run(main())
```

- [ ] **Step 2: Add `data/auth_state.json` to `.gitignore`** (it's a live session token — must never be committed)

Check `.gitignore` for an existing `data/*.json` or similar pattern first; if none covers it, append:

```
data/auth_state.json
```

- [ ] **Step 3: Manual verification**

Run: `cd backend && python ../scripts/save_login_session.py`
Expected: three browser navigations, each pausing for you to log in by hand; `data/auth_state.json` exists after the script finishes and is *not* shown by `git status` (confirms the gitignore entry works).

- [ ] **Step 4: Commit**

```bash
git add scripts/save_login_session.py .gitignore
git commit -m "feat: add local login-session capture script for board scrapers"
```

---

### Task 8: `_run()` entrypoint + daily GitHub Actions workflow

**Files:**
- Modify: `backend/scrapers/board_scraper.py` (append `_run` + `__main__` block)
- Create: `.github/workflows/job_boards_scraper.yml`

**Interfaces:**
- Consumes: every `fetch_*` function from Tasks 3-6, `data/job_boards.json` (Task 3)
- Produces: a `board_jobs.json` file, same shape the existing `playwright_scraper.yml` workflow POSTs to `/api/ingest`

- [ ] **Step 1: Implement `_run()`** (append to `board_scraper.py`)

```python
# ---- GitHub Action entrypoint ----
# Invoked by .github/workflows/job_boards_scraper.yml:
#   python -m scrapers.board_scraper --boards ../data/job_boards.json --output board_jobs.json
# The workflow then POSTs board_jobs.json to /api/ingest, same as
# playwright_scraper.py — but on its own daily schedule, not the existing
# 30-minute one (see the workflow file for why: login-session risk and the
# confirmed Google CAPTCHA block both favor low frequency here).
import argparse
import asyncio
import json


async def _run(boards_path: str, output_path: str) -> None:
    boards = json.loads(open(boards_path).read())
    all_jobs: list[dict] = []

    for board in boards:
        name, url = board.get("name"), board.get("url")
        if not url:
            print(f"[board_scraper] skipping {name}: no url configured in job_boards.json")
            continue
        try:
            if name == "hiringcafe":
                jobs = await fetch_hiringcafe(url)
            elif name == "handshake":
                jobs = await fetch_handshake(url)
            elif name == "jobright":
                jobs = await fetch_jobright(url)
            elif name == "simplify":
                jobs = await fetch_simplify(url)
            else:
                print(f"[board_scraper] unknown board {name}, skipping")
                continue
        except NotImplementedError as e:
            print(f"[board_scraper] {name} not implemented yet: {e}")
            continue
        all_jobs.extend(jobs)
        await asyncio.sleep(random.uniform(1.5, 3.0))

    all_jobs.extend(await fetch_newgrad_minisite())

    for domain in ATS_DOMAINS:
        all_jobs.extend(await fetch_google_boolean(domain))
        await asyncio.sleep(random.uniform(1.5, 3.0))

    with open(output_path, "w") as f:
        json.dump(all_jobs, f, ensure_ascii=False, indent=2)
    print(f"[board_scraper] wrote {len(all_jobs)} job(s) to {output_path}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Daily job-board aggregator scraper")
    ap.add_argument("--boards", required=True, help="path to data/job_boards.json")
    ap.add_argument("--output", default="board_jobs.json", help="where to write scraped jobs")
    args = ap.parse_args()
    asyncio.run(_run(args.boards, args.output))
```

- [ ] **Step 2: Create the workflow**

```yaml
# Daily, not every-30-min like playwright_scraper.yml: the login-gated
# boards use a saved session that shouldn't be hammered, and the Google
# boolean search is confirmed CAPTCHA-prone on first request (see
# docs/superpowers/specs/2026-07-31-job-boards-design.md) — low frequency
# keeps both risks down. Requires INGEST_URL / INGEST_TOKEN (same secrets
# as playwright_scraper.yml) plus AUTH_STATE (contents of a locally-run
# scripts/save_login_session.py's data/auth_state.json) once Handshake/
# Jobright/Simplify are implemented — see Task 6 in the implementation plan.
name: job-boards-scraper

on:
  workflow_dispatch:
  schedule:
    - cron: "0 13 * * *" # once daily, 6am PT / 1pm UTC

permissions:
  contents: read

jobs:
  scrape:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          persist-credentials: false
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: Install deps + Chromium
        run: |
          pip install -r backend/requirements.txt
          playwright install chromium --with-deps
      - name: Restore login session
        if: ${{ secrets.AUTH_STATE != '' }}
        run: echo '${{ secrets.AUTH_STATE }}' > data/auth_state.json
      - name: Scrape
        working-directory: backend
        run: python -m scrapers.board_scraper --boards ../data/job_boards.json --output board_jobs.json
      - name: POST to /api/ingest
        env:
          INGEST_URL: ${{ secrets.INGEST_URL }}
          INGEST_TOKEN: ${{ secrets.INGEST_TOKEN }}
        run: |
          curl -sf -X POST "$INGEST_URL/api/ingest" \
            -H "X-Ingest-Token: $INGEST_TOKEN" \
            -H "Content-Type: application/json" \
            --data-binary @backend/board_jobs.json
```

- [ ] **Step 3: Manual verification**

Run: `cd backend && python -m pytest -q` (confirm the module still imports cleanly and nothing broke)
Expected: all tests pass.

Run: `cd backend && python -m scrapers.board_scraper --boards ../data/job_boards.json --output /tmp/board_jobs.json` locally (with `data/job_boards.json`'s `hiringcafe` URL filled in — the other three will log "not implemented yet" and skip, which is expected until Handshake/Jobright/Simplify are filled in via their own future task).
Expected: `board_jobs.json` written with HiringCafe + newgrad-minisite + (likely empty, per confirmed CAPTCHA risk) Google results; process exits 0.

- [ ] **Step 4: Commit**

```bash
git add backend/scrapers/board_scraper.py .github/workflows/job_boards_scraper.yml
git commit -m "feat: add daily job-board scraper workflow"
```

---

### Task 9: Expose down-source names on `/api/health`

**Files:**
- Modify: `backend/main.py:82-92`

**Interfaces:**
- Produces: `GET /api/health` response body gains a `"down": ["<source>", ...]` key (list of source names currently at/above `HEALTH_FAILURE_THRESHOLD`) — previously this was computed into a `down` dict but only used for the status code, never returned.

- [ ] **Step 1: Modify the endpoint**

```python
@app.get("/api/health")
async def health():
    sources = state.load_health()
    down = {
        name: s for name, s in sources.items()
        if s.get("consecutive_failures", 0) >= HEALTH_FAILURE_THRESHOLD
    }
    body = {"status": "degraded" if down else "ok", "sources": sources, "down": list(down.keys())}
    if down:
        return JSONResponse(body, status_code=503)
    return body
```

- [ ] **Step 2: Manual verification** (no existing test file for `main.py` — matches this file's existing zero-coverage convention)

Run: `cd backend && uvicorn main:app --reload`, then in another terminal:
```bash
curl -s http://localhost:8000/api/health | python -m json.tool
```
Expected: response includes a `"down": []` key (empty on a healthy system) alongside the existing `status`/`sources` keys.

- [ ] **Step 3: Commit**

```bash
git add backend/main.py
git commit -m "feat: expose down source names on /api/health"
```

---

### Task 10: `SourceHealth` dashboard widget

**Files:**
- Create: `frontend/src/components/SourceHealth.tsx`
- Modify: `frontend/src/App.tsx:1-7` (imports), `frontend/src/App.tsx:66-70` (header row)

**Interfaces:**
- Consumes: `GET /api/health` → `{"status": "ok" | "degraded", "sources": {...}, "down": string[]}` (Task 9)
- Produces: `<SourceHealth />` — a no-props component, polls on a timer, renders nothing when `down` is empty.

- [ ] **Step 1: Implement the component**

```tsx
import { useEffect, useState } from "react";

// Polls the same /api/health the backend already tracks (consecutive
// scrape failures per source) — surfaces it somewhere Sriram will
// actually see it, since nothing did before this. Renders nothing when
// everything's healthy, so it never adds visual noise on a normal day.
const POLL_MS = 5 * 60 * 1000; // health changes slowly; no need to hammer it

export default function SourceHealth() {
  const [down, setDown] = useState<string[]>([]);

  useEffect(() => {
    const check = () =>
      fetch("/api/health")
        .then((r) => r.json())
        .then((body) => setDown(body.down ?? []))
        .catch(() => {}); // best-effort — a failed health check shouldn't itself alarm the UI
    check();
    const id = setInterval(check, POLL_MS);
    return () => clearInterval(id);
  }, []);

  if (down.length === 0) return null;

  return (
    <div className="text-sm text-amber-700 bg-amber-50 border border-amber-200 rounded px-3 py-1.5">
      ⚠ {down.length} source{down.length > 1 ? "s" : ""} down: {down.join(", ")}
    </div>
  );
}
```

- [ ] **Step 2: Wire into `App.tsx`**

Add the import near the top (frontend/src/App.tsx:6, alongside the other component imports):

```tsx
import SourceHealth from "./components/SourceHealth";
```

Add it next to `LiveBadge` in the header row (frontend/src/App.tsx:66-70):

```tsx
      <div className="flex items-center justify-between mb-4">
        <h1 className="text-2xl font-bold">JobRadar</h1>
        <div className="flex items-center gap-3">
          <SourceHealth />
          <LiveBadge count={liveJobs.length} />
        </div>
      </div>
```

- [ ] **Step 3: Manual verification**

Run: `cd frontend && npm run dev`, open the dashboard in a browser.
Expected: nothing extra shown when all sources are healthy (empty `data/source_health.json` or all under threshold). To confirm the widget itself renders correctly, temporarily edit `data/source_health.json` to set some source's `consecutive_failures` to `3` or higher, restart the backend, reload the dashboard — the amber "N sources down" pill should appear next to the live badge. Revert the manual edit afterward.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/SourceHealth.tsx frontend/src/App.tsx
git commit -m "feat: add source health widget to dashboard"
```

---

## Follow-up work (explicitly out of scope for this plan)

- Filling in `fetch_handshake`/`fetch_jobright`/`fetch_simplify`'s real selectors — blocked on Sriram running `scripts/save_login_session.py` and providing each board's filtered search URL, then following the discovery procedure documented in Task 6's module docstring.
- Filling in the `url` fields in `data/job_boards.json` (Handshake, Jobright, Simplify, HiringCafe) — Sriram copies these from his own browser after applying filters in each board's UI.
