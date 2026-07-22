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
# trailing year-as-anchor on. The trailing alternation `years?|yrs?` accepts
# both the long form ("5 years", "5 year") and the abbreviation ("5 yrs",
# "5 yr") — JDs use them interchangeably; the suffix is a layout choice, not
# a years-vs-no-years signal. Both branches are pure literals so the
# alternation can't backtrack.
#
# Parentheses are stripped upstream (in `_max_years_required` and
# `_hard_cap_requirement`) so no regex gap needs to tolerate `(` or `)`.
# The `[\s)]{0,3}` in the tail gaps is harmless but vestigial — the `)`
# branch never fires because parens are already removed from the text.
# Kept as-is for backward compatibility with any external caller that
# reads `_YEARS_RE` directly.
_YEARS_RE = re.compile(
    r"(\d+)[\s)]{0,3}\+?[\s)]{0,3}(?:-|to)?[\s)]{0,3}(\d+)?[\s)]{0,3}\+?[\s)]{0,3}(?:years?|yrs?)"
)

# English number words 0-99 — anything above the years cap (default 2)
# is a reject anyway, but the dict still needs to MAP a spelled-out token
# to a digit so the existing _YEARS_RE can fire. Both hyphenated
# ("twenty-two") and spaced ("twenty two") forms are listed because JDs
# use them interchangeably.
_ONES = [(0, "zero"), (1, "one"), (2, "two"), (3, "three"), (4, "four"),
         (5, "five"), (6, "six"), (7, "seven"), (8, "eight"), (9, "nine"),
         (10, "ten"), (11, "eleven"), (12, "twelve"), (13, "thirteen"),
         (14, "fourteen"), (15, "fifteen"), (16, "sixteen"),
         (17, "seventeen"), (18, "eighteen"), (19, "nineteen")]
_TENS = [(20, "twenty"), (30, "thirty"), (40, "forty"), (50, "fifty"),
         (60, "sixty"), (70, "seventy"), (80, "eighty"), (90, "ninety")]
_UNITS = [(1, "one"), (2, "two"), (3, "three"), (4, "four"), (5, "five"),
          (6, "six"), (7, "seven"), (8, "eight"), (9, "nine")]


def _build_num_words() -> dict[str, int]:
    """Single source of truth for the spelled-out vocabulary. Adding a new
    number is one line in _ONES/_TENS/_UNITS — avoid hand-rolling 172
    dict entries that drift out of sync."""
    out: dict[str, int] = {w: n for n, w in _ONES}
    out.update({w: n for n, w in _TENS})
    # Tens+units compounds: "twenty-one"/"twenty one" through
    # "ninety-nine"/"ninety nine". Hyphenated + space variants both
    # included so JDs that drop the hyphen are still matched.
    for tens_n, tens_w in _TENS:
        for units_n, units_w in _UNITS:
            out[f"{tens_w}-{units_w}"] = tens_n + units_n
            out[f"{tens_w} {units_w}"] = tens_n + units_n
    return out


_NUM_WORDS = _build_num_words()
# Sort longest-first so hyphenated compounds ("twenty-two") match before
# their prefix token ("twenty"). Ties broken alphabetically for
# determinism independent of dict insertion order.
_NUM_WORDS_PATTERN = "|".join(
    sorted(_NUM_WORDS, key=lambda k: (-len(k), k))
)
# All-alternation-of-literals — no backtracking risk.
_NUM_WORDS_RE = re.compile(
    rf"\b({_NUM_WORDS_PATTERN})\b", re.IGNORECASE
)


def _spelled_to_digit(text: str) -> str:
    """Replace English number words with their digit form so the years
    regex catches `"two years experience"` / `"minimum two years"` /
    `"Twenty Years required"` the same way it catches the digit form.
    Compound forms (`"twenty-two"`) match before their prefix token
    (`"twenty"`) because of the longest-first alternation order."""
    return _NUM_WORDS_RE.sub(
        lambda m: str(_NUM_WORDS[m.group(1).lower()]), text
    )


def _max_years_required(text: str) -> int:
    """Highest year-count mentioned in text (0 if none found). Spelled-out
    numbers (`"two years"`) are normalized to digits before matching so
    they share the same path as `"2 years"`.

    Parentheses are stripped before the regex runs so JDs that wrap either
    the entire phrase or just the digit in parens ("(2) years",
    "(0) to (2) years") are handled uniformly without widening the regex
    gaps to tolerate `(` or `)` inline."""
    years = []
    text = text.replace("(", " ").replace(")", " ")
    for m in _YEARS_RE.finditer(_spelled_to_digit(text)):
        years.append(int(m.group(1)))
        if m.group(2):
            years.append(int(m.group(2)))
    return max(years, default=0)


def _hard_cap_requirement(text: str) -> bool:
    """True if text states a bare (non-range) requirement of exactly
    MAX_YEARS_EXPERIENCE years — e.g. "2 years", "2+ years". A range whose
    low end is under the cap ("0-2 years", "1-2 years") is still fine; only
    a flat minimum sitting right at the cap should be rejected.

    Parentheses stripped before the regex runs (same rationale as
    `_max_years_required`)."""
    text = text.replace("(", " ").replace(")", " ")
    for m in _YEARS_RE.finditer(_spelled_to_digit(text)):
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
    title = (job.get("title") or "").lower()
    location = (job.get("location") or "").lower()

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
    description = (job.get("description") or "").lower()
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
