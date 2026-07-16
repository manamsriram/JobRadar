"""Careers-page discovery for funding-signal companies (design doc §3).

Given a company domain, probe the handful of conventional careers paths and
return the first that responds. Used to turn a funding headline into a scrapeable
careers URL. Best-effort: a miss just means no careers page found yet.
"""
import httpx

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_CANDIDATE_PATHS = [
    "/careers", "/jobs", "/company/careers", "/about/careers",
    "/careers/jobs", "/join", "/join-us", "/work-with-us",
]


async def discover_careers_url(domain: str) -> str | None:
    """Return the first reachable careers URL for `domain`, or None."""
    domain = domain.strip().rstrip("/")
    if not domain.startswith("http"):
        domain = f"https://{domain}"
    async with httpx.AsyncClient(headers={"User-Agent": _UA}, follow_redirects=True) as client:
        for path in _CANDIDATE_PATHS:
            url = f"{domain}{path}"
            try:
                r = await client.get(url, timeout=10)
                if r.status_code == 200:
                    return str(r.url)
            except httpx.HTTPError as e:
                print(f"[careers_discovery] error: {e}")
                continue
    return None
