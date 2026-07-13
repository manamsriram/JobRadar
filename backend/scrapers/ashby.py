import httpx


async def fetch_ashby(slug: str) -> list[dict]:
    url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}"
    async with httpx.AsyncClient() as client:
        r = await client.get(url, timeout=10)
        r.raise_for_status()
        raw = r.json().get("jobs", [])

    jobs = []
    for j in raw:
        jobs.append({
            "id": f"ashby_{slug}_{j['id']}",
            "title": j.get("title", ""),
            "location": j.get("location", ""),
            "url": j.get("jobUrl") or j.get("applyUrl", ""),
            "posted_at": j.get("publishedAt"),
        })
    return jobs
