"""Adaptive selector fallback for _fetch_plain (scraper.py).

The hardcoded keyword selector (a[href*='/job'], .../career, .../position)
breaks silently whenever a career page's markup drifts to a term outside
that fixed list (e.g. "/roles/", "/openings/") — 0 jobs, no error, easy to
miss. This learns each company's job-link path prefix from every scrape
that *does* find jobs via the primary selector, so a later zero-anchor
cycle gets a second pass against the last-known-good pattern instead of
just giving up.

ponytail: path-prefix matching, not real DOM/structural similarity. Upgrade
to fingerprinting only if prefix drift (not just keyword drift) shows up as
a real failure mode.
"""
from urllib.parse import urlparse


def _path_prefix(href: str) -> str | None:
    """First non-empty path segment, e.g. "/jobs/backend-eng" -> "/jobs"."""
    path = urlparse(href).path
    parts = [p for p in path.split("/") if p]
    return f"/{parts[0]}" if parts else None


def learn_prefix(hrefs: list[str]) -> str | None:
    """Majority path prefix across a successful scrape's job links, or None
    if there's no clear pattern (e.g. every job lives under a distinct
    top-level path) worth remembering."""
    prefixes = [p for h in hrefs if (p := _path_prefix(h))]
    if not prefixes:
        return None
    counts: dict[str, int] = {}
    for p in prefixes:
        counts[p] = counts.get(p, 0) + 1
    best, count = max(counts.items(), key=lambda kv: kv[1])
    return best if count >= max(2, len(prefixes) // 2) else None


def fallback_hrefs(all_hrefs: list[str], prefix: str) -> list[str]:
    """hrefs matching a previously learned prefix — tried only when the
    primary keyword selector finds nothing (candidate markup drift)."""
    return [h for h in all_hrefs if _path_prefix(h) == prefix]
