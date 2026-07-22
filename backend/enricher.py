"""Hunter.io on-demand contact lookup — fired per job application, not per
poll cycle (F5: degrades to None when HUNTER_API_KEY is unset).

Design: Hunter's free plan bills 1 credit per email actually returned, 50/mo
total, no bulk discount. Each lookup fetches one contact (limit=1) for
1 credit; a per-domain cache (data/company_contacts.json) accumulates
contacts found for that company across every job/application, so any other
job at the same company sees them for free, and asking again fetches the
*next* ranked contact (via Hunter's `offset` param) instead of re-billing
for the one already found.
"""
import os
import re
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx

import state

CONTACTS_CACHE_FILE = state.DATA_DIR / "company_contacts.json"

# ATS boards and job aggregators are never the employer's own domain — reject
# them outright rather than mistaking e.g. a levels.fyi/YC listing URL for the
# posting company's site (levels.fyi and ycombinator.com surface jobs for any
# company, so trusting their host as "the employer's domain" attributes the
# contact lookup to the aggregator, not the actual company).
_ATS_HOSTS = {
    "greenhouse.io", "boards.greenhouse.io", "job-boards.greenhouse.io",
    "lever.co", "jobs.lever.co",
    "ashbyhq.com", "jobs.ashbyhq.com",
    "myworkdayjobs.com", "linkedin.com", "indeed.com",
    "levels.fyi", "ycombinator.com", "wellfound.com", "angel.co",
    "builtin.com", "glassdoor.com", "ziprecruiter.com",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _strip_www(host: str) -> str:
    return host[4:] if host.startswith("www.") else host


def _slugify(name: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def resolve_domain(company_name: str, posting_url: str | None) -> tuple[str, bool] | None:
    """Returns (domain, guessed). guessed=True means this is a best-effort
    slug guess, not a confirmed domain — flag it to the caller/UI."""
    companies = {(c.get("name") or "").lower(): c for c in state.load_companies()}
    curated = companies.get((company_name or "").strip().lower())
    if curated and curated.get("domain"):
        return curated["domain"], False

    if posting_url:
        host = _strip_www(urlparse(posting_url).netloc.lower())
        if host and not any(host == h or host.endswith(f".{h}") for h in _ATS_HOSTS):
            return host, False

    slug = _slugify(company_name)
    if not slug:
        return None
    return f"{slug}.com", True


# ---- Per-company contact cache ----
# {domain: {"contacts": [...], "researched_at": iso8601, "domain_guessed": bool}}
def _load_cache() -> dict:
    return state._read_json(CONTACTS_CACHE_FILE, {})


def get_cached(domain: str) -> dict | None:
    return _load_cache().get(domain)


def get_company_contacts(company_name: str, posting_url: str | None) -> dict:
    """Pure cache read — no Hunter call, no budget touched. Lets any job at
    an already-researched company show prior contacts for free."""
    resolved = resolve_domain(company_name, posting_url)
    if resolved is None:
        return {"contacts": [], "domain_guessed": False}
    domain, guessed = resolved
    cached = get_cached(domain)
    if cached is None:
        return {"contacts": [], "domain_guessed": guessed}
    return {"contacts": cached["contacts"], "domain_guessed": cached["domain_guessed"]}


def _append_cached(domain: str, contacts: list[dict], guessed: bool) -> None:
    cache = _load_cache()
    cache[domain] = {
        "contacts": contacts,
        "researched_at": _now(),
        "domain_guessed": guessed,
    }
    state._write_json_atomic(CONTACTS_CACHE_FILE, cache)


# ---- Monthly Hunter credit budget ----
BUDGET_FILE = state.DATA_DIR / "hunter_budget.json"
# Verified against Hunter's own docs: free plan = 50 credits/month, billed
# per email returned (not per request).
MONTHLY_CALL_CAP = int(os.getenv("HUNTER_MONTHLY_CALL_CAP", "50"))


def _month() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def budget_remaining() -> bool:
    data = state._read_json(BUDGET_FILE, {})
    return data.get(_month(), 0) < MONTHLY_CALL_CAP


def _record_credit(n: int) -> None:
    """Record `n` credits actually billed by Hunter this call. n=0 (a
    zero-result search) is free — matches Hunter's real credit ledger."""
    if n <= 0:
        return
    data = state._read_json(BUDGET_FILE, {})
    month = _month()
    data = {month: data.get(month, 0)}  # drop stale months, single-key file
    data[month] += n
    state._write_json_atomic(BUDGET_FILE, data)


async def _fetch_contact_at_offset(domain: str, offset: int) -> dict | None:
    api_key = os.getenv("HUNTER_API_KEY")
    if not api_key:
        return None

    url = "https://api.hunter.io/v2/domain-search"
    params = {"domain": domain, "api_key": api_key, "limit": 1, "offset": offset}
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(url, params=params, timeout=8)
            r.raise_for_status()
            emails = r.json().get("data", {}).get("emails", [])
    except (httpx.HTTPError, ValueError) as e:
        print(f"[enricher] error: {e}")
        return None

    _record_credit(len(emails))
    if not emails:
        return None
    e = emails[0]
    return {
        "name": f"{e.get('first_name', '')} {e.get('last_name', '')}".strip(),
        "title": e.get("position"),
        "email": e.get("value"),
        "linkedin": e.get("linkedin"),
    }


async def find_contact(company_name: str, posting_url: str | None) -> dict:
    """Fetch one *new* contact for this company (the next-ranked one Hunter
    hasn't returned yet) and append it to the per-domain cache. Returns:
    {"contacts": [...], "domain_guessed": bool, "new_contact": bool}
    or {"error": "quota_exhausted"} / {"error": "no_domain"}."""
    resolved = resolve_domain(company_name, posting_url)
    if resolved is None:
        return {"error": "no_domain"}
    domain, guessed = resolved

    cached = get_cached(domain)
    existing = cached["contacts"] if cached else []
    guessed = cached["domain_guessed"] if cached else guessed

    if not budget_remaining():
        return {"error": "quota_exhausted"}

    contact = await _fetch_contact_at_offset(domain, offset=len(existing))
    if contact is None:
        return {"contacts": existing, "domain_guessed": guessed, "new_contact": False}

    updated = existing + [contact]
    # Only cache non-empty results — a zero-result guess must stay retryable
    # (e.g. after companies.json gets a corrected domain).
    _append_cached(domain, updated, guessed)
    return {"contacts": updated, "domain_guessed": guessed, "new_contact": True}
