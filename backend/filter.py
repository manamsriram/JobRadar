from datetime import datetime, timedelta, timezone

from config import MAX_POSTED_AGE_DAYS, ROLE_FILTERS


def _too_old(posted_at: str | None) -> bool:
    """True if posted_at is older than MAX_POSTED_AGE_DAYS (calendar days, UTC).
    Missing/unparseable dates count as fresh (custom pages fall back to now)."""
    if not posted_at:
        return False
    try:
        dt = datetime.fromisoformat(posted_at.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return False
    cutoff = datetime.now(timezone.utc).date() - timedelta(days=MAX_POSTED_AGE_DAYS)
    return dt.date() < cutoff


def matches(job: dict) -> bool:
    title = job.get("title", "").lower()
    location = job.get("location", "").lower()

    # Must have been posted within the recency window
    if _too_old(job.get("posted_at")):
        return False

    # Must match at least one title keyword
    if not any(kw in title for kw in ROLE_FILTERS["titles"]):
        return False

    # Must not match any exclusion keyword (seniority / experience / level)
    if any(kw in title for kw in ROLE_FILTERS["exclude"]):
        return False

    # Must match at least one location (if any locations are configured)
    if ROLE_FILTERS["locations"]:
        if not any(loc in location for loc in ROLE_FILTERS["locations"]):
            return False

    # Body-level exclusion: drop senior roles whose title looked entry-level
    description = job.get("description", "").lower()
    if any(kw in description for kw in ROLE_FILTERS["desc_exclude"]):
        return False

    return True
