"""Careers-page discovery for funding-signal companies (design doc §3).

Given a company domain, probe the handful of conventional careers paths and
return the first that responds. Used to turn a funding headline into a scrapeable
careers URL. Best-effort: a miss just means no careers page found yet.
"""
import httpx

from net_safety import is_safe_url

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_CANDIDATE_PATHS = [
    "/careers", "/jobs", "/company/careers", "/about/careers",
    "/careers/jobs", "/join", "/join-us", "/work-with-us",
]


async def _get_following_safe_redirects(client: httpx.AsyncClient, url: str, max_hops: int = 5) -> httpx.Response | None:
    """Manual redirect follow: validate every hop's host, not just the first."""
    for _ in range(max_hops):
        if not is_safe_url(url):
            return None
        r = await client.get(url, timeout=10)
        if r.is_redirect:
            url = str(r.next_request.url) if r.next_request else r.headers.get("location", "")
            if not url:
                return None
            continue
        return r
    return None


async def discover_careers_url(domain: str) -> str | None:
    """Return the first reachable careers URL for `domain`, or None."""
    domain = domain.strip().rstrip("/")
    if not domain.startswith("http"):
        domain = f"https://{domain}"
    async with httpx.AsyncClient(headers={"User-Agent": _UA}, follow_redirects=False) as client:
        for path in _CANDIDATE_PATHS:
            url = f"{domain}{path}"
            try:
                r = await _get_following_safe_redirects(client, url)
                if r is not None and r.status_code == 200:
                    return str(r.url)
            except httpx.HTTPError as e:
                print(f"[careers_discovery] error probing {url}: {e}")
                continue
    return None
