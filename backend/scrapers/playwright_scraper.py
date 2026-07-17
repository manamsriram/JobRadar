"""Headless-browser scrapers for JS-rendered pages (custom career sites, Levels.fyi).

Phase 2 split: this module never runs inside the always-on host container. It's
invoked standalone by a GitHub Action (see the `__main__` block and
.github/workflows/playwright_scraper.yml), which POSTs the resulting JSON to
/api/ingest — the host VM never has to hold a 300MB+ browser in RAM, so it fits
on a 1GB free-tier box.

Every scrape sends a realistic User-Agent and jitters 1.5–3s to stay polite.
"""
import argparse
import asyncio
import hashlib
import json
import random

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
# Launch flags keep Chromium alive under constrained RAM.
_LAUNCH_ARGS = ["--no-sandbox", "--disable-dev-shm-usage"]


def _uid(prefix: str, key: str) -> str:
    return f"{prefix}_{hashlib.md5(key.encode()).hexdigest()[:12]}"


async def fetch_custom_js(company: str, url: str) -> list[dict]:
    """Scrape a JS-rendered custom career page. Returns matched-shape job dicts."""
    jobs: list[dict] = []
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(args=_LAUNCH_ARGS)
            page = await browser.new_page(user_agent=_UA)
            await page.goto(url, wait_until="networkidle", timeout=30000)
            await page.wait_for_timeout(random.randint(1500, 3000))  # let JS render + jitter
            # Broken selector? Career SPAs render each role as an <a> pointing at a
            # job/careers/position detail path. If this returns 0, open the page and
            # look for the anchor pattern the new markup uses (often a data-testid).
            links = await page.eval_on_selector_all(
                "a[href*='/jobs/'], a[href*='/careers/'], a[href*='/position']",
                "els => els.map(e => ({ title: e.innerText.trim(), href: e.href }))",
            )
            await browser.close()
    except Exception as e:
        print(f"[playwright_scraper] error scraping {company} ({url}): {e}")
        return []

    for link in links:
        if not link["title"] or len(link["title"]) < 5:
            continue
        jobs.append({
            "id": _uid(f"custom_{company.lower()}", link["href"]),
            "title": link["title"],
            "company": company,
            "location": "",
            "url": link["href"],
            "source": "custom",
            "posted_at": None,  # falls back to scraped_at in the orchestrator
            "description": "",
        })
    return jobs


async def fetch_levels() -> list[dict]:
    """Scrape the Levels.fyi job board (JS-rendered) for new-grad SWE roles.

    Levels links each posting via a query string (/jobs?jobId=<id>), not a
    path segment, and wraps title/company/location in CSS-module classes
    with a build-hash prefix (e.g. "...__h_Bkua__companyJobTitle") that
    changes across deploys — hence substring class matching below rather
    than exact class names. Rendered HTML is parsed with BeautifulSoup
    instead of in-page JS since the title span nests a "posted X ago" date
    that needs stripping, which is fiddlier to do inside eval_on_selector_all.
    """
    url = "https://www.levels.fyi/jobs?searchText=software%20engineer"
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(args=_LAUNCH_ARGS)
            page = await browser.new_page(user_agent=_UA)
            await page.goto(url, wait_until="networkidle", timeout=30000)
            await page.wait_for_timeout(random.randint(1500, 3000))
            html = await page.content()
            await browser.close()
    except Exception as e:
        print(f"[playwright_scraper] error scraping levels.fyi: {e}")
        return []

    soup = BeautifulSoup(html, "html.parser")
    jobs: list[dict] = []
    for a in soup.select("a[href*='jobId=']"):
        href = a.get("href", "")
        title_el = a.select_one("[class*=companyJobTitle]")
        if not title_el:
            continue
        date_el = title_el.select_one("[class*=companyJobDate]")
        if date_el:
            date_el.extract()  # drop nested "posted X ago" text before reading the title
        title = title_el.get_text(strip=True)
        if not title or len(title) < 5:
            continue
        loc_el = a.select_one("[class*=companyJobLocation]")
        location = loc_el.get_text(strip=True) if loc_el else ""
        name_el = a.find_previous(class_=lambda c: c and "companyName" in c)
        company = name_el.get_text(strip=True) if name_el else "Unknown"
        full_url = href if href.startswith("http") else f"https://www.levels.fyi{href}"
        jobs.append({
            "id": _uid("levels", href),
            "title": title,
            "company": company,
            "location": location,
            "url": full_url,
            "source": "levels",
            "posted_at": None,
            "description": "",
        })
    return jobs


# ---- GitHub Action entrypoint ----
# Invoked by .github/workflows/playwright_scraper.yml:
#   python -m scrapers.playwright_scraper --companies companies.json --output jobs.json
# The workflow then POSTs jobs.json to /api/ingest. Never called by the host app.
async def _run(companies_path: str | None, output_path: str) -> None:
    all_jobs: list[dict] = await fetch_levels()
    if companies_path:
        companies = json.loads(open(companies_path).read())
        for c in companies:
            if c.get("requires_js") and c.get("careers_url"):
                all_jobs.extend(await fetch_custom_js(c["name"], c["careers_url"]))
                await asyncio.sleep(random.uniform(1.5, 3.0))
    with open(output_path, "w") as f:
        json.dump(all_jobs, f, ensure_ascii=False, indent=2)
    print(f"[playwright_scraper] wrote {len(all_jobs)} job(s) to {output_path}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Phase 2 standalone Playwright scraper")
    ap.add_argument("--companies", help="path to companies.json (requires_js entries)")
    ap.add_argument("--output", default="jobs.json", help="where to write scraped jobs")
    args = ap.parse_args()
    asyncio.run(_run(args.companies, args.output))
