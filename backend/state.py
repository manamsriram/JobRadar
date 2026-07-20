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
VISA_SPONSOR_SEEDS_FILE = DATA_DIR / "visa_sponsor_seeds.json"
DISCOVERY_COUNTS_FILE = DATA_DIR / "discovery_counts.json"
LINK_PATTERNS_FILE = DATA_DIR / "link_patterns.json"
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
    """Drop jobs first scraped more than `days` calendar days ago. Only an
    applied job is kept indefinitely — the user curates that list by hand via
    DELETE /api/jobs/{id}. Everything else (matched-but-unapplied included)
    ages out after `days`; unmatched jobs never make it into `seen` in the
    first place (dropped at ingest/scrape time), so in practice this only
    prunes matched-unapplied jobs. Returns the pruned dict (caller persists it)."""
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
    """Matched, not-yet-applied jobs, newest first — feeds GET /api/jobs (the
    active feed, subject to purge_old). Applied jobs move to get_applied()."""
    matched = [j for j in seen.values() if j.get("matched") and not j.get("applied")]
    matched.sort(key=lambda j: j.get("posted_at") or j.get("scraped_at") or "", reverse=True)
    return matched


def get_applied(seen: dict) -> list[dict]:
    """Applied jobs, newest first — feeds GET /api/jobs/applied. Exempt from
    purge_old; the user removes entries by hand via delete_job()."""
    applied = [j for j in seen.values() if j.get("applied")]
    applied.sort(key=lambda j: j.get("posted_at") or j.get("scraped_at") or "", reverse=True)
    return applied


def delete_job(seen: dict, job_id: str) -> bool:
    """Remove a job (typically an applied one) from the persisted store.
    Returns True if it existed. Caller persists via save_seen()."""
    return seen.pop(job_id, None) is not None


# ---- Companies (seed lives in repo data/, mounted to /data) ----
def load_companies() -> list[dict]:
    return _read_json(COMPANIES_FILE, [])


def save_companies(companies: list[dict]) -> None:
    """Backed up like seen_jobs.json: this file is now written from
    regex/headline-derived data (funding-signal promotion), so a bad write
    should be one revert away, not a manual re-type of the curated rows."""
    _write_json_atomic_with_backup(COMPANIES_FILE, companies)


# ---- Visa-sponsor seed list (hand-edited, same schema as companies.json) ----
def load_visa_sponsor_seeds() -> list[dict]:
    return _read_json(VISA_SPONSOR_SEEDS_FILE, [])


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


# ---- Auto-discovery YC-frequency counts (signals/company_discovery.py) ----
# name (normalized) -> number of poll cycles seen in. Persisted so the
# 2-cycle threshold survives a restart instead of resetting to 0.
def load_discovery_counts() -> dict:
    return _read_json(DISCOVERY_COUNTS_FILE, {})


def save_discovery_counts(counts: dict) -> None:
    _write_json_atomic(DISCOVERY_COUNTS_FILE, counts)


# ---- Adaptive selector fallback (adaptive.py) ----
# company -> learned job-link path prefix (e.g. "/jobs"), refreshed on every
# scrape that finds jobs via the primary keyword selector. Consulted only
# when the primary selector returns zero anchors (candidate markup drift).
def load_link_patterns() -> dict:
    return _read_json(LINK_PATTERNS_FILE, {})


def save_link_patterns(patterns: dict) -> None:
    _write_json_atomic(LINK_PATTERNS_FILE, patterns)


# ---- Source health (finding #1) ----
def load_health() -> dict:
    return _read_json(SOURCE_HEALTH_FILE, {})


def save_health(health: dict) -> None:
    _write_json_atomic(SOURCE_HEALTH_FILE, health)


def record_health(health: dict, source: str, ok: bool,
                  job_count: int | None = None) -> dict:
    """Update `source`'s consecutive-failure streak in place. `ok` must reflect
    fetch-level success (no exception, non-5xx) — never "0 jobs matched",
    since a legitimate zero-results cycle isn't a scraper break (senior-pass
    caveat: keying off match count would cry wolf on normal filter-starvation).

    When `job_count` is provided, a `consecutive_zero_jobs` counter is also
    tracked and reset to 0 whenever `job_count > 0`. The caller uses this to
    skip sources that have returned nothing for `MAX_CONSECUTIVE_ZERO_JOBS`
    cycles — avoids wasting retry budget on pages with no entry-level openings."""
    entry = health.get(source, {"consecutive_failures": 0, "consecutive_zero_jobs": 0})
    entry["consecutive_failures"] = 0 if ok else entry.get("consecutive_failures", 0) + 1
    entry["status"] = "ok" if ok else "failing"
    entry["last_checked"] = datetime.now(timezone.utc).isoformat()
    if job_count is not None:
        entry["consecutive_zero_jobs"] = 0 if job_count > 0 else entry.get("consecutive_zero_jobs", 0) + 1
        entry["last_job_count"] = job_count
    health[source] = entry
    return health


def should_skip_source(health: dict, source: str, max_zero_cycles: int) -> bool:
    """True if this source has returned 0 jobs for `max_zero_cycles` or more
    consecutive scrape cycles. The source will be temporarily skipped until
    a cycle resets its counter (manual or across the whole batch)."""
    entry = health.get(source, {})
    return entry.get("consecutive_zero_jobs", 0) >= max_zero_cycles
