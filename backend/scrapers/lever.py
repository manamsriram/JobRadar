from datetime import datetime, timezone

import httpx

from scrapers.util import plaintext


async def fetch_lever(slug: str) -> list[dict]:
    url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
    async with httpx.AsyncClient() as client:
        r = await client.get(url, timeout=10)
        r.raise_for_status()
        raw = r.json()

    jobs = []
    for j in raw:
        created_ms = j.get("createdAt")
        posted_at = (
            datetime.fromtimestamp(created_ms / 1000, tz=timezone.utc).isoformat()
            if created_ms else None
        )
        jobs.append({
            "id": f"lever_{slug}_{j['id']}",
            "title": j.get("text", ""),
            "location": (j.get("categories") or {}).get("location", ""),
            "url": j.get("hostedUrl", ""),
            "posted_at": posted_at,
            "description": plaintext(j.get("descriptionPlain")),
        })
    return jobs
