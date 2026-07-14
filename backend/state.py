"""Job state store backed by Supabase Postgres via its PostgREST REST API.

Uses httpx (no Supabase SDK). The service key is server-side only and never
reaches the browser — the React app talks to FastAPI, never Supabase directly.
"""
import os
from datetime import datetime, timedelta, timezone

import httpx

TABLE = "jobs"
TIMEOUT = 10


def _conf() -> tuple[str, dict]:
    """Return (base_url, headers). Raise a clear error if unconfigured (F5)."""
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_KEY")
    if not url or not key:
        raise RuntimeError(
            "SUPABASE_URL and SUPABASE_SERVICE_KEY must be set to use the job store."
        )
    base = f"{url.rstrip('/')}/rest/v1/{TABLE}"
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    return base, headers


async def load_seen() -> dict:
    """All rows keyed by id. Dataset is tiny (<=3000 rows), so fetching all is fine."""
    base, headers = _conf()
    async with httpx.AsyncClient() as client:
        r = await client.get(f"{base}?select=*", headers=headers, timeout=TIMEOUT)
        r.raise_for_status()
        return {row["id"]: row for row in r.json()}


async def upsert_job(job: dict) -> None:
    """Insert or merge one job by primary key (F3: persist per job)."""
    base, headers = _conf()
    headers = {**headers, "Prefer": "resolution=merge-duplicates,return=minimal"}
    async with httpx.AsyncClient() as client:
        r = await client.post(base, headers=headers, json=job, timeout=TIMEOUT)
        r.raise_for_status()


async def purge_old(days: int) -> None:
    """Delete un-applied jobs posted before the calendar-day cutoff (safety net;
    the Supabase pg_cron is the primary purger). Skipped gracefully by the caller
    if the `applied` column isn't present yet."""
    cutoff = (datetime.now(timezone.utc).date() - timedelta(days=days)).isoformat()
    base, headers = _conf()
    headers = {**headers, "Prefer": "return=minimal"}
    async with httpx.AsyncClient() as client:
        r = await client.delete(
            base,
            params={"posted_at": f"lt.{cutoff}", "applied": "eq.false"},
            headers=headers,
            timeout=TIMEOUT,
        )
        r.raise_for_status()


async def mark_applied(job_id: str) -> None:
    """Flag a job applied=true so the purge/cron preserves it."""
    base, headers = _conf()
    headers = {**headers, "Prefer": "return=minimal"}
    async with httpx.AsyncClient() as client:
        r = await client.patch(
            base,
            params={"id": f"eq.{job_id}"},
            headers=headers,
            json={"applied": True},
            timeout=TIMEOUT,
        )
        r.raise_for_status()


async def is_empty() -> bool:
    """True if the table has no rows (drives the F2 silent-seed decision)."""
    base, headers = _conf()
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{base}?select=id&limit=1", headers=headers, timeout=TIMEOUT
        )
        r.raise_for_status()
        return len(r.json()) == 0


async def get_matched(limit: int | None = None) -> list[dict]:
    """Matched jobs, newest first — feeds GET /api/jobs."""
    base, headers = _conf()
    q = f"{base}?matched=eq.true&order=posted_at.desc"
    if limit:
        q += f"&limit={limit}"
    async with httpx.AsyncClient() as client:
        r = await client.get(q, headers=headers, timeout=TIMEOUT)
        r.raise_for_status()
        return r.json()


async def get_matched_unnotified(limit: int) -> list[dict]:
    """Matched jobs not yet emailed, newest first — feeds the digest (F3)."""
    base, headers = _conf()
    q = f"{base}?matched=eq.true&notified=eq.false&order=posted_at.desc&limit={limit}"
    async with httpx.AsyncClient() as client:
        r = await client.get(q, headers=headers, timeout=TIMEOUT)
        r.raise_for_status()
        return r.json()


async def mark_notified(ids: list[str]) -> None:
    """Flip notified=true for the given job ids."""
    if not ids:
        return
    base, headers = _conf()
    headers = {**headers, "Prefer": "return=minimal"}
    id_list = ",".join(ids)
    async with httpx.AsyncClient() as client:
        r = await client.patch(
            f"{base}?id=in.({id_list})",
            headers=headers,
            json={"notified": True},
            timeout=TIMEOUT,
        )
        r.raise_for_status()


def get_new_jobs(seen: dict, fetched: list[dict]) -> list[dict]:
    """Jobs from `fetched` not already present in `seen`."""
    return [j for j in fetched if j["id"] not in seen]
