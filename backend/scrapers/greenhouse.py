import httpx

from scrapers.util import plaintext


async def fetch_greenhouse(slug: str) -> list[dict]:
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
    async with httpx.AsyncClient() as client:
        r = await client.get(url, timeout=10)
        r.raise_for_status()
        raw = r.json().get("jobs", [])

    jobs = []
    for j in raw:
        jobs.append({
            "id": f"gh_{slug}_{j['id']}",
            "title": j.get("title", ""),
            "location": (j.get("location") or {}).get("name", ""),
            "url": j.get("absolute_url", ""),
            # Greenhouse exposes updated_at, not created_at (edited-old jobs look fresh).
            "posted_at": j.get("updated_at"),
            "description": plaintext(j.get("content")),
        })
    return jobs
