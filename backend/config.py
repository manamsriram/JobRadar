import os

# ---- Role filters ----
ROLE_FILTERS = {
    "titles": [
        "software engineer", "backend engineer", "full stack engineer",
        "full-stack engineer", "swe", "software developer",
        "frontend engineer", "fullstack engineer",
    ],
    "exclude": [
        "senior", "sr ", "sr.", "staff", "principal", "lead", "manager",
        "director", "vp ", "head of", "experienced", "expert",
        # New grad / 0 experience: drop anything signalling required years or
        # a non-entry level in the title (title-only — descriptions aren't fetched).
        "2+ years", "3+ years", "4+ years", "5+ years", "6+ years",
        "7+ years", "8+ years", "10+ years", "years of experience",
        " ii", " iii", " iv",
    ],
    # Positive US signals. "remote"/"hybrid" are NOT here — alone they matched
    # "India - Remote", "Seoul (Hybrid)", etc. Remote/hybrid is handled separately
    # in filter.py: allowed only with a US signal or no non-US marker.
    "locations": [
        "san jose", "san francisco", "sf", "bay area", "mountain view",
        "sunnyvale", "palo alto", "menlo park", "milpitas", "santa clara",
        "seattle", "new york", "nyc", "chicago", "austin", "boston",
        "los angeles", "denver", "atlanta", "washington, dc", "washington dc",
        # "us" alone matched Austin/Houston/etc — use anchored forms instead
        "(us", ", us", "us/", "us-", "united states", "usa", "u.s.",
    ],
    # Non-US markers — reject a job if its location contains any, even when it
    # also says "remote"/"hybrid" (e.g. "Remote - Brussels", "Sydney (Hybrid)").
    "non_us": [
        ", uk", "united kingdom", "london", "ireland", "dublin",
        "japan", "tokyo", "korea", "seoul", "singapore",
        "india", "bengaluru", "bangalore", "delhi",
        "canada", "toronto", "can-remote", "mexico",
        "australia", "sydney", "melbourne",
        "germany", "munich", "france", "paris",
        "sweden", "stockholm", "spain", "madrid", "barcelona",
        "brazil", "são paulo", "sao paulo", "uae", "abu dhabi",
        "luxembourg", "switzerland", "zurich", "brussels", "belgium",
        "netherlands", "amsterdam",
    ],
    # Body-level exclusions: strong senior-experience signals that slip past a
    # clean title. Kept to 5+ years only — lower thresholds ("2+ years preferred")
    # show up in entry-level posts too and would wrongly drop new-grad roles.
    "desc_exclude": [
        "5+ years", "6+ years", "7+ years", "8+ years", "9+ years",
        "10+ years", "5-7 years", "5-8 years", "7-10 years",
    ],
}

# ---- Tuning (env-overridable) ----
POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL_SECONDS", "300"))
# TechCrunch funding RSS is polled hourly — new funding rarely lands faster.
FUNDING_CHECK_INTERVAL = int(os.getenv("FUNDING_CHECK_INTERVAL", "3600"))
PURGE_AFTER_DAYS = int(os.getenv("PURGE_AFTER_DAYS", "3"))
# Only surface jobs posted within this many days (calendar-day granularity).
# Niche boards post fresh roles; 14 balances freshness vs. volume.
MAX_POSTED_AGE_DAYS = int(os.getenv("MAX_POSTED_AGE_DAYS", "14"))
