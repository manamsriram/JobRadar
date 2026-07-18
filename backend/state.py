"""JSON-file state store on /data — the only persistent storage (user rule).

Everything the poller needs to survive a restart lives as plain JSON under
DATA_DIR: seen jobs, the funding queue, and seen-funding ids. No database.

All writes are atomic (write .tmp then os.replace) so a crash mid-write can
never corrupt the single source of truth (senior-review fix #5). seen_jobs.json
additionally keeps a bounded, rotating backup snapshot (finding #5) — it's the
one file where a bad write (corrupt scrape, botched merge) would lose real
data. Backups are capped at BACKUP_KEEP and pruned on every write, so this
never turns into unbounded disk growth on the 1GB VM.
"""
import json
import os
import pathlib
import shutil
from datetime import datetime, timedelta, timezone

# /data is the mounted persistent volume in Docker. Override with DATA_DIR for
# local dev (e.g. DATA_DIR=./data uvicorn main:app).
DATA_DIR = pathlib.Path(os.getenv("DATA_DIR", "/data"))
SEEN_JOBS_FILE = DATA_DIR / "seen_jobs.json"
COMPANIES_FILE = DATA_DIR / "companies.json"
FUNDING_QUEUE_FILE = DATA_DIR / "funding_queue.json"
SEEN_FUNDING_FILE = DATA_DIR / "seen_funding.json"
SOURCE_HEALTH_FILE = DATA_DIR / "source_health.json"
COMPANY_ALIASES_FILE = DATA_DIR / "company_aliases.json"
BACKUP_KEEP = 3


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


def _prune_backups(path: pathlib.Path, keep: int) -> None:
    backups = sorted(path.parent.glob(f"{path.name}.*.bak"))
    for old in backups[:-keep] if keep > 0 else backups:
        old.unlink(missing_ok=True)


def _write_json_atomic_with_backup(path: pathlib.Path, data, keep: int = BACKUP_KEEP) -> None:
    """Snapshot the current file before overwriting, then prune to the last
    `keep` snapshots. Bounded by construction — never accumulates."""
    if path.exists():
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        shutil.copy2(path, path.with_name(f"{path.name}.{stamp}.bak"))
        _prune_backups(path, keep)
    _write_json_atomic(path, data)


# ---- Seen jobs ----
def load_seen() -> dict:
    """All persisted jobs keyed by id."""
    return _read_json(SEEN_JOBS_FILE, {})


def save_seen(seen: dict) -> None:
    _write_json_atomic_with_backup(SEEN_JOBS_FILE, seen)


def get_new_jobs(seen: dict, fetched: list[dict]) -> list[dict]:
    """Jobs from `fetched` whose id is not already in `seen`."""
    return [j for j in fetched if j.get("id") and j["id"] not in seen]


def purge_old(seen: dict, days: int) -> dict:
    """Drop jobs first scraped more than `days` calendar days ago. Applied and
    matched jobs are always kept — purging a still-live matched job (YC
    postings stay listed for weeks, well past PURGE_AFTER_DAYS) made
    get_new_jobs() treat it as brand new on the next cycle and re-alert on
    the same posting over and over. Returns the pruned dict (caller persists it)."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    kept = {}
    for jid, job in seen.items():
        if job.get("applied") or job.get("matched"):
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


def save_companies(companies: list[dict]) -> None:
    """Backed up like seen_jobs.json: this file is now written from
    regex/headline-derived data (funding-signal promotion), so a bad write
    should be one revert away, not a manual re-type of the curated rows."""
    _write_json_atomic_with_backup(COMPANIES_FILE, companies)


# ---- Company alias canonicalization (finding #6) ----
# ATS org-name vs. curated brand-name drift (Greenhouse/Lever/Ashby board slugs,
# YC's href-derived slug) breaks anything keyed by company name — health
# tracking, contact enrichment, the trust domain check. Static map, hand-verify
# each entry; alias -> canonical name in companies.json.
def load_company_aliases() -> dict:
    return _read_json(COMPANY_ALIASES_FILE, {})


# ---- Funding signal queue ----
def load_funding_queue() -> list[dict]:
    return _read_json(FUNDING_QUEUE_FILE, [])


def save_funding_queue(queue: list[dict]) -> None:
    _write_json_atomic(FUNDING_QUEUE_FILE, queue)


def load_seen_funding() -> set:
    return set(_read_json(SEEN_FUNDING_FILE, []))


def save_seen_funding(ids: set) -> None:
    _write_json_atomic(SEEN_FUNDING_FILE, sorted(ids))


# ---- Source health (finding #1) ----
def load_health() -> dict:
    return _read_json(SOURCE_HEALTH_FILE, {})


def save_health(health: dict) -> None:
    _write_json_atomic(SOURCE_HEALTH_FILE, health)


def record_health(health: dict, source: str, ok: bool) -> dict:
    """Update `source`'s consecutive-failure streak in place. `ok` must reflect
    fetch-level success (no exception, non-5xx) — never "0 jobs matched",
    since a legitimate zero-results cycle isn't a scraper break (senior-pass
    caveat: keying off match count would cry wolf on normal filter-starvation)."""
    entry = health.get(source, {"consecutive_failures": 0})
    entry["consecutive_failures"] = 0 if ok else entry.get("consecutive_failures", 0) + 1
    entry["status"] = "ok" if ok else "failing"
    entry["last_checked"] = datetime.now(timezone.utc).isoformat()
    health[source] = entry
    return health
