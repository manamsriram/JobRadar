"""Headless-browser scraper for custom career pages (no Greenhouse/Lever/Ashby).

Only used for companies with ats:"custom". The default seed ships none — custom
pages are per-site opt-in (the generic selector below grabs junk on SPAs). Launch
flags keep Chromium alive under the 512MB free tier (F10).
"""
import hashlib

from playwright.async_api import async_playwright

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


async def fetch_custom(company: str, url: str) -> list[dict]:
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        page = await browser.new_page(user_agent=_UA)
        await page.goto(url, wait_until="networkidle")
        await page.wait_for_timeout(2000)  # let JS render

        links = await page.eval_on_selector_all(
            "a[href*='/jobs/'], a[href*='/careers/'], a[href*='/position']",
            "els => els.map(e => ({ title: e.innerText.trim(), href: e.href }))",
        )
        await browser.close()

    jobs = []
    for link in links:
        if not link["title"] or len(link["title"]) < 5:
            continue
        uid = hashlib.md5(link["href"].encode()).hexdigest()[:12]
        jobs.append({
            "id": f"custom_{company.lower()}_{uid}",
            "title": link["title"],
            "location": "See listing",
            "url": link["href"],
            "posted_at": None,  # falls back to scraped_at in the orchestrator
        })
    return jobs
