"""Visa-sponsor / new-grad company seeding (design doc §3 follow-up).

Unlike funding_watcher (an unbounded live feed), the visa-sponsor list is a
small, hand-curated set of companies known for both H-1B sponsorship and
active new-grad hiring — there's no reliable live API for that combination
(USCIS/DOL disclosure data is bulk CSV, no per-company "hires new grads"
signal). Seeded from data/visa_sponsor_seeds.json, which is meant to be
edited directly like companies.json.

Careers URLs are hardcoded per seed row rather than auto-probed: large
enterprises put careers pages on dedicated ATS subdomains (jobs.apple.com,
careers.google.com, Workday tenants), not the conventional paths
discover_careers_url() checks — that probe was built for funding-round
startups sitting on their own root domain. discover_careers_url() is kept
as a best-effort fallback only for a seed row a user adds without a known
careers_url.
"""
import httpx

import state
from net_safety import is_safe_url
from signals.careers_discovery import discover_careers_url

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def load_seed_companies() -> list[dict]:
    return state.load_visa_sponsor_seeds()


def _already_present(name: str, domain: str, companies: list[dict]) -> bool:
    name_l, domain_l = name.lower(), domain.lower()
    return any(
        c.get("name", "").lower() == name_l or c.get("domain", "").lower() == domain_l
        for c in companies
    )


async def _url_is_live(url: str) -> bool:
    """Hardcoded seed URLs (ATS vendors reshuffle paths) and discovered URLs
    both need a liveness check before landing in companies.json — a dead
    careers_url just means the scraper 404s on that company forever until
    someone notices. Follows redirects (each hop re-validated) same as
    careers_discovery's own check."""
    if not is_safe_url(url):
        return False
    try:
        async with httpx.AsyncClient(headers={"User-Agent": _UA}, follow_redirects=True) as client:
            r = await client.get(url, timeout=10)
            return r.status_code == 200
    except httpx.HTTPError as e:
        print(f"[visa_sponsors] error verifying {url}: {e}")
        return False


async def merge_seed_companies(companies: list[dict]) -> list[dict] | None:
    """Diff data/visa_sponsor_seeds.json against `companies`, append any new
    seed row (dedup by name/domain, same check as _promote_funding_company).
    A seed row missing careers_url gets one best-effort discover_careers_url()
    attempt. Every candidate URL (hardcoded or discovered) is verified live
    before being added — skipped otherwise. Returns the updated list if
    anything was added, else None."""
    added: list[dict] = []
    for seed in load_seed_companies():
        name, domain = seed.get("name"), seed.get("domain")
        if not name or not domain or _already_present(name, domain, companies + added):
            continue
        careers_url = seed.get("careers_url") or await discover_careers_url(domain)
        if not careers_url or not await _url_is_live(careers_url):
            continue
        added.append({
            "name": name,
            "source": seed.get("source", "visa-sponsor"),
            "careers_url": careers_url,
            "domain": domain,
            "tier": seed.get("tier", 2),
            "requires_js": seed.get("requires_js", True),
        })

    if not added:
        return None
    companies = companies + added
    state.save_companies(companies)
    print(f"[visa_sponsors] merged {len(added)} new sponsor compan{'y' if len(added) == 1 else 'ies'} into companies.json")
    return companies
