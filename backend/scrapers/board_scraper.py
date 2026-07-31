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
from urllib.parse import urlencode

from bs4 import BeautifulSoup
import httpx
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
