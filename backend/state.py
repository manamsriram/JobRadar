"""JSON-file state store on /data — the only persistent storage (user rule).

Everything the poller needs to survive a restart lives as plain JSON under
DATA_DIR: seen jobs, the funding queue, and seen-funding ids. No database.

All writes are atomic (write .tmp then os.replace) so a crash mid-write can
never corrupt the single source of truth (senior-review fix #5).
"""
import json
import os
import pathlib
from datetime import datetime, timedelta, timezone

# /data is the mounted persistent volume in Docker. Override with DATA_DIR for
# local dev (e.g. DATA_DIR=./data uvicorn main:app).
DATA_DIR = pathlib.Path(os.getenv("DATA_DIR", "/data"))
SEEN_JOBS_FILE = DATA_DIR / "seen_jobs.json"
COMPANIES_FILE = DATA_DIR / "companies.json"
FUNDING_QUEUE_FILE = DATA_DIR / "funding_queue.json"
SEEN_FUNDING_FILE = DATA_DIR / "seen_funding.json"


def _read_json(path: pathlib.Path, default):
    """Missing file -> default (genuine first run). Corrupt/unreadable file ->
    raise, so callers don't silently treat a broken state store as empty."""
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return default


def _write_json_atomic(path: pathlib.Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    os.replace(tmp, path)  # atomic on POSIX


# ---- Seen jobs ----
def load_seen() -> dict:
    """All persisted jobs keyed by id."""
    return _read_json(SEEN_JOBS_FILE, {})


def save_seen(seen: dict) -> None:
    _write_json_atomic(SEEN_JOBS_FILE, seen)


def get_new_jobs(seen: dict, fetched: list[dict]) -> list[dict]:
    """Jobs from `fetched` whose id is not already in `seen`."""
    return [j for j in fetched if j.get("id") and j["id"] not in seen]


def purge_old(seen: dict, days: int) -> dict:
    """Drop jobs first scraped more than `days` calendar days ago. Applied jobs
    are always kept. Returns the pruned dict (caller persists it)."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    kept = {}
    for jid, job in seen.items():
        if job.get("applied"):
            kept[jid] = job
            continue
        ts = job.get("scraped_at")
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00")) if ts else None
        except (ValueError, AttributeError):
            dt = None
        if dt is None or dt >= cutoff:
            kept[jid] = job
    return kept


def mark_applied(job_id: str) -> bool:
    """Flag a job applied=true so purge_old preserves it. Returns True if found."""
    seen = load_seen()
    job = seen.get(job_id)
    if not job:
        return False
    job["applied"] = True
    save_seen(seen)
    return True


def get_matched(seen: dict) -> list[dict]:
    """Matched jobs, newest first — feeds GET /api/jobs."""
    matched = [j for j in seen.values() if j.get("matched")]
    matched.sort(key=lambda j: j.get("posted_at") or j.get("scraped_at") or "", reverse=True)
    return matched


# ---- Companies (seed lives in repo data/, mounted to /data) ----
def load_companies() -> list[dict]:
    return _read_json(COMPANIES_FILE, [])


# ---- Funding signal queue ----
def load_funding_queue() -> list[dict]:
    return _read_json(FUNDING_QUEUE_FILE, [])


def save_funding_queue(queue: list[dict]) -> None:
    _write_json_atomic(FUNDING_QUEUE_FILE, queue)


def load_seen_funding() -> set:
    return set(_read_json(SEEN_FUNDING_FILE, []))


def save_seen_funding(ids: set) -> None:
    _write_json_atomic(SEEN_FUNDING_FILE, sorted(ids))
