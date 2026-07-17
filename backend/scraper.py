"""Orchestrator: drives every source, filters, enriches, alerts, persists.

poll_loop runs continuously (every POLL_INTERVAL_SECONDS): niche boards (YC /
TLDR), plain (non-JS) custom company career pages, and any careers pages
discovered from the funding queue. funding_loop runs hourly, watching
TechCrunch for newly funded startups and discovering their careers pages.

Phase 2 split: this process never launches a browser. JS-rendered sources
(Levels.fyi, `requires_js` company pages) are scraped by a separate GitHub
Action running scrapers/playwright_scraper.py, which POSTs results to
/api/ingest — see .github/workflows/playwright_scraper.yml.

Matched jobs are enriched (tier-1 only), pushed to the live SSE feed, emailed,
and persisted to /data. Every source failure is caught and logged so one bad
page never kills a cycle.
"""
import asyncio
import random
from datetime import datetime, timezone
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

import state
from config import FUNDING_CHECK_INTERVAL, POLL_INTERVAL_SECONDS, PURGE_AFTER_DAYS
from enricher import find_contacts
from filter import matches
from notifier import send_email_alert
from scrapers.yc_scraper import fetch_yc
from signals.careers_discovery import discover_careers_url
from signals.funding_watcher import check_funding, resolve_domain_from_article

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Live feed for the SSE endpoint. Bounded + drop-oldest so an idle (no client)
# server can't leak memory. History is served separately via GET /api/jobs.
# ponytail: single-consumer SSE; use pub/sub fan-out if multi-client needed.
new_jobs_queue: asyncio.Queue = asyncio.Queue(maxsize=100)


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


async def _fetch_plain(company: str, url: str) -> list[dict]:
    """Scrape a server-rendered career page with httpx + BS4 (no browser)."""
    await asyncio.sleep(random.uniform(1.5, 3.0))
    try:
        async with httpx.AsyncClient(headers={"User-Agent": _UA}) as client:
            r = await client.get(url, timeout=15, follow_redirects=True)
            r.raise_for_status()
    except httpx.HTTPError as e:
        print(f"[scraper] error fetching {company} ({url}): {e}")
        return []
    soup = BeautifulSoup(r.text, "html.parser")
    # Broken selector? Most plain career pages link roles via /jobs//careers/
    # anchors. If empty for a real company, add a per-company selector override.
    anchors = soup.select("a[href*='/job'], a[href*='/career'], a[href*='/position']")
    jobs, seen_hrefs = [], set()
    import hashlib
    for a in anchors:
        href = a.get("href", "")
        title = a.get_text(strip=True)
        if not title or len(title) < 5 or href in seen_hrefs:
            continue
        seen_hrefs.add(href)
        full = urljoin(url, href)
        uid = hashlib.md5(full.encode()).hexdigest()[:12]
        jobs.append({
            "id": f"custom_{company.lower()}_{uid}",
            "title": title, "company": company, "location": _nearby_location(a),
            "url": full, "source": "custom", "posted_at": None, "description": "",
        })
    return jobs


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


async def _scrape_company(company: dict) -> list[dict]:
    name = company.get("name", "Unknown")
    url = company.get("careers_url")
    if not url:
        return []
    if company.get("requires_js"):
        # Phase 2: JS-rendered pages are scraped off-box by the GitHub Action
        # (scrapers/playwright_scraper.py) and arrive via /api/ingest instead.
        return []
    return await _fetch_plain(name, url)


async def _gather_sources(companies: list[dict]) -> list[dict]:
    """Run every source; concatenate results. Each source guards its own errors."""
    jobs: list[dict] = []
    jobs += await fetch_yc()
    for company in companies:
        jobs += await _scrape_company(company)
    return jobs


async def _process(jobs: list[dict], seen: dict, companies: list[dict], seed_mode: bool) -> None:
    by_name = {c.get("name", "").lower(): c for c in companies}
    contacts_cache: dict[str, list] = {}  # per-cycle, per-domain
    for job in state.get_new_jobs(seen, jobs):
        job["scraped_at"] = _now()
        if not job.get("posted_at"):
            job["posted_at"] = job["scraped_at"]
        job["matched"] = matches(job)

        if job["matched"]:
            company = by_name.get(job.get("company", "").lower())
            domain = company.get("domain") if company else None
            if company and company.get("tier") == 1 and domain:
                if domain not in contacts_cache:
                    contacts_cache[domain] = await find_contacts(domain)
                job["contacts"] = contacts_cache[domain]
            if not seed_mode:
                _push_live(job)
                await send_email_alert(job)
        seen[job["id"]] = job


async def poll_loop() -> None:
    while True:
        try:
            seen = state.load_seen()
            seed_mode = not seen  # first ever run: persist silently, don't email
            seen = state.purge_old(seen, days=PURGE_AFTER_DAYS)
            companies = state.load_companies()

            jobs = await _gather_sources(companies)
            await _process(jobs, seen, companies, seed_mode)

            # Discovered careers pages from funding signals
            for entry in state.load_funding_queue():
                url = entry.get("careers_url")
                if not url:
                    continue
                found = await _scrape_company({"name": entry.get("company", "Funded"),
                                               "careers_url": url})
                for j in found:
                    j["source"] = "funding"
                await _process(found, seen, companies, seed_mode)

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
            state.save_funding_queue(queue)
        except Exception as e:
            print(f"[scraper] funding loop failed: {e}")
