"""Auto-discovery of new companies for companies.json — grows the curated list
without manual entry. Three sources, all capped and deduped by domain (not
just name) before any write:

- funding_promotion: a funding-queue entry whose careers page already yielded
  a matched job (was scraper._promote_funding_company; moved here so all
  auto-added rows share one schema/cap/log path).
- yc_frequency: a YC company seen across 2+ poll cycles gets a careers-page
  lookup (persisted counts survive restarts — see state.load_discovery_counts).
- url_domain_extraction: any scraped job's URL whose domain isn't already
  covered gets the same careers-page lookup.

Every accepted row is tier 3 / source "auto-discovered" so it never consumes
Hunter.io credits until a human promotes it. Capped at MAX_NEW_PER_CYCLE per
call — no ceiling on total list size, it's meant to keep growing.
"""
import re
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

import state
from net_safety import is_safe_url
from signals.careers_discovery import discover_careers_url

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

MAX_NEW_PER_CYCLE = 5
YC_FREQUENCY_THRESHOLD = 2

# Greenhouse/Lever/Ashby are multi-tenant: the host is shared across every
# company on the platform, and the *first path segment* is the company's
# board slug (boards.greenhouse.io/acme, jobs.lever.co/acme) — so the host
# alone can't be the dedupe key, but host+slug can, and the board root is a
# ready-made careers_url with no discover_careers_url probing needed.
_ATS_PATH_HOSTS = {
    "greenhouse.io", "boards.greenhouse.io", "job-boards.greenhouse.io",
    "lever.co", "jobs.lever.co",
    "ashbyhq.com", "jobs.ashbyhq.com",
}

# True aggregators: search/listing surfaces with no per-company slug to
# resolve a board from, so there's nothing scrapeable to attribute here.
# (Distinct from _ATS_PATH_HOSTS above, which ARE scrapeable per-company.)
_AGGREGATOR_HOSTS = {
    "linkedin.com", "indeed.com", "levels.fyi", "ycombinator.com",
    "wellfound.com", "angel.co", "builtin.com", "glassdoor.com",
    "ziprecruiter.com",
}

_SUFFIX_RE = re.compile(r"\b(inc|llc|corp|co|ltd)\.?\s*$", re.IGNORECASE)


def _normalize_name(name: str) -> str:
    return _SUFFIX_RE.sub("", (name or "").strip().lower()).strip(" ,.")


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def _host(url: str) -> str | None:
    try:
        host = urlparse(url).netloc.lower()
    except ValueError:
        return None
    host = host.split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    if not host or "." not in host:
        return None
    if not is_safe_url(f"https://{host}"):
        return None
    return host


async def _has_job_links(url: str) -> bool:
    """Plain GET; True if the page itself carries job-like anchors. Any fetch
    problem (including a JS-rendered shell with no server-side links) reads
    as False, not an exception — the caller just flags requires_js instead."""
    try:
        async with httpx.AsyncClient(headers={"User-Agent": _UA}) as client:
            r = await client.get(url, timeout=10, follow_redirects=True)
            r.raise_for_status()
    except httpx.HTTPError:
        return False
    soup = BeautifulSoup(r.text, "html.parser")
    return bool(soup.select("a[href*='/job'], a[href*='/career'], a[href*='/position']"))


def _matches_host_set(host: str, hosts: set[str]) -> bool:
    return any(host == h or host.endswith(f".{h}") for h in hosts)


def _ats_board_slug(url: str, host: str) -> str | None:
    """First path segment of a Greenhouse/Lever/Ashby job URL — that's the
    company's board slug on the shared host."""
    path = urlparse(url).path.strip("/")
    return path.split("/")[0] if path else None


def _known_domains(companies: list[dict]) -> set[str]:
    return {(c.get("domain") or "").lower() for c in companies if c.get("domain")}


def _known_names(companies: list[dict]) -> set[str]:
    return {_normalize_name(c.get("name")) for c in companies if c.get("name")}


def _make_record(name: str, domain: str, careers_url: str, requires_js: bool, method: str) -> dict:
    return {
        "name": name,
        "source": "auto-discovered",
        "careers_url": careers_url,
        "domain": domain,
        "tier": 3,
        "requires_js": requires_js,
        "discovered_at": datetime.now(timezone.utc).isoformat(),
        "discovery_method": method,
    }


def _add(companies: list[dict], record: dict) -> list[dict]:
    if not record.get("name") or not record.get("domain"):
        print(f"[discovery] skipping record with missing name/domain: {record}")
        return companies
    companies = companies + [record]
    state.save_companies(companies)
    print(f"[discovery] Added {record['name']} ({record['domain']}) via {record['discovery_method']}")
    return companies


async def _from_funding(companies: list[dict], funding_results: list[tuple[dict, list[dict]]], budget: int) -> tuple[list[dict], int]:
    """A funding-queue entry that already scraped a matched job via the plain
    (non-browser) fetcher is proven to not require JS — no re-validation needed."""
    added = 0
    for entry, jobs in funding_results:
        if added >= budget:
            break
        if not any(j.get("matched") for j in jobs):
            continue
        name, domain, url = entry.get("company"), entry.get("domain"), entry.get("careers_url")
        if not name or not domain or not url:
            continue
        if domain.lower() in _known_domains(companies) or _normalize_name(name) in _known_names(companies):
            continue
        companies = _add(companies, _make_record(name, domain.lower(), url, False, "funding_promotion"))
        added += 1
    return companies, added


async def _from_yc_frequency(companies: list[dict], yc_jobs: list[dict], budget: int) -> tuple[list[dict], int]:
    added = 0
    counts = state.load_discovery_counts()
    seen_this_cycle: dict[str, str] = {}  # normalized -> original casing
    for job in yc_jobs:
        name = job.get("company")
        if not name:
            continue
        norm = _normalize_name(name)
        if norm in _known_names(companies):
            continue
        seen_this_cycle.setdefault(norm, name)

    for norm in seen_this_cycle:
        counts[norm] = counts.get(norm, 0) + 1
    state.save_discovery_counts(counts)

    for norm, name in seen_this_cycle.items():
        if added >= budget:
            break
        if counts.get(norm, 0) < YC_FREQUENCY_THRESHOLD:
            continue
        domain = f"{_slug(name)}.com"
        if not _slug(name) or domain in _known_domains(companies):
            continue
        url = await discover_careers_url(domain)
        if not url:
            continue
        requires_js = not await _has_job_links(url)
        companies = _add(companies, _make_record(name, domain, url, requires_js, "yc_frequency"))
        added += 1
    return companies, added


async def _from_url_domains(companies: list[dict], jobs: list[dict], budget: int) -> tuple[list[dict], int]:
    added = 0
    tried: set[str] = set()
    for job in jobs:
        if added >= budget:
            break
        job_url = job.get("url", "")
        host = _host(job_url)
        if not host or _matches_host_set(host, _AGGREGATOR_HOSTS):
            continue

        if _matches_host_set(host, _ATS_PATH_HOSTS):
            slug = _ats_board_slug(job_url, host)
            if not slug:
                continue
            domain_key = f"{host}/{slug}"
            careers_url = f"https://{host}/{slug}"
        else:
            domain_key = host
            careers_url = None  # resolved below via discover_careers_url

        if domain_key in tried or domain_key in _known_domains(companies):
            continue
        tried.add(domain_key)

        if careers_url is None:
            careers_url = await discover_careers_url(host)
            if not careers_url:
                continue

        requires_js = not await _has_job_links(careers_url)
        name = job.get("company") or host
        if _normalize_name(name) in _known_names(companies):
            continue
        companies = _add(companies, _make_record(name, domain_key, careers_url, requires_js, "url_domain_extraction"))
        added += 1
    return companies, added


async def discover_new_companies(
    companies: list[dict],
    jobs: list[dict],
    funding_results: list[tuple[dict, list[dict]]],
) -> list[dict]:
    """Run all three discovery sources against this cycle's scrape results.
    Returns the (possibly extended) companies list; persists it to disk itself
    whenever a row is added (see _add), same as the code this replaces."""
    budget = MAX_NEW_PER_CYCLE
    companies, n = await _from_funding(companies, funding_results, budget)
    budget -= n
    yc_jobs = [j for j in jobs if j.get("source") == "yc"]
    companies, n = await _from_yc_frequency(companies, yc_jobs, budget)
    budget -= n
    companies, _ = await _from_url_domains(companies, jobs, budget)
    return companies
