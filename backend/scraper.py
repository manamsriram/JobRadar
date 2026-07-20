"""Orchestrator: drives every source, filters, enriches, alerts, persists.

poll_loop runs continuously (every POLL_INTERVAL_SECONDS): niche boards (YC /
TLDR), plain (non-JS) custom company career pages, and any careers pages
discovered from the funding queue. funding_loop runs hourly, watching
TechCrunch for newly funded startups and discovering their careers pages.

Phase 2 split: this process never launches a browser. JS-rendered sources
(Levels.fyi, `requires_js` company pages) are scraped by a separate GitHub
Action running scrapers/playwright_scraper.py, which POSTs results to
/api/ingest — see .github/workflows/playwright_scraper.yml.

Matched jobs are pushed to the live SSE feed, emailed, and persisted to
/data. Contact enrichment is on-demand only (POST /api/jobs/{id}/contacts in
main.py), never fired from this loop. Every source failure is caught and
logged so one bad page never kills a cycle.
"""
import asyncio
import hashlib
import random
from datetime import datetime, timezone
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

import adaptive
import ai_match
import aliases
import state
import trust
from config import (
    ALERT_DIGEST_SIZE,
    ALERT_INTERVAL_SECONDS,
    CYCLE_RETRY_BUDGET,
    FUNDING_CHECK_INTERVAL,
    MAX_CONSECUTIVE_ZERO_JOBS,
    POLL_INTERVAL_SECONDS,
    PURGE_AFTER_DAYS,
    VISA_SPONSOR_CHECK_INTERVAL,
)
from fetch import RetryBudget, fetch_with_retry
from filter import matches
from notifier import send_digest_alert
from scrapers.yc_scraper import fetch_yc, fetch_yc_description
from signals import visa_sponsors
from signals.careers_discovery import discover_careers_url
from signals.company_discovery import discover_new_companies
from signals.funding_watcher import check_funding, resolve_domain_from_article
from text_utils import slug_to_title
from url_norm import normalize_url

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Live feed for the SSE endpoint. Bounded + drop-oldest so an idle (no client)
# server can't leak memory. History is served separately via GET /api/jobs.
# ponytail: single-consumer SSE; use pub/sub fan-out if multi-client needed.
new_jobs_queue: asyncio.Queue = asyncio.Queue(maxsize=100)

# Matched jobs awaiting the next digest email. digest_loop drains this.
# ponytail: single-process in-memory list; fine since scraper runs as one process.
_pending_alerts: list[dict] = []


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _push_live(job: dict) -> None:
    try:
        new_jobs_queue.put_nowait(job)
    except asyncio.QueueFull:
        try:
            new_jobs_queue.get_nowait()  # drop oldest
            new_jobs_queue.put_nowait(job)
        except asyncio.QueueEmpty:
            pass


def _extract_jobs(anchors, company: str, base_url: str) -> list[dict]:
    jobs, seen = [], set()
    for a in anchors:
        href = a.get("href", "")
        if not href:
            continue
        # Canonicalize first so two hrefs differing only by tracking params
        # (utm_source vs fbclid) collapse to one row — otherwise the second
        # one would emit a duplicate with a fresh uid.
        full = normalize_url(urljoin(base_url, href))
        if not full or full in seen:
            continue
        title = _real_title(a, href)
        if not title or len(title) < 5:
            continue
        seen.add(full)
        uid = hashlib.md5(full.encode()).hexdigest()[:12]
        jobs.append({
            "id": f"custom_{company.lower()}_{uid}",
            "title": title, "company": company, "location": _nearby_location(a),
            "url": full, "source": "custom", "posted_at": None, "description": "",
        })
    return jobs


async def _fetch_plain(company: str, url: str, retries: int = 2) -> tuple[list[dict], bool]:
    """Scrape a server-rendered career page with httpx + BS4 (no browser).
    Returns (jobs, ok) — ok is False only on a fetch-level failure, never on
    zero jobs found (see state.record_health)."""
    await asyncio.sleep(random.uniform(1.5, 3.0))
    try:
        async with httpx.AsyncClient(headers={"User-Agent": _UA}) as client:
            r = await fetch_with_retry(client, url, retries=retries)
    except httpx.HTTPError as e:
        print(f"[scraper] error fetching {company} ({url}): {e}")
        return [], False
    soup = BeautifulSoup(r.text, "html.parser")
    # Most plain career pages link roles via /jobs//careers//position anchors.
    anchors = soup.select("a[href*='/job'], a[href*='/career'], a[href*='/position']")
    jobs = _extract_jobs(anchors, company, url)

    if not jobs:
        # Broken selector (markup drift outside the fixed keyword list)?
        # Retry against this company's last-known-good link pattern instead
        # of giving up outright — see adaptive.py.
        prefix = state.load_link_patterns().get(company)
        if prefix:
            all_anchors = soup.find_all("a", href=True)
            wanted = set(adaptive.fallback_hrefs([a["href"] for a in all_anchors], prefix))
            fallback_anchors = [a for a in all_anchors if a["href"] in wanted]
            jobs = _extract_jobs(fallback_anchors, company, url)
            if jobs:
                print(f"[scraper] adaptive fallback recovered {len(jobs)} job(s) "
                      f"for {company} via prefix {prefix}")

    if jobs:
        learned = adaptive.learn_prefix([j["url"] for j in jobs])
        if learned:
            patterns = state.load_link_patterns()
            if patterns.get(company) != learned:
                patterns[company] = learned
                state.save_link_patterns(patterns)

    return jobs, True


# Career-page templates often put a generic CTA in the anchor itself (e.g.
# Plaid: <a>See role</a>) rather than the role name — sibling text near the
# anchor is unreliable too (frequently a location list, not a title). The
# href slug is the one thing consistently present and accurate across
# templates: ".../content-lead/" -> "Content Lead".
_CTA_PHRASES = {
    "see role", "view role", "apply", "apply now", "learn more",
    "view position", "see job", "view job", "read more", "see details",
    "view details", "see more",
}


def _real_title(anchor, href: str) -> str:
    raw = anchor.get_text(strip=True)
    if raw and raw.lower() not in _CTA_PHRASES and len(raw) >= 5:
        return raw

    slug = href.rstrip("/").split("/")[-1]
    return slug_to_title(slug) if slug else raw


def _nearby_location(anchor) -> str:
    """Best-effort location text near a career-page anchor (a sibling/descendant
    element whose class/id names it as a location). Empty if none found —
    filter.py treats blank location as trusting the curated company's US listing."""
    for el in anchor.parent.find_all(True, limit=8):
        ident = (el.get("class") and " ".join(el.get("class"))) or el.get("id") or ""
        if "location" in ident.lower():
            text = el.get_text(strip=True)
            if text:
                return text
    return ""


async def _scrape_company(company: dict, budget: RetryBudget | None = None) -> tuple[list[dict], bool | None]:
    """Returns (jobs, ok). ok is None for skipped sources (no url, or
    requires_js — those aren't attempts, so they shouldn't count as health
    failures)."""
    name = company.get("name", "Unknown")
    url = company.get("careers_url")
    if not url:
        return [], None
    if company.get("requires_js"):
        # Phase 2: JS-rendered pages are scraped off-box by the GitHub Action
        # (scrapers/playwright_scraper.py) and arrive via /api/ingest instead.
        return [], None
    retries = budget.take(2) if budget else 2
    return await _fetch_plain(name, url, retries=retries)


def _drop_promoted_from_queue(queue: list[dict], companies: list[dict]) -> list[dict]:
    """Entries already promoted to companies.json (poll_loop's
    _promote_funding_company) are self-identifying by domain — no cross-loop
    flag needed. Keeps funding_queue.json single-writer (funding_loop only)."""
    promoted_domains = {c.get("domain", "").lower() for c in companies}
    return [e for e in queue if (e.get("domain") or "").lower() not in promoted_domains]


async def _gather_sources(
    companies: list[dict], budget: RetryBudget, health: dict
) -> tuple[list[dict], dict[str, tuple[bool, int]]]:
    """Run every source; return (jobs, health_updates).
    `health_updates` maps source_key -> (ok, job_count) for the caller to
    apply via `record_health`. Sources with `consecutive_zero_jobs` at or
    above `MAX_CONSECUTIVE_ZERO_JOBS` are skipped entirely to avoid wasting
    the retry budget on consistently empty career pages."""
    jobs: list[dict] = []
    health_updates: dict[str, tuple[bool, int]] = {}
    yc_jobs, yc_ok = await fetch_yc(retries=budget.take(2))
    jobs += yc_jobs
    health_updates["yc"] = (yc_ok, len(yc_jobs))
    for company in companies:
        source_key = f"company:{company.get('name', 'Unknown')}"
        if state.should_skip_source(health, source_key, MAX_CONSECUTIVE_ZERO_JOBS):
            entry = health.setdefault(source_key, {})
            skip_streak = entry.get("skip_streak", 0) + 1
            if skip_streak < MAX_CONSECUTIVE_ZERO_JOBS:
                entry["skip_streak"] = skip_streak
                n = entry.get("consecutive_zero_jobs", 0)
                print(f"[scraper] skipping {source_key} ({n} consecutive zero-job cycles)")
                continue
            # Probe: without this, consecutive_zero_jobs freezes at the
            # threshold forever (skipped sources never reach record_health)
            # and the source would stay skipped even after it starts
            # posting again. Re-attempt once every MAX_CONSECUTIVE_ZERO_JOBS
            # skipped cycles instead.
            entry["skip_streak"] = 0
        c_jobs, ok = await _scrape_company(company, budget)
        jobs += c_jobs
        if ok is not None:
            health_updates[source_key] = (ok, len(c_jobs))
    return jobs, health_updates


async def _process(jobs: list[dict], seen: dict, companies: list[dict], seed_mode: bool) -> None:
    by_name = {c.get("name", "").lower(): c for c in companies}
    alias_map = state.load_company_aliases()
    for job in state.get_new_jobs(seen, jobs):
        job["scraped_at"] = _now()
        if not job.get("posted_at"):
            job["posted_at"] = job["scraped_at"]
        job["company"] = aliases.canonicalize_company(job.get("company", ""), alias_map)
        if job.get("source") == "yc" and not job.get("description") and job.get("url"):
            job["description"] = await fetch_yc_description(job["url"])
        job["matched"] = matches(job)

        # AI second-pass gate: regex only catches years-of-experience mentions
        # that fit a fixed pattern, so it still lets some over-experienced
        # roles through. Only worth calling when there's real description text
        # to reason over, and never during seed_mode (would burn the whole
        # daily call budget on the first-ever run's backlog).
        if job["matched"] and not seed_mode and len(job.get("description", "")) > 100:
            verdict = await ai_match.review(job)
            if verdict is not None:
                if verdict["verdict"] == "reject":
                    job["matched"] = False
                else:
                    job["ai_score"] = verdict.get("score")
                    job["ai_resume"] = verdict.get("resume")
                    job["ai_reason"] = verdict.get("reason")

        # Unmatched jobs are dropped immediately rather than persisted and
        # purged later — they're cheaply re-evaluated next cycle if the
        # source still lists them.
        if not job["matched"]:
            continue
        company = by_name.get(job.get("company", "").lower())
        job["low_confidence"] = trust.score_posting(job, company)
        if not seed_mode:
            _push_live(job)
            _pending_alerts.append(job)
        seen[job["id"]] = job


async def poll_loop() -> None:
    while True:
        try:
            seen = state.load_seen()
            seed_mode = not seen  # first ever run: persist silently, don't email
            seen = state.purge_old(seen, days=PURGE_AFTER_DAYS)
            companies = state.load_companies()
            budget = RetryBudget(CYCLE_RETRY_BUDGET)
            health = state.load_health()

            jobs, health_updates = await _gather_sources(companies, budget, health)
            await _process(jobs, seen, companies, seed_mode)

            # Discovered careers pages from funding signals
            funding_results: list[tuple[dict, list[dict]]] = []
            for entry in state.load_funding_queue():
                url = entry.get("careers_url")
                if not url:
                    continue
                found, ok = await _scrape_company(
                    {"name": entry.get("company", "Funded"), "careers_url": url}, budget
                )
                for j in found:
                    j["source"] = "funding"
                if ok is not None:
                    health_updates[f"funding:{entry.get('company', 'Funded')}"] = (ok, len(found))
                await _process(found, seen, companies, seed_mode)
                funding_results.append((entry, found))

            # funding_queue.json stays single-writer (funding_loop only) —
            # _drop_promoted_from_queue runs there against this updated list.
            companies = await discover_new_companies(companies, jobs, funding_results)

            for source, (ok, job_count) in health_updates.items():
                health = state.record_health(health, source, ok, job_count)
            state.save_health(health)

            # Merge in whatever changed concurrently (applied-flags from
            # /api/jobs/{id}/apply, or jobs ingested via /api/ingest) during this
            # cycle's runtime, so this save doesn't clobber them.
            current = state.load_seen()
            for jid, job in current.items():
                if jid not in seen:
                    seen[jid] = job
                elif job.get("applied"):
                    seen[jid]["applied"] = True
            seen = state.purge_old(seen, days=PURGE_AFTER_DAYS)
            state.save_seen(seen)
            print(f"[scraper] poll cycle complete ({len(seen)} known). Sleep {POLL_INTERVAL_SECONDS}s.")
        except Exception as e:
            print(f"[scraper] poll loop cycle failed: {e}")
        await asyncio.sleep(POLL_INTERVAL_SECONDS)


async def digest_loop() -> None:
    while True:
        await asyncio.sleep(ALERT_INTERVAL_SECONDS)
        batch, _pending_alerts[:] = _pending_alerts[-ALERT_DIGEST_SIZE:], []
        try:
            await send_digest_alert(batch)
        except Exception as e:
            print(f"[scraper] digest loop failed: {e}")


async def visa_sponsor_loop() -> None:
    """Re-diffs data/visa_sponsor_seeds.json against companies.json weekly —
    picks up rows a user has added to the seed file since the last check.
    No live discovery for seeded rows (they carry a hardcoded careers_url);
    see signals/visa_sponsors.py for why."""
    while True:
        await asyncio.sleep(VISA_SPONSOR_CHECK_INTERVAL)
        try:
            await visa_sponsors.merge_seed_companies(state.load_companies())
        except Exception as e:
            print(f"[scraper] visa sponsor loop failed: {e}")


async def funding_loop() -> None:
    while True:
        await asyncio.sleep(FUNDING_CHECK_INTERVAL)  # sleep first — no burst on restart
        try:
            await check_funding()
            # Resolve a careers URL for every unresolved entry (new this cycle or
            # left over from a prior failed resolution) so poll_loop can scrape it.
            queue = state.load_funding_queue()
            for entry in queue:
                if entry.get("careers_url"):
                    continue
                # Real domain from the article's outbound link; the headline
                # guess ("<company>.com") is only a fallback. Once resolved,
                # don't re-fetch the article every cycle just because
                # discover_careers_url hasn't found a careers page yet.
                if not entry.get("domain_resolved") and entry.get("article_url"):
                    domain = await resolve_domain_from_article(
                        entry["article_url"], entry.get("company")
                    )
                    if domain:
                        entry["domain"] = domain
                        entry["domain_resolved"] = True
                domain = entry.get("domain")
                if not domain:
                    continue
                entry["careers_url"] = await discover_careers_url(domain)

            queue = _drop_promoted_from_queue(queue, state.load_companies())
            state.save_funding_queue(queue)
        except Exception as e:
            print(f"[scraper] funding loop failed: {e}")
