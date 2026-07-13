"""Hunter.io contact enrichment for tier-1 companies.

Degrades gracefully when HUNTER_API_KEY is unset (F5). Contacts are memoized
per domain per cycle by the orchestrator so one company costs at most one
Hunter credit regardless of how many jobs matched (F6).
"""
import logging
import os

import httpx

log = logging.getLogger(__name__)

TARGET_TITLES = [
    "engineering manager", "software engineer", "tech lead",
    "recruiter", "talent", "hiring",
]


async def find_contacts(domain: str) -> list[dict]:
    api_key = os.getenv("HUNTER_API_KEY")
    if not api_key:
        return []  # enrichment disabled — no key

    url = "https://api.hunter.io/v2/domain-search"
    params = {"domain": domain, "api_key": api_key, "limit": 10}
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(url, params=params, timeout=8)
            r.raise_for_status()
            emails = r.json().get("data", {}).get("emails", [])
    except (httpx.HTTPError, ValueError) as e:
        log.warning("Hunter lookup failed for %s: %s", domain, e)
        return []

    contacts = []
    for e in emails:
        title = (e.get("position") or "").lower()
        if any(t in title for t in TARGET_TITLES):
            contacts.append({
                "name": f"{e.get('first_name', '')} {e.get('last_name', '')}".strip(),
                "title": e.get("position"),
                "email": e.get("value"),
                "linkedin": e.get("linkedin"),
            })
    return contacts[:3]  # cap at 3 per company
