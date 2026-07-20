"""Y Combinator job board scraper (httpx + BeautifulSoup — no browser needed).

The Work at a Startup public listing renders server-side, so a plain GET is
enough. Realistic UA + jitter keep it polite.
"""
import asyncio
import hashlib
import random

import httpx
from bs4 import BeautifulSoup

from fetch import fetch_with_retry
from text_utils import slug_to_title
from url_norm import normalize_url

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_URL = "https://www.ycombinator.com/jobs/role/software-engineer"


async def fetch_yc_description(url: str, retries: int = 1) -> str:
    """Best-effort job-detail body text. YC listing pages carry no description
    (title/company/href only), so the years-of-experience body check in
    filter.py was silently skipped for every YC job — a "3+ years" requirement
    buried in the JD, rather than the title, passed straight through. Called
    only for newly-seen jobs (see scraper._process), so the extra request is
    bounded to genuinely new postings, not the full listing every cycle."""
    await asyncio.sleep(random.uniform(0.5, 1.5))
    try:
        async with httpx.AsyncClient(headers={"User-Agent": _UA}) as client:
            r = await fetch_with_retry(client, url, retries=retries)
    except httpx.HTTPError as e:
        print(f"[yc_scraper] description fetch error for {url}: {e}")
        return ""
    soup = BeautifulSoup(r.text, "html.parser")
    return soup.get_text(" ", strip=True)


async def fetch_yc(retries: int = 2) -> tuple[list[dict], bool]:
    """Returns (jobs, ok) — ok is False only on a fetch-level failure, not on
    zero jobs found, so callers can feed it straight into source-health tracking."""
    await asyncio.sleep(random.uniform(1.5, 3.0))  # polite jitter
    try:
        async with httpx.AsyncClient(headers={"User-Agent": _UA}) as client:
            r = await fetch_with_retry(client, _URL, retries=retries)
    except httpx.HTTPError as e:
        print(f"[yc_scraper] error: {e}")
        return [], False

    soup = BeautifulSoup(r.text, "html.parser")
    # Broken selector? YC lists each role as an <a class="..."> whose href starts
    # with /companies/<slug>/jobs/. If this yields 0, open the page source and find
    # the current anchor class or the JSON blob in a <script> and target that.
    anchors = soup.select("a[href*='/jobs/']")

    jobs: list[dict] = []
    seen = set()
    for a in anchors:
        href = a.get("href", "")
        title = a.get_text(strip=True)
        if not title or len(title) < 5:
            continue
        url = href if href.startswith("http") else f"https://www.ycombinator.com{href}"
        # Canonicalize first so two hrefs differing only by tracking params
        # collapse to one row.
        key = normalize_url(url)
        if key in seen:
            continue
        seen.add(key)
        # href form: /companies/<slug>/jobs/<id>-<title>
        parts = href.strip("/").split("/")
        company = slug_to_title(parts[1]) if len(parts) > 1 else "YC Startup"
        uid = hashlib.md5(key.encode()).hexdigest()[:12]
        jobs.append({
            "id": f"yc_{uid}",
            "title": title,
            "company": company,
            "location": "",
            "url": key,
            "source": "yc",
            "posted_at": None,
            "description": "",
        })
    return jobs, True
