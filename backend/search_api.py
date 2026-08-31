"""One web-search call, three interchangeable backends.

Why this exists: driving google.com/search through Chromium gets walled by a
CAPTCHA on the first request from a datacenter IP — confirmed both in design
and again live from this machine. The fix is not to out-run the wall (proxy
rotation, fingerprint spoofing, solver services — an arms race that keeps the
scraper permanently flaky) but to stop knocking on it. Serper returns the same
Google results over a supported API, DuckDuckGo's HTML endpoint is free and
never challenged us, and the Chromium path stays only as a last resort.

Backend order (SEARCH_BACKEND overrides): serper when SERPER_API_KEY is set,
else duckduckgo. On an empty or failed result the next backend is tried, so a
missing key or a throttled endpoint degrades instead of returning "no jobs".
"""
import logging
import os
import random
import re
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

from bs4 import BeautifulSoup
import httpx

logger = logging.getLogger(__name__)

SERPER_API_KEY = os.getenv("SERPER_API_KEY", "")
SEARCH_BACKEND = os.getenv("SEARCH_BACKEND", "")
SEARCH_TIMEOUT = float(os.getenv("SEARCH_TIMEOUT_SECONDS", "20"))

_SERPER_URL = "https://google.serper.dev/search"
_DDG_URL = "https://html.duckduckgo.com/html/"
_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


class Blocked(Exception):
    """The backend was challenged (CAPTCHA/rate limit), not merely empty.

    Distinct from "no results" on purpose: an empty list means the query found
    nothing, a block means we learned nothing and must back off.
    """


def backend_order() -> list[str]:
    if SEARCH_BACKEND:
        return [SEARCH_BACKEND]
    return (["serper"] if SERPER_API_KEY else []) + ["duckduckgo"]


async def search(query: str, days: int | None = 1) -> tuple[list[dict], bool, bool]:
    """Run one query. Returns (results, ok, blocked), matching the contract the
    scrapers already use. Each result is {"title", "url", "snippet"}.

    `days` restricts to the last N days where the backend supports it; None
    means no recency filter (recruiter discovery wants old pages too).
    """
    blocked = False
    for name in backend_order():
        runner = _BACKENDS.get(name)
        if runner is None:
            logger.warning("unknown SEARCH_BACKEND %r, skipping", name)
            continue
        try:
            results = await runner(query, days)
        except Blocked:
            logger.warning("%s blocked query %r", name, query)
            blocked = True
            continue
        except Exception as e:
            logger.warning("%s search failed for %r: %s", name, query, e)
            continue
        if results:
            return results, True, False
        # An honestly-empty result from a working backend is still a success —
        # but try the next one before believing it.
        blocked = False
    return [], not blocked, blocked


# Serper's free tier rejects num > 10 outright ("Query pattern not allowed for
# free accounts", HTTP 400) — verified live. Ten results is plenty per query
# here, and asking for more silently demotes every search to DuckDuckGo.
SERPER_MAX_RESULTS = 10


async def _serper(query: str, days: int | None) -> list[dict]:
    payload = {"q": query, "num": SERPER_MAX_RESULTS}
    if days:
        payload["tbs"] = f"qdr:d{days}" if days > 1 else "qdr:d"
    async with httpx.AsyncClient(timeout=SEARCH_TIMEOUT) as client:
        r = await client.post(_SERPER_URL, json=payload,
                              headers={"X-API-KEY": SERPER_API_KEY})
    if r.status_code in (401, 403):
        raise RuntimeError(f"serper rejected the API key ({r.status_code})")
    if r.status_code == 429:
        raise Blocked("serper rate limit / out of credits")
    if r.status_code == 400:
        # Carry Serper's own message: a 400 here is a rejected query shape, and
        # the reason is the only thing that tells them apart.
        raise RuntimeError(f"serper rejected the query: {r.text[:200]}")
    r.raise_for_status()
    return [
        {"title": o.get("title") or "", "url": o.get("link") or "",
         "snippet": o.get("snippet") or ""}
        for o in (r.json().get("organic") or [])
        if o.get("link")
    ]


# DDG's HTML endpoint wraps every outbound link as /l/?uddg=<encoded target>.
_DDG_REDIRECT = re.compile(r"^(?:https?:)?//duckduckgo\.com/l/")


def _unwrap_ddg(href: str) -> str | None:
    if _DDG_REDIRECT.match(href):
        target = parse_qs(urlparse(href).query).get("uddg")
        return target[0] if target else None
    return href if href.startswith("http") else None


async def _duckduckgo(query: str, days: int | None) -> list[dict]:
    params = {"q": query}
    if days:
        params["df"] = "d" if days <= 1 else "w"
    # A bare POST gets 403 and a GET gets 202 (their "slow down" code) — the
    # browser-shaped header set below is what actually returns results.
    async with httpx.AsyncClient(timeout=SEARCH_TIMEOUT, follow_redirects=True) as client:
        r = await client.post(_DDG_URL, data=params, headers=_DDG_HEADERS)
    if r.status_code in (202, 403, 429):
        raise Blocked(f"duckduckgo throttled ({r.status_code})")
    r.raise_for_status()
    return parse_ddg_html(r.text)


def parse_ddg_html(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    out, seen = [], set()
    for a in soup.select("a.result__a"):
        url = _unwrap_ddg(a.get("href") or "")
        if not url or url in seen:
            continue
        seen.add(url)
        snippet = a.find_parent(class_="result")
        snippet_el = snippet.select_one(".result__snippet") if snippet else None
        out.append({
            "title": a.get_text(strip=True), "url": url,
            "snippet": snippet_el.get_text(strip=True) if snippet_el else "",
        })
    return out


async def _playwright(query: str, days: int | None) -> list[dict]:
    """Last resort: google.com/search through real Chromium. Expect a CAPTCHA
    from any datacenter IP — kept only so a keyless, DDG-throttled run has
    somewhere left to go."""
    from playwright.async_api import async_playwright

    url = "https://www.google.com/search?" + urlencode(
        {"q": query, **({"tbs": "qdr:d"} if days else {})})
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-http2"])
        async with browser:
            page = await browser.new_page(user_agent=_UA)
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(random.randint(1500, 3000))
            final_url, html = page.url, await page.content()
    if "google.com/sorry" in final_url or "/sorry/index" in html:
        raise Blocked("google CAPTCHA")
    return parse_serp_anchors(html)


def parse_serp_anchors(html: str, base_url: str = "https://www.google.com") -> list[dict]:
    """Structure-agnostic SERP scrape: every <a href> with usable link text.
    Google's result markup is unverifiable from here (we get walled before it
    renders), so this deliberately depends on nothing but anchors."""
    soup = BeautifulSoup(html, "html.parser")
    out, seen = [], set()
    for a in soup.find_all("a", href=True):
        href = urljoin(base_url, a["href"])
        if not href.startswith("http") or href in seen or "google.com" in href:
            continue
        text = a.get_text(strip=True)
        if len(text) < 5:
            continue
        seen.add(href)
        out.append({"title": text, "url": href, "snippet": ""})
    return out


_DDG_HEADERS = {
    "User-Agent": _UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://duckduckgo.com/",
    "Content-Type": "application/x-www-form-urlencoded",
}

_BACKENDS = {"serper": _serper, "duckduckgo": _duckduckgo, "playwright": _playwright}
