"""TLDR newsletter jobs scraper (httpx + BeautifulSoup).

TLDR runs a lightweight jobs page listing curated startup roles. Server-rendered,
so a plain GET works. Realistic UA + jitter keep it polite.
"""
import asyncio
import hashlib
import random

import httpx
from bs4 import BeautifulSoup

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_URL = "https://jobs.tldr.tech/"


async def fetch_tldr() -> list[dict]:
    await asyncio.sleep(random.uniform(1.5, 3.0))  # polite jitter
    try:
        async with httpx.AsyncClient(headers={"User-Agent": _UA}) as client:
            r = await client.get(_URL, timeout=15, follow_redirects=True)
            r.raise_for_status()
    except httpx.HTTPError as e:
        print(f"[tldr_scraper] error: {e}")
        return []

    soup = BeautifulSoup(r.text, "html.parser")
    # Broken selector? TLDR renders each posting as an <a href="/jobs/..."> card.
    # If this yields 0, inspect the page and update to the current card anchor.
    anchors = soup.select("a[href*='/jobs/'], a[href*='/companies/']")

    jobs: list[dict] = []
    seen_hrefs = set()
    for a in anchors:
        href = a.get("href", "")
        title = a.get_text(" ", strip=True)
        if not title or len(title) < 5 or href in seen_hrefs:
            continue
        seen_hrefs.add(href)
        url = href if href.startswith("http") else f"https://jobs.tldr.tech{href}"
        uid = hashlib.md5(href.encode()).hexdigest()[:12]
        jobs.append({
            "id": f"tldr_{uid}",
            "title": title,
            "company": "TLDR listing",
            "location": "See listing",
            "url": url,
            "source": "tldr",
            "posted_at": None,
            "description": "",
        })
    return jobs
