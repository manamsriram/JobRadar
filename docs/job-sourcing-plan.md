# JobRadar: experience filter + career-page parsing + bigger company list

## Context
User complaints + expansion, after senior-dev review of a first draft and study of
two references:
- **speedyapply/2027-SWE-College-Jobs**: downstream renderer reading jobs from Supabase
  (populated by a separate aggregator, JobSpy). Stores minimal fields, classifies
  `job_type`/`is_usa` server-side, tracks `age` in days. No career-page extractor to reuse.
- **newgrad-jobs.com**: defines new-grad as **"0–2 years"** — confirms the ≤2 threshold.

Three goals:
1. **Jobs requiring years of experience still show up.** Descriptions ARE scraped/stored
   (Greenhouse/Lever/Ashby), but the body filter (`config.py:49-52` `desc_exclude`) only
   catches **5+ years**, via plain substring. "2+ years", "minimum of 4 years", "3-5 years"
   pass. No min-years extraction. → regex fix, threshold ≤2.
2. **Parse careers pages, not just ATS boards.** Existing Playwright scraper
   (`playwright_scraper.py`) grabs anchor `title`+`href` only, `description:""`,
   `location:"See listing"` → **bypasses the experience AND location filters** (the exact bug).
   Must fetch each job's detail text so the filter can run.
3. **Only 4 companies.** `companies.json` hand-curated, no discovery. Want volume.

Target: new-grad roles, **≤2 years**.

## Design principle (key insight)
Most company `/careers` pages are **ATS-backed** (embed Greenhouse/Lever/Ashby/SmartRecruiters/
Workday). Structured ATS JSON gives real descriptions → the experience filter works. Blind HTML
scraping gives empty descriptions → filter can't run. So: **prefer routing career pages to a
structured fetcher; only truly custom pages fall to Playwright, and Playwright must fetch job
detail text.**

---

## Change 1 — regex experience filter (the core fix)

**`backend/filter.py`** — add a min-years extractor; replace the `desc_exclude` substring
block (`filter.py:49-52`).

```python
import re

# Untrusted body text, but description is capped at 5000 chars (util.py:6) and this
# pattern is linear (no nested quantifiers) → no ReDoS risk.
_YEARS_RE = re.compile(
    r"(?:minimum of|at least|min\.?\s*of)?\s*"
    r"(\d{1,2})\s*(?:(?:-|to|–)\s*\d{1,2})?\s*\+?\s*(?:years?|yrs?)",
    re.IGNORECASE,
)

def _min_years_required(description: str) -> int | None:
    """Lowest years-of-experience figure in the body, or None.
    Ranges ('0-2 years') and 'N+ years' resolve to the LOW end (0, N).
    ponytail: takes min() across all matches, so a new-grad post that also says
    'preferred: 5 years' keeps its low floor and passes — biases toward keeping
    entry-level, which is the desired failure direction. A senior IC titled cleanly
    ('Software Engineer', body '6+ years') with no lower number is still dropped.
    Ceiling: won't catch spelled-out ('two years'); rare in ATS posts."""
    nums = [int(m) for m in _YEARS_RE.findall(description)]
    return min(nums) if nums else None
```
In `matches()`, replace `filter.py:49-52` with:
```python
    description = job.get("description", "").lower()
    min_years = _min_years_required(description)
    if min_years is not None and min_years > MAX_EXPERIENCE_YEARS:
        return False
```
Regex supersedes `desc_exclude` (5-7→5, 10+→10 both dropped), so **remove `desc_exclude`**
from `config.py`. Keep the title `exclude` list unchanged (cheap first pass).

**`backend/config.py`**: add `MAX_EXPERIENCE_YEARS = int(os.getenv("MAX_EXPERIENCE_YEARS", "2"))`;
delete `desc_exclude` (config.py:49-52).

---

## Change 2 — career-page parsing

Two sub-parts:

**2a. Add SmartRecruiters fetcher** (`backend/scrapers/smartrecruiters.py`) — many career
pages embed it, clean public GET API with descriptions. Mirror the shape of the existing
ATS fetchers (`greenhouse.py`):
- List: `GET https://api.smartrecruiters.com/v1/companies/{slug}/postings` → `content[]`
- Detail (for description): `GET .../postings/{id}` → `jobAd.sections.*.text` (HTML) → run
  through existing `plaintext()` (`scrapers/util.py`). Location from `location.city/country`;
  filter non-US via country. Cap detail fetches per company to bound cost.
- Register in `ATS_FETCHERS` (`scraper.py:25-30`) under key `"smartrecruiters"`; dispatch by
  `slug` (already handled — only `custom` uses `url`, scraper.py:52-55).

**2b. Playwright as a separate parser service** (avoids OOM on the main 512MB service —
Chromium alone is ~300-500MB, and the main box also runs FastAPI + the poll loop). The browser
moves out of the main backend into its own small service; the main backend calls it over HTTP.

*New service `parser-service/`* — minimal FastAPI, one real endpoint:
- `POST /parse` body `{ "url": str, "max_jobs": int }` → runs the (improved) `fetch_custom`
  logic and returns `jobs[]` JSON. `GET /healthz` for uptime.
- **Improved `fetch_custom`** (moved here): after collecting anchor links, reuse the open
  browser to visit up to `max_jobs` detail pages and populate `description` from `body`
  innerText via `plaintext()` — so the experience + location filters actually run (fixes the
  empty-description bypass). Also try to derive a real `location`/`posted_at` from the detail
  page; else `location:"See listing"` (no US signal → filter drops it, acceptable) and
  `posted_at:None`.
  ```python
  for job in jobs[:max_jobs]:
      try:
          await page.goto(job["url"], wait_until="domcontentloaded", timeout=15000)
          await page.wait_for_timeout(800)
          job["description"] = plaintext(await page.inner_text("body"))
      except Exception:
          pass  # leave empty; main-side filter drops title-only
  ```
- **Own Dockerfile** on the Playwright base image (`mcr.microsoft.com/playwright/python`),
  deployed as a second Render service (own 512MB → Chromium isolated from the main backend).

*Main backend* — `backend/scrapers/playwright_scraper.py` `fetch_custom` becomes a thin httpx
client: `POST {PARSER_URL}/parse` with the shared-secret header, timeout ~60s, returns the
parsed jobs. Signature unchanged, so `scrape_company` dispatch (scraper.py:52-53) is untouched.
If `PARSER_URL` is unset, `fetch_custom` returns `[]` (graceful — matches today's "no custom by
default"; the main service never imports Playwright).

**Security — the parser endpoint fetches arbitrary URLs (SSRF surface):**
- Require a shared secret: `X-Parser-Token` header checked against env `PARSER_TOKEN`; reject
  401 otherwise. Both services get the same secret via env.
- Allowlist the host: only parse URLs whose host matches a `custom` entry in `companies.json`
  (bundle the allowed hosts into the parser via env or a shared file). Blocks the token-holder
  from pointing it at internal/metadata IPs.
- Parser service exposes only `/parse` + `/healthz`; no other routes.

`CUSTOM_MAX_JOBS = int(os.getenv("CUSTOM_MAX_JOBS", "15"))`, `PARSER_URL`, `PARSER_TOKEN` in
`config.py` (main) / parser env. ponytail ceilings: bounded per company; Render free tier spins
down after ~15min idle → first custom parse may cold-start slow/time out (best-effort, main loop
swallows the error via scraper.py:56-58). Workday deferred.

---

## Change 3 — bigger company list

**`backend/companies.json`** — expand from 4 to ~50-150 validated entries, same schema
`{name, ats, slug, domain, tier}`, `ats ∈ greenhouse|lever|ashby|smartrecruiters` (plus a
few `custom` career-page URLs to exercise 2b). Keep most at `tier:2` with `domain:""` (Hunter
enrichment guarded to tier-1, scraper.py:88 — tier-2 skips cleanly, verified). Representative:
- greenhouse: `airbnb databricks robinhood dropbox coinbase figma gitlab brex plaid`
- lever: `netflix palantir attentive`
- ashby: `linear notion vercel mistral anthropic perplexityai`
- custom: 2-3 real career-page URLs (for 2b)

**Validation mandatory** — stale slugs fetch nothing (per-company errors are swallowed,
scraper.py:56-58, so failures are silent). Throwaway script hits each board URL, keeps only
slugs returning 200 + non-empty.

---

## Senior-review fixes folded in
- **desc_exclude removal safe** — regex is a superset (covers 5-7, 10+, etc.).
- **ReDoS** — linear pattern + 5000-char cap; noted in code.
- **Playwright bypass** — was the biggest flaw; 2b fixes it (empty desc no longer masquerades as a match).
- **Cost / free tier** — serial poll loop + more companies + Playwright per-job nav; bounded by `CUSTOM_MAX_JOBS`, tier-2 skips Hunter. Consider raising `POLL_INTERVAL_SECONDS` if cycles overrun.
- **Cross-provider dedup** — same company via both ATS and custom → duplicate rows (provider-prefixed ids differ). Rule: never list a company under two `ats` values.
- **SSRF** — career-page URLs come from maintainer-controlled `companies.json`, not user input; no API to add companies. Low risk. Do not expose company-add via HTTP.

## Early + hidden jobs (jobright.ai study)
jobright.ai's "hidden jobs" / "apply earliest" pitch is **not a special data source** — it is
continuous **direct polling of company ATS/career pages**. A "hidden job" = a role that only
ever appears on the company's own ATS and never syndicates to LinkedIn/Indeed. JobRadar already
uses this architecture (direct Greenhouse/Lever/Ashby APIs), so it is already positioned to
catch hidden + early roles. Aggregators (jobright's other input) trade breadth for staleness —
they surface expired/filled/fake posts; direct-ATS is source-of-truth and avoids that. Concrete
levers, folded into this plan:
- **Breadth = hidden coverage** — every board added in Change 3 is a hidden-job source not on
  aggregators. This is the main lever; more boards > cleverness.
- **Freshness ordering — already done.** `state.get_matched` orders `posted_at.desc` (state.py:96),
  so newest surface first. Caveat: custom/Playwright jobs have `posted_at=None` → fall back to
  `_now()` (scraper.py:63-64) and sort as "just posted", crowding real fresh ATS jobs. If custom
  volume grows, parse a real date from the detail page (2b) or sort them below dated jobs.
- **Poll frequency = earliness** — `POLL_INTERVAL_SECONDS` (default 300, config.py:56) is the
  earliest-detection knob. Lower = earlier, but more companies × Playwright detail fetches strain
  the 512MB free tier. Tune after measuring one full cycle's wall-time; a tier-1 fast lane
  (poll top companies more often) is possible but deferred as over-engineering until needed.

## Deferred (out of scope, noted for user)
- **JobSpy aggregator route** (what speedyapply uses upstream): scrapes LinkedIn/Indeed/Google
  for far higher volume than per-company ATS. Bigger pivot, ToS-gray, rate-limited. Revisit if
  per-company coverage proves too thin.
- **H1B sponsorship** — user chose skip.

## Files
- `backend/filter.py` — regex extractor + gate (Change 1)
- `backend/config.py` — add `MAX_EXPERIENCE_YEARS`, `CUSTOM_MAX_JOBS`; remove `desc_exclude`
- `backend/scrapers/smartrecruiters.py` — new fetcher (2a); register in `scraper.py` `ATS_FETCHERS`
- `backend/scrapers/playwright_scraper.py` — detail-page description fetch (2b)
- `backend/companies.json` — expanded validated list (Change 3)
- `backend/test_filter.py` — new, assert-based self-check

## Verification
1. **Unit self-check** (`test_filter.py`, plain asserts, no framework):
   - `_min_years_required("requires 3+ years") == 3`
   - `_min_years_required("0-2 years experience") == 0`
   - `_min_years_required("minimum of 5 years") == 5`
   - `_min_years_required("3 to 5 years") == 3`
   - `_min_years_required("great team, no numbers") is None`
   - `matches({title:"Software Engineer", location:"San Jose", description:"3+ years required"})` → False
   - `matches({title:"Software Engineer", location:"San Jose", description:"0-2 years"})` → True
2. **Slug-validation script** — every new `companies.json` slug returns 200 + jobs; prune failures.
3. **SmartRecruiters** — fetch one known company (e.g. a public SR board), confirm descriptions populate and a 4+-years posting is filtered out.
4. **Playwright 2b** — run `fetch_custom` against one real career page; confirm descriptions come back non-empty and the experience filter drops a senior posting.
5. **End-to-end** — run `poll_loop` once locally; confirm new volume, a known 3+-years job absent, `GET /api/jobs` reflects it.
