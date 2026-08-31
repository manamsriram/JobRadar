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
import argparse
import asyncio
import hashlib
import json
import logging
import os
import random
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode, urljoin

from bs4 import BeautifulSoup
import httpx
from playwright.async_api import async_playwright

import search_api
import state

logger = logging.getLogger(__name__)

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
def _parse_hiringcafe(html: str, base_url: str = "https://hiringcafe.com") -> list[dict]:
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
        href = urljoin(base_url, link["href"])
        jobs.append({
            "id": _uid("hiringcafe", href),
            "title": title, "company": company, "location": location,
            "url": href, "source": "hiringcafe", "posted_at": None, "description": "",
        })
    return jobs


async def fetch_hiringcafe(url: str) -> tuple[list[dict], bool]:
    """Returns (jobs, ok) — ok reflects fetch-level success (no exception),
    not job_count, so callers can feed it straight into state.record_health."""
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(args=_LAUNCH_ARGS)
            async with browser:
                page = await browser.new_page(user_agent=_UA)
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                try:
                    await page.wait_for_selector(
                        "div.relative.bg-white.rounded-xl.border", timeout=10000
                    )
                except Exception:
                    logger.warning("hiringcafe: selector wait timed out, parsing whatever loaded")
                await page.wait_for_timeout(random.randint(1500, 3000))
                html = await page.content()
                jobs = _parse_hiringcafe(html, url)
    except Exception as e:
        logger.error("error scraping hiringcafe: %s", e)
        return [], False
    return jobs, True


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


async def fetch_newgrad_minisite() -> tuple[list[dict], bool]:
    """The results table is virtualized (react-window-style — only visible
    rows exist in the DOM at any moment, confirmed via a `transform:
    translateY(...)` style on the table). A single page.content() read
    would only capture whatever happened to be on-screen, so this scrolls
    and merges by job id across rounds, stopping once 3 consecutive rounds
    add nothing new (or after 30 rounds regardless, as a hard bound against
    an infinite scroll). Returns (jobs, ok) — see fetch_hiringcafe."""
    all_jobs: dict[str, dict] = {}
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(args=_LAUNCH_ARGS)
            async with browser:
                page = await browser.new_page(user_agent=_UA)
                await page.goto(NEWGRAD_MINISITE_URL, wait_until="domcontentloaded", timeout=30000)
                try:
                    await page.wait_for_selector("tr[class*='tableRow']", timeout=10000)
                except Exception:
                    logger.warning("newgrad minisite: selector wait timed out, parsing whatever loaded")
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
    except Exception as e:
        logger.error("error scraping newgrad minisite: %s", e)
        return list(all_jobs.values()), False
    return list(all_jobs.values()), True


# ---- Google boolean search across ATS domains ----
# Runs on its own every-3h workflow (.github/workflows/google_search.yml),
# separate from the daily board run — the login-gated boards use a saved
# session that shouldn't be hammered, but this source has no such constraint.
#
# QUERY SHAPE: Google silently truncates queries past 32 words ("Word (and
# any subsequent words) was ignored because we limit queries to 32 words").
# The full filter set is far past that: 34 title alternatives OR'd = 107
# words, plus 14 site: terms and the exclude list. An earlier all-in-one
# OR-chain therefore lost most of its terms and returned unreliable results.
#
# So each run emits a few queries that each fit the cap: one rotating group
# of titles, packed with as many site: domains as the remaining budget
# allows. The title group rotates by UTC hour, so consecutive runs across a
# day cover the whole title list without persisting a cursor anywhere.
# Losing query-side precision is cheap here: _process()'s ROLE_FILTERS regex
# re-filters every job by title anyway, so the query only has to be broad
# enough to surface the postings, not narrow enough to be the final filter.
#
# CONFIRMED RISK: a manual request for this exact kind of query was
# redirected straight to google.com/sorry (CAPTCHA wall) on the first
# attempt, no warm-up. Mitigated (not solved) by running through real
# Chromium rather than httpx, and by backing off for
# GOOGLE_BLOCK_BACKOFF_HOURS after a block instead of re-hammering the wall.
# Still best-effort: expect [] on plenty of runs. Never raises past
# fetch_google_boolean.
ATS_DOMAINS = [
    "lever.co", "greenhouse.io", "ashbyhq.com", "app.dover.io", "breezy.hr",
    "careerpuck.com", "jobs.smartrecruiters.com", "apply.workable.com",
    "jobs.jobvite.com", "careers.bullhorn.com", "workwithus.pinpointhq.com",
    "jobs.hrmdirect.com", "applytojob.com", "recruitee.com",
]
_GOOGLE_EXCLUDE_TERMS = ["senior", "staff", "principal", "lead", "manager", "director"]

# Google's documented cap. Counted conservatively: every whitespace-separated
# token counts, including each OR and each -exclude. Google excludes stop
# words and may not charge for operators at all, so a query that fits this
# counter is safely under the real limit rather than near it.
GOOGLE_MAX_QUERY_TOKENS = int(os.getenv("GOOGLE_MAX_QUERY_TOKENS", "32"))
# ponytail: whitespace tokens, not Google's real tokenizer — deliberately
# over-counts. Tighten only if runs come back provably under-packed.
def _token_count(q: str) -> int:
    return len(q.split())


def _title_groups(titles: list[str], per_group: int = 5) -> list[list[str]]:
    """Partition the title list into fixed-size groups. Every title lands in
    exactly one group, so rotating through groups covers the full list."""
    return [titles[i:i + per_group] for i in range(0, len(titles), per_group)]


def _quote(term: str) -> str:
    term = term.strip()
    return f'"{term}"' if " " in term else term


def _build_google_queries(titles: list[str], domains: list[str]) -> list[str]:
    """One query per domain batch: a fixed title OR-chain plus as many
    site: terms as fit under GOOGLE_MAX_QUERY_TOKENS. Excludes are added
    only while they still fit — they are a nicety, the Python-side
    ROLE_FILTERS exclude list is what actually enforces them."""
    title_clause = "(" + " OR ".join(_quote(t) for t in titles) + ")"
    queries: list[str] = []
    batch: list[str] = []

    def flush() -> None:
        if not batch:
            return
        q = f"{title_clause} (" + " OR ".join(f"site:{d}" for d in batch) + ")"
        for word in _GOOGLE_EXCLUDE_TERMS:
            candidate = f"{q} -{word}"
            if _token_count(candidate) > GOOGLE_MAX_QUERY_TOKENS:
                break
            q = candidate
        queries.append(q)

    for domain in domains:
        trial = batch + [domain]
        probe = f"{title_clause} (" + " OR ".join(f"site:{d}" for d in trial) + ")"
        if batch and _token_count(probe) > GOOGLE_MAX_QUERY_TOKENS:
            flush()
            batch = [domain]
        else:
            batch = trial
    flush()
    return queries


def plan_google_queries(hour: int | None = None) -> list[str]:
    """The queries for this run. The title group rotates by UTC hour so a
    3-hourly schedule walks the whole title list across the day with no
    stored cursor."""
    from config import ROLE_FILTERS
    groups = _title_groups(ROLE_FILTERS["titles"])
    if hour is None:
        hour = datetime.now(timezone.utc).hour
    return _build_google_queries(groups[hour % len(groups)], ATS_DOMAINS)


def _search_url(query: str) -> str:
    # tbs=qdr:d — past 24h only, same filter briansjobsearch.com uses.
    return "https://www.google.com/search?" + urlencode({"q": query, "tbs": "qdr:d"})


# ---- CAPTCHA backoff ----
# Piggybacks on the existing source_health entry rather than adding another
# state file: a block writes `blocked_until`, and the next run skips Google
# entirely until it passes. Without this, a 3-hourly schedule would keep
# knocking on a wall that has already said no.
GOOGLE_BLOCK_BACKOFF_HOURS = int(os.getenv("GOOGLE_BLOCK_BACKOFF_HOURS", "6"))
GOOGLE_HEALTH_KEY = "google-search"


def google_blocked_until(health: dict) -> str | None:
    return (health.get(GOOGLE_HEALTH_KEY) or {}).get("blocked_until")


def is_google_backing_off(health: dict, now: datetime | None = None) -> bool:
    until = google_blocked_until(health)
    if not until:
        return False
    try:
        return (now or datetime.now(timezone.utc)) < datetime.fromisoformat(until)
    except ValueError:
        # Corrupt timestamp shouldn't wedge the source off forever.
        logger.warning("google backoff: unparseable blocked_until %r, ignoring", until)
        return False


def mark_google_blocked(health: dict, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    entry = health.get(GOOGLE_HEALTH_KEY, {})
    entry["blocked_until"] = (now + timedelta(hours=GOOGLE_BLOCK_BACKOFF_HOURS)).isoformat()
    health[GOOGLE_HEALTH_KEY] = entry
    return health


def clear_google_block(health: dict) -> dict:
    entry = health.get(GOOGLE_HEALTH_KEY)
    if entry:
        entry.pop("blocked_until", None)
    return health


def _jobs_from_results(results: list[dict], domains: list[str]) -> list[dict]:
    """Keep only results pointing at one of the target ATS domains. The result
    title is used as-is: unlike other sources, _process() never re-fetches the
    title from the job's own page (only the description), so a noisy SERP
    title can persist as the shown title. Accepted trade-off — the existing
    ROLE_FILTERS regex still re-filters every job by title."""
    jobs, seen = [], set()
    for r in results:
        href = r.get("url") or ""
        title = (r.get("title") or "").strip()
        if href in seen or len(title) < 5:
            continue
        if not any(d in href for d in domains):
            continue
        seen.add(href)
        jobs.append({
            "id": _uid("google", href),
            "title": title, "company": "Unknown", "location": "",
            "url": href, "source": "google-search", "posted_at": None, "description": "",
        })
    return jobs


def _parse_google_serp(html: str, domains: list[str]) -> list[dict]:
    """Kept for the raw-HTML path (the Playwright fallback backend) and its
    tests; the API backends hand back results directly."""
    return _jobs_from_results(search_api.parse_serp_anchors(html), domains)


def _is_captcha(url: str, html: str) -> bool:
    return "google.com/sorry" in url or "/sorry/index" in html


async def fetch_google_boolean(query: str, domains: list[str]) -> tuple[list[dict], bool, bool]:
    """Run one query through search_api (Serper → DuckDuckGo → Chromium).
    Returns (jobs, ok, blocked) — see fetch_hiringcafe for `ok`; `blocked`
    means every backend was challenged, so the caller should back the whole
    source off rather than burning the rest of the run's queries."""
    results, ok, blocked = await search_api.search(query, days=1)
    if blocked:
        logger.warning("google search blocked — backing off %dh", GOOGLE_BLOCK_BACKOFF_HOURS)
        return [], False, True
    if not ok:
        return [], False, False
    try:
        return _jobs_from_results(results, domains), True, False
    except Exception as e:
        logger.error("google SERP parse failed: %s", e)
        return [], False, False


async def run_google_search(health: dict) -> tuple[list[dict], dict]:
    """Whole Google source for one run: skip if backing off, else walk this
    hour's queries, stopping early on a CAPTCHA. Returns (jobs, health)."""
    if is_google_backing_off(health):
        logger.info("google search skipped: backing off until %s", google_blocked_until(health))
        return [], health

    jobs: list[dict] = []
    any_ok = False
    for query in plan_google_queries():
        found, ok, blocked = await fetch_google_boolean(query, ATS_DOMAINS)
        any_ok = any_ok or ok
        jobs.extend(found)
        if blocked:
            health = mark_google_blocked(health)
            break
        await asyncio.sleep(random.uniform(1.5, 3.0))

    if any_ok:
        health = clear_google_block(health)
    # At-least-one-success policy: a single blocked query shouldn't mark the
    # whole source down when the others came back fine.
    return jobs, state.record_health(health, GOOGLE_HEALTH_KEY, any_ok, len(jobs))


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


# ---- GitHub Action entrypoint ----
# Two workflows share this entrypoint, on different schedules, via --sources:
#   job_boards_scraper.yml (daily)   --sources boards --boards ../data/job_boards.json
#   google_search.yml      (every 3h) --sources google
# Both POST their output to /api/ingest, same as playwright_scraper.py. The
# boards stay daily because they ride a saved login session that shouldn't be
# hammered; Google runs more often because fresher ATS postings are the whole
# point of it, and its own CAPTCHA backoff (not the schedule) is what keeps
# the request rate sane.
_BOARD_FETCHERS = {
    "hiringcafe": fetch_hiringcafe,
    "handshake": fetch_handshake,
    "jobright": fetch_jobright,
    "simplify": fetch_simplify,
}


async def _run(boards_path: str, output_path: str, sources: str = "all") -> None:
    all_jobs: list[dict] = []
    health = state.load_health()

    if sources in ("all", "boards"):
        with open(boards_path) as f:
            boards = json.load(f)
        for board in boards:
            name, url = board.get("name"), board.get("url")
            if not url:
                logger.info("skipping %s: no url configured in job_boards.json", name)
                continue
            fetcher = _BOARD_FETCHERS.get(name)
            if fetcher is None:
                logger.warning("unknown board %s, skipping", name)
                continue
            try:
                jobs, ok = await fetcher(url)
            except NotImplementedError as e:
                logger.info("%s not implemented yet: %s", name, e)
                continue
            except Exception as e:
                # One bad board should never block the others in the same run
                # (see module docstring) — any exception a real fetch_* raises
                # (expired session, timeout, ...) is caught here, not just the
                # NotImplementedError stubs above.
                logger.error("%s fetch failed: %s", name, e)
                health = state.record_health(health, name, False)
                continue
            health = state.record_health(health, name, ok, len(jobs))
            all_jobs.extend(jobs)
            await asyncio.sleep(random.uniform(1.5, 3.0))

        newgrad_jobs, newgrad_ok = await fetch_newgrad_minisite()
        health = state.record_health(health, "newgrad-jobs", newgrad_ok, len(newgrad_jobs))
        all_jobs.extend(newgrad_jobs)

    if sources in ("all", "google"):
        google_jobs, health = await run_google_search(health)
        all_jobs.extend(google_jobs)

    state.save_health(health)

    with open(output_path, "w") as f:
        json.dump(all_jobs, f, ensure_ascii=False, indent=2)
    logger.info("wrote %d job(s) to %s", len(all_jobs), output_path)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="[board_scraper] %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="Daily job-board aggregator scraper")
    ap.add_argument("--boards", help="path to data/job_boards.json (required unless --sources google)")
    ap.add_argument("--output", default="board_jobs.json", help="where to write scraped jobs")
    ap.add_argument("--sources", choices=["all", "boards", "google"], default="all",
                    help="which sources to run — the boards and Google search have separate "
                         "workflows and schedules (daily vs every 3h)")
    args = ap.parse_args()
    if args.sources in ("all", "boards") and not args.boards:
        ap.error("--boards is required unless --sources google")
    asyncio.run(_run(args.boards, args.output, args.sources))
