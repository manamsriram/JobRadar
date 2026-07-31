# Job board aggregation design

## Problem

Sriram applies from ~10 bookmarked job-board tabs (job boards + a Google
boolean search) with per-board filters already dialed in. JobRadar should
scrape each filtered view and funnel results through the existing
matching/AI-gate/alert pipeline, so applying happens from one place instead
of ten.

## Scope

Boards: Handshake, Jobright, Simplify, HiringCafe, NewGrad-jobs,
briansjobsearch, plus a hand-tuned Google boolean search across ATS domains
(lever.co, greenhouse.io, ashbyhq.com, app.dover.io, breezy.hr,
careerpuck.com, jobs.smartrecruiters.com, apply.workable.com,
jobs.jobvite.com, careers.bullhorn.com, workwithus.pinpointhq.com,
jobs.hrmdirect.com, applytojob.com, recruitee.com).

LinkedIn was considered and dropped — scraping while logged in ties
automated activity to Sriram's personal LinkedIn account, and repeated runs
risk a temporary restriction/ban. Not worth it relative to the other boards.

## 1. Boolean search

Google truncates long queries and a single 14-domain OR chain returns
unreliable results, so the site-list is batched into 3-4 queries per run
(4-5 `site:` domains each), each combined with the existing title/exclude
terms already used for the regular pipeline:

```
(site:lever.co OR site:greenhouse.io OR site:ashbyhq.com OR site:app.dover.io OR site:breezy.hr)
("software engineer" OR "backend engineer" OR "full stack engineer" OR swe OR "software developer"
 OR "frontend engineer" OR "new grad" OR "university grad" OR ...)
-senior -staff -principal -lead -manager
```

Title/exclude terms are pulled from `backend/config.py`'s existing
`ROLE_FILTERS["titles"]` / `["exclude"]` — no separate list to maintain.
The `-senior -staff ...` terms are excludes (strip senior-level postings out
of results), not includes — new-grad terms (`new grad`, `swe`, etc.) are
what's being searched *for*.

Fetched via plain `httpx` GET against Google's SERP (static HTML, no
Playwright/browser needed) — cheaper than a full browser render.

**Risk:** scraping Google SERPs programmatically is more aggressively
bot-detected than scraping career pages directly; expect occasional CAPTCHA
walls. Handled the same way as `_fetch_job_description`'s existing
best-effort pattern — a failed fetch returns empty and never blocks the
pipeline. Kept to once/day, ~3-4 queries, realistic UA + jitter, to keep
risk low (not zero).

## 2. Board scrapers

New `data/job_boards.json`, one entry per board:

```json
[
  {"name": "handshake", "url": "<filtered search URL>", "requires_login": true, "source": "handshake"},
  {"name": "jobright", "url": "...", "requires_login": true, "source": "jobright"},
  {"name": "simplify", "url": "...", "requires_login": true, "source": "simplify"},
  {"name": "hiringcafe", "url": "...", "requires_login": false, "source": "hiringcafe"},
  {"name": "newgrad-jobs", "url": "...", "requires_login": false, "source": "newgrad-jobs"},
  {"name": "briansjobsearch", "url": "...", "requires_login": false, "source": "briansjobsearch"}
]
```

`url` is the search-results page with Sriram's board-native filters already
applied (copied from the browser after configuring filters in the board's
own UI) — JobRadar does not reimplement each board's filter UI.

One new function per board in `backend/scrapers/playwright_scraper.py`
(markup differs per site, so each gets its own CSS-selector set, same
pattern as the existing `fetch_levels()`). Login-gated boards
(Handshake/Jobright/Simplify) open their browser context with
`storage_state="data/auth_state.json"` instead of a fresh unauthenticated
context.

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

All boards feed the same path as every existing source: `_run()` in
`playwright_scraper.py` → POST `/api/ingest` → `_process()` in
`scraper.py`. No new filter/AI-gate/alert logic — boards are just more rows
of jobs entering the same funnel.

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
