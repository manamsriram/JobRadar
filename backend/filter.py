from config import ROLE_FILTERS


def matches(job: dict) -> bool:
    title = job.get("title", "").lower()
    location = job.get("location", "").lower()

    # Must match at least one title keyword
    if not any(kw in title for kw in ROLE_FILTERS["titles"]):
        return False

    # Must not match any exclusion keyword
    if any(kw in title for kw in ROLE_FILTERS["exclude"]):
        return False

    # Must match at least one location (if any locations are configured)
    if ROLE_FILTERS["locations"]:
        if not any(loc in location for loc in ROLE_FILTERS["locations"]):
            return False

    return True
