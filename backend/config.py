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
        "senior", "staff", "principal", "lead", "manager", "director",
        "vp ", "head of", "10+ years", "8+ years", "7+ years",
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
DIGEST_INTERVAL_HOURS = float(os.getenv("DIGEST_INTERVAL_HOURS", "4"))
DIGEST_MAX_JOBS = int(os.getenv("DIGEST_MAX_JOBS", "5"))

# ---- Companies ----
COMPANIES_FILE = pathlib.Path(__file__).parent / "companies.json"


def load_companies() -> list[dict]:
    return json.loads(COMPANIES_FILE.read_text())


COMPANIES = load_companies()
