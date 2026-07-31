# Job board aggregation design

## Problem

Sriram applies from ~10 bookmarked job-board tabs (job boards + a Google
boolean search) with per-board filters already dialed in. JobRadar should
scrape each filtered view and funnel results through the existing
matching/AI-gate/alert pipeline, so applying happens from one place instead
of ten.

## Scope

Boards: Handshake, Jobright (personalized, login), Simplify, HiringCafe,
plus a Google boolean search across ATS domains (lever.co, greenhouse.io,
ashbyhq.com, app.dover.io, breezy.hr, careerpuck.com,
jobs.smartrecruiters.com, apply.workable.com, jobs.jobvite.com,
careers.bullhorn.com, workwithus.pinpointhq.com, jobs.hrmdirect.com,
applytojob.com, recruitee.com).

LinkedIn was considered and dropped — scraping while logged in ties
automated activity to Sriram's personal LinkedIn account, and repeated runs
risk a temporary restriction/ban. Not worth it relative to the other boards.

briansjobsearch.com and newgrad-jobs.com were folded into other sources
after live inspection (see below) rather than built as standalone scrapers:

- **briansjobsearch.com** turned out to not be a job board at all — it's a
  UI that builds one Google search URL per ATS domain (confirmed via
  `agent-browser`: clicking its "Start" button produces links like
  `google.com/search?q="Software Engineer" site:greenhouse.io remote
  usa&tbs=qdr:h1`, one per platform, using Google's native `tbs=qdr:*`
  time-filter param). This *is* the Google boolean search board — its
  per-domain-query pattern is adopted directly instead of hand-rolling a
  batched OR-chain query.
- **newgrad-jobs.com** turned out to just embed two iframes — a public,
  login-free Jobright "new-grad SWE" minisite
  (`jobright.ai/minisites-jobs/newgrad/us/swe`) and an Airtable. Scraping
  newgrad-jobs.com means scraping that Jobright minisite directly. This is
  unrelated to Sriram's personalized/filtered Jobright.com search, which
  still needs login.

## 1. Boolean search

Modeled on briansjobsearch.com's own approach (verified working): one
query **per ATS domain** (14 total) rather than one big OR-chain across all
domains — Google truncates long queries and a multi-domain OR chain returns
unreliable results, whereas one domain + the full title OR-list stays well
under the practical length limit. Each query:

```
https://www.google.com/search?q=("software engineer" OR "backend engineer" OR "full stack engineer"
  OR swe OR "software developer" OR "frontend engineer" OR "new grad" OR "university grad" OR ...)
  site:lever.co -senior -staff -principal -lead -manager&tbs=qdr:d
```

repeated for each of the 14 domains (`tbs=qdr:d` = Google's native past-24h
filter, same param briansjobsearch.com uses). Title/exclude terms are
pulled from `backend/config.py`'s existing `ROLE_FILTERS["titles"]` /
`["exclude"]` — no separate list to maintain. The `-senior -staff ...`
terms are excludes (strip senior-level postings out of results), not
includes — new-grad terms (`new grad`, `swe`, etc.) are what's being
searched *for*.

Fetched via plain `httpx` GET against Google's SERP (static HTML, no
Playwright/browser needed) — cheaper than a full browser render.

**Risk (confirmed, not hypothetical):** a live test during design — one
manual `agent-browser` request to Google for exactly this kind of query —
was redirected straight to `google.com/sorry/...` (Google's CAPTCHA wall)
on the *first* attempt, no warm-up. This is expected to fail most/all of
the time from a GitHub Actions IP. Sriram's call: build it anyway as
best-effort, same non-blocking pattern as `_fetch_job_description` (a
failed/blocked fetch returns no jobs for that query and never breaks the
run) — accepted that this board may simply not produce results on most
days rather than being a reliable feed. Not worth a paid SERP API for
this personal-use tool.

**Parsing approach:** since Google's SERP markup churns and a wrapper
class (`div.g` etc.) wasn't verifiable (blocked before rendering), the
parser doesn't depend on Google's HTML structure at all — it scans every
`<a href>` in whatever HTML comes back and keeps only links whose host
matches one of the 14 known ATS domains being queried. Link text becomes
the job's title. `_process()` already fetches the job's own page for
`description` when a job passes the initial regex gate (existing AI-gate
step), but it does *not* re-fetch/replace `title` — so a Google-sourced
job's title stays whatever text Google's link snippet had, which can be
noisy (truncated, prefixed with the company name, etc.). Accepted
trade-off for this source specifically: the regex title filter still runs
against it, so junk titles are as likely to be dropped as matched, same
risk as any other source's title text.

## 2. Board scrapers

New `data/job_boards.json`, one entry per board:

```json
[
  {"name": "handshake", "url": "<filtered search URL>", "requires_login": true, "source": "handshake"},
  {"name": "jobright", "url": "...", "requires_login": true, "source": "jobright"},
  {"name": "simplify", "url": "...", "requires_login": true, "source": "simplify"},
  {"name": "hiringcafe", "url": "<filtered search URL>", "requires_login": false, "source": "hiringcafe"}
]
```

`url` is the search-results page with Sriram's board-native filters already
applied (copied from the browser after configuring filters in the board's
own UI) — JobRadar does not reimplement each board's filter UI. The
Jobright new-grad minisite (`newgrad-jobs`) has a fixed public URL, not a
per-user filtered one, so it's hardcoded rather than stored in this config.

New file `backend/scrapers/board_scraper.py` (kept separate from
`playwright_scraper.py`, which already handles company-career-page
scraping — a distinct responsibility) with one function per board:

- `fetch_hiringcafe(url)` — confirmed via live DOM inspection
  (`agent-browser`) on `hiringcafe.com`. Cards: `div.relative.bg-white
  .rounded-xl.border` (one per listing, confirmed 19 matches against 19
  visible listings). Within each card: title = `span.font-bold
  .line-clamp-2`; company = the first `span.font-bold` nested inside
  `span.line-clamp-3.font-light`; location = the `span.line-clamp-2`
  *without* `font-bold` (title has both classes, location has only
  `line-clamp-2` — distinguish on that); link = `a[href*="/job/"]` inside
  the card (confirmed real hrefs like
  `hiringcafe.com/job/software-engineer-ii-ai-ml-bank-of-america-...`).
  `ponytail: HiringCafe uses plain Tailwind utility classes with no
  semantic hooks — this selector combination is what's stable today, not
  a documented API. If it silently returns 0 jobs, the site's markup
  changed; re-inspect with agent-browser before assuming the account/URL
  broke.`
- `fetch_newgrad_minisite()` — hits `jobright.ai/minisites-jobs/newgrad
  /us/swe?embed=true` directly (confirmed public, no login). Rows:
  `tr[class*="tableRow"]` inside the results table (CSS-module classes
  with a build-hash suffix, substring-matched the same way
  `fetch_levels()` already handles Levels.fyi's build-hashed classes).
  Confirmed columns in order: index, Position Title
  (`.index_positionTitle__`), Date, Apply link
  (`.index_airtableApplyLink__`), Work Model, Location, Company, Salary,
  Company Size, Company Industry, Qualifications, H1B Sponsored, Is New
  Grad. Company/location/salary/H1B columns use a shared
  `.index_cellText__` class — read by column position, not class alone.
  **The table is virtualized** (only visible rows are in the DOM,
  confirmed by a `transform: translateY(...)` style on the table) — the
  scraper must scroll the container and re-read rows in a loop until no
  new row keys appear, not just read the DOM once.
- `fetch_handshake(url)`, `fetch_jobright(url)`, `fetch_simplify(url)` —
  selectors unknown (login-gated, not reachable during design). Each is a
  stub with a `NotImplementedError` and a docstring describing the
  discovery procedure, filled in as its own implementation-plan task: log
  in once locally, open the filtered URL, use `agent-browser eval` the
  same way `fetch_hiringcafe`'s selectors were derived here, then replace
  the stub.

Login-gated boards (Handshake/Jobright/Simplify) open their browser
context with `storage_state="data/auth_state.json"` instead of a fresh
unauthenticated context.

## 3. Session refresh

`storage_state.json` (cookies + localStorage from a real login) is captured
once via a new local-only script, `scripts/save_login_session.py`: opens a
non-headless browser, Sriram logs into Handshake/Jobright/Simplify by hand,
script dumps `storage_state.json` on exit. Stored as a GitHub Actions secret
for the scheduled run (never committed to git — it's a live session token).

No auto-detection of expiry — when a login board's session dies, its scrape
returns 0 jobs, which surfaces the same way any other source's failure does
today: `state.record_health` marks `consecutive_zero_jobs`, and
`should_skip_source` eventually skips it. Sriram re-runs the capture script
and re-uploads the secret when he notices via the dashboard health widget
(section 5).

## 4. Pipeline integration

All boards feed the same terminal path as every existing source: an
`_run()` entrypoint POSTs a JSON job list to `/api/ingest`, same as
`playwright_scraper.py` does today. `board_scraper.py` gets its own
`_run()` (same shape) and its own GitHub Actions workflow, on a daily
cron (not the existing 30-minute one) — see the boolean-search and
login-session risk notes above for why. No new filter/AI-gate/alert logic
on the backend — boards are just more rows of jobs entering the same
`/api/ingest` → `_process()` funnel.

## 5. Cross-board dedup

The same posting often appears on multiple boards (e.g. cross-posted to
Handshake and Simplify) with different URLs, so the existing `id`-hash
lookup in `_process()` won't catch it. A secondary index keyed on
`(company.lower(), title.lower(), location.lower())` is checked first — on
a hit, the new board's name is appended to a `sources: [...]` list on the
existing entry instead of inserting a duplicate row.

## 6. Health surfacing

No backend/notifier changes. `/api/health` already exists and already
returns per-source `consecutive_failures`/`consecutive_zero_jobs` — a new
frontend widget, `frontend/src/components/SourceHealth.tsx`, polls it and
renders a small sidebar panel listing any source at or above
`HEALTH_FAILURE_THRESHOLD` (currently 3) consecutive failures. No new
backend endpoint, no email change.

## Out of scope

- LinkedIn (dropped, see above).
- Auto-refreshing an expired login session (manual re-capture only).
- Per-board filter UI inside JobRadar (filters are configured on the
  board's own site; JobRadar just scrapes the resulting URL).
