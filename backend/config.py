import json
import os
import pathlib

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
    "locations": [
        "san jose", "san francisco", "bay area", "mountain view",
        "sunnyvale", "palo alto", "menlo park", "remote", "hybrid",
        # "us" alone matched Austin/Houston/etc — use anchored forms instead
        "(us", ", us", "us/", "united states", "usa", "u.s.",
    ],
}

# ---- Tuning (env-overridable) ----
POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL_SECONDS", "300"))
PURGE_AFTER_DAYS = int(os.getenv("PURGE_AFTER_DAYS", "3"))
# Only surface jobs posted within this many days (calendar-day granularity):
# today=13 → keep 13,12,11,10.
MAX_POSTED_AGE_DAYS = int(os.getenv("MAX_POSTED_AGE_DAYS", "3"))
DIGEST_INTERVAL_HOURS = float(os.getenv("DIGEST_INTERVAL_HOURS", "4"))
DIGEST_MAX_JOBS = int(os.getenv("DIGEST_MAX_JOBS", "5"))

# ---- Companies ----
COMPANIES_FILE = pathlib.Path(__file__).parent / "companies.json"


def load_companies() -> list[dict]:
    return json.loads(COMPANIES_FILE.read_text())


COMPANIES = load_companies()
