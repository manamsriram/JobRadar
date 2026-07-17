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
    # US citizenship / clearance requirements — dropped unless a sponsorship
    # signal is also present (some postings list both boilerplate clearance
    # language and "we sponsor visas").
    "citizenship_exclude": [
        "us citizenship", "u.s. citizenship", "us citizen", "u.s. citizen",
        "must be a citizen", "citizenship required", "security clearance",
        "no sponsorship", "not provide sponsorship", "unable to sponsor",
        "does not sponsor", "will not sponsor", "cannot sponsor",
    ],
    "sponsorship_signals": [
        "visa sponsorship", "will sponsor", "we sponsor", "sponsor visas",
        "sponsorship available", "able to sponsor", "provide sponsorship",
    ],
}

# Any "N years"/"N+ years"/"N-M years" mention (title or description) above
# this cap rejects the job — regex-based, catches ranges like "2-5 years"
# that plain substring lists miss.
MAX_YEARS_EXPERIENCE = int(os.getenv("MAX_YEARS_EXPERIENCE", "2"))

# ---- Tuning (env-overridable) ----
POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL_SECONDS", "300"))
# TechCrunch funding RSS is polled hourly — new funding rarely lands faster.
FUNDING_CHECK_INTERVAL = int(os.getenv("FUNDING_CHECK_INTERVAL", "3600"))
# Email digest cadence — batches matches instead of one email per job.
ALERT_INTERVAL_SECONDS = int(os.getenv("ALERT_INTERVAL_SECONDS", str(4 * 3600)))
ALERT_DIGEST_SIZE = int(os.getenv("ALERT_DIGEST_SIZE", "5"))
PURGE_AFTER_DAYS = int(os.getenv("PURGE_AFTER_DAYS", "3"))
# Total fetch retries allowed across one poll cycle (finding #2 guardrail) —
# caps retry-storm blowup when many sources fail at once (e.g. a network blip)
# instead of letting every source retry independently and stack past the next
# poll interval.
CYCLE_RETRY_BUDGET = int(os.getenv("CYCLE_RETRY_BUDGET", "20"))
# Only surface jobs posted within this many days (calendar-day granularity).
MAX_POSTED_AGE_DAYS = int(os.getenv("MAX_POSTED_AGE_DAYS", "7"))

# Degree requirements that, combined with any years-of-experience mention,
# push the effective bar above what a 0-2 YOE candidate can meet (e.g. "MS +
# 2 years") even though the number alone would pass MAX_YEARS_EXPERIENCE.
DEGREE_PLUS_EXPERIENCE_EXCLUDE = [
    "master's degree", "masters degree", "master's in", "masters in",
    "ms degree", "m.s. degree", "phd", "ph.d.", "doctorate",
]
