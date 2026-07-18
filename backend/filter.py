import re
from datetime import datetime, timedelta, timezone

from config import (
    DEGREE_PLUS_EXPERIENCE_EXCLUDE,
    MAX_POSTED_AGE_DAYS,
    MAX_YEARS_EXPERIENCE,
    ROLE_FILTERS,
)

# Whitespace runs are bounded (\s{0,3}) rather than \s* so the matcher can't
# be driven into polynomial backtracking by long runs of whitespace with no
# trailing "year(s)" to anchor on.
_YEARS_RE = re.compile(r"(\d+)\s{0,3}\+?\s{0,3}(?:-|to)?\s{0,3}(\d+)?\s{0,3}\+?\s{0,3}years?")


def _max_years_required(text: str) -> int:
    """Highest year-count mentioned in text (0 if none found)."""
    years = []
    for m in _YEARS_RE.finditer(text):
        years.append(int(m.group(1)))
        if m.group(2):
            years.append(int(m.group(2)))
    return max(years, default=0)


def _hard_cap_requirement(text: str) -> bool:
    """True if text states a bare (non-range) requirement of exactly
    MAX_YEARS_EXPERIENCE years — e.g. "2 years", "2+ years". A range whose
    low end is under the cap ("0-2 years", "1-2 years") is still fine; only
    a flat minimum sitting right at the cap should be rejected."""
    for m in _YEARS_RE.finditer(text):
        if m.group(2) is None and int(m.group(1)) >= MAX_YEARS_EXPERIENCE:
            return True
    return False


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

    # Must not match any exclusion keyword (seniority / level)
    if any(kw in title for kw in ROLE_FILTERS["exclude"]):
        return False

    # No years-of-experience number in the title may exceed the cap
    # (catches "2-5 years", "3+ years", "5 years", etc.)
    if _max_years_required(title) > MAX_YEARS_EXPERIENCE:
        return False

    # Reject a bare "2 years"/"2+ years" minimum in the title — only a range
    # starting below the cap ("0-2 years") counts as entry-level.
    if _hard_cap_requirement(title):
        return False

    # Location: US only. A positive US signal wins (US multi-city roles often
    # also list Toronto/Dublin). Otherwise plain remote/hybrid passes only when
    # no non-US marker is present ("Remote - Brussels", "Seoul (Hybrid)" fail).
    # Blank location (custom career pages with no locatable DOM element) is not
    # a non-US signal — trust the curated (US) company list rather than reject.
    has_us = (
        not location.strip()
        or location.strip() in ("us", "u.s", "u.s.")
        or any(loc in location for loc in ROLE_FILTERS["locations"])
    )
    if not has_us:
        if "remote" in location or "hybrid" in location:
            if any(m in location for m in ROLE_FILTERS["non_us"]):
                return False
        else:
            return False

    # Body-level exclusion: drop roles whose description reveals a
    # years-of-experience requirement above the cap (title looked entry-level)
    description = job.get("description", "").lower()
    if _max_years_required(description) > MAX_YEARS_EXPERIENCE:
        return False

    if _hard_cap_requirement(description):
        return False

    # Drop roles pairing a graduate-degree requirement with any years-of-
    # experience mention (e.g. "Master's degree + 2 years") — the combined
    # bar exceeds what a 0-2 YOE candidate can meet even though "2 years"
    # alone would pass.
    text = f"{title} {description}"
    if any(kw in text for kw in DEGREE_PLUS_EXPERIENCE_EXCLUDE) and _max_years_required(text) > 0:
        return False

    if any(kw in text for kw in ROLE_FILTERS["citizenship_exclude"]):
        if not any(kw in text for kw in ROLE_FILTERS["sponsorship_signals"]):
            return False

    return True
