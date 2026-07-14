import asyncio
import logging
import random
from datetime import datetime, timezone

import state
from config import (
    COMPANIES, DIGEST_INTERVAL_HOURS, DIGEST_MAX_JOBS,
    POLL_INTERVAL_SECONDS, PURGE_AFTER_DAYS,
)
from enricher import find_contacts
from filter import matches
from notifier import send_digest
from scrapers.ashby import fetch_ashby
from scrapers.greenhouse import fetch_greenhouse
from scrapers.lever import fetch_lever
from scrapers.playwright_scraper import fetch_custom

log = logging.getLogger(__name__)

# Live feed for the SSE endpoint. Bounded + drop-oldest so an idle (no client)
# server can't leak memory (F7). History is served separately via GET /api/jobs.
new_jobs_queue: asyncio.Queue = asyncio.Queue(maxsize=100)

ATS_FETCHERS = {
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "ashby": fetch_ashby,
    "custom": fetch_custom,
}


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


async def scrape_company(company: dict) -> list[dict]:
    ats = company["ats"]
    fetcher = ATS_FETCHERS[ats]
    try:
        if ats == "custom":
            jobs = await fetcher(company["name"], company["url"])
        else:
            jobs = await fetcher(company["slug"])
    except Exception as e:  # one bad company must not kill the cycle
        log.warning("Scrape error on %s: %s", company["name"], e)
        return []

    for job in jobs:
        job["company"] = company["name"]
        job["ats"] = ats
        if not job.get("posted_at"):
            job["posted_at"] = _now()
    return jobs


async def poll_loop(companies: list[dict] = COMPANIES) -> None:
    while True:
        try:
            try:
                await state.purge_old(days=PURGE_AFTER_DAYS)
            except Exception as e:  # e.g. `applied` column not migrated yet
                log.warning("Purge skipped: %s", e)
            seed_mode = await state.is_empty()  # F2: silent-seed the first ever run
            seen = await state.load_seen()
            contacts_cache: dict[str, list] = {}  # per-cycle, per-domain (F6)

            for company in companies:
                fetched = await scrape_company(company)
                for job in state.get_new_jobs(seen, fetched):
                    job["scraped_at"] = _now()
                    job["matched"] = matches(job)
                    job["notified"] = False

                    if job["matched"]:
                        domain = company.get("domain")
                        if company.get("tier") == 1 and domain:
                            if domain not in contacts_cache:
                                contacts_cache[domain] = await find_contacts(domain)
                            job["contacts"] = contacts_cache[domain]
                        if seed_mode:
                            job["notified"] = True  # seed silently, don't email
                        else:
                            _push_live(job)

                    seen[job["id"]] = job
                    await state.upsert_job(job)  # F3: persist per job

                await asyncio.sleep(random.uniform(1.5, 3.0))  # polite delay

            log.info("Poll cycle complete. Sleeping %ds.", POLL_INTERVAL_SECONDS)
        except Exception as e:
            log.error("Poll loop cycle failed: %s", e)
        await asyncio.sleep(POLL_INTERVAL_SECONDS)


async def digest_loop() -> None:
    interval = DIGEST_INTERVAL_HOURS * 3600
    while True:
        await asyncio.sleep(interval)  # sleep first — avoids a burst on each restart
        try:
            jobs = await state.get_matched_unnotified(DIGEST_MAX_JOBS)
            if await send_digest(jobs):
                await state.mark_notified([j["id"] for j in jobs])
                log.info("Digest sent for %d job(s).", len(jobs))
        except Exception as e:
            log.error("Digest loop failed: %s", e)
