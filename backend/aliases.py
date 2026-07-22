"""Company alias canonicalization (finding #6). Resolves a scraped company
name to the curated name in companies.json before it's used as a lookup key.
Fail-open: an unmapped name passes through unchanged rather than being
dropped or guessed — a missing alias should degrade to "pre-#6 behavior",
never silently discard a real job."""


def canonicalize_company(name: str, aliases: dict[str, str]) -> str:
    if not name:
        return name or ""
    return aliases.get(name.strip().lower(), name)
