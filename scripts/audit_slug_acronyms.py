#!/usr/bin/env python3
"""scripts/audit_slug_acronyms.py — data-driven acronym-allowlist audit.

Fetches ~120 real job boards across 5 ATS platforms (Greenhouse, Lever,
Ashby, SmartRecruiters, Workday), classifies the slug tokens against the
current `text_utils._ACRONYMS_UPPER` / `_ACRONYM_TITLED` allowlist, and
surfaces:
  1. Tokens already covered (coverage proof).
  2. Tokens NOT covered that look like acronyms — ranked by frequency.
     Auto-promote threshold: ≥10 occurrences AND length ≤4 AND 0 vowels.
  3. Borderline candidates for human review (≥3 occurrences but
     vowel-bearing or longer).

Workday is JS-rendered; the audit uses its JSON API at
  POST .../wday/cxs/{company}/{tenant}/jobs
instead of HTML parsing. All other platforms use standard GET + BS4.

Output is intended to drive an update to backend/text_utils.py: a small
list of nearly-certain acronyms gets added, and the borderline list is
shown for the maintainer to pick.

Polite-scraping: 1.5-3s jitter, 15s timeout, single-threaded (concurrent
hits from one IP get rate-limited by ATS vendors). Total runtime ~6 min.

Run: python3 scripts/audit_slug_acronyms.py"""
import asyncio
import random
import re
import sys
from collections import Counter
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

sys.path.insert(0, "/Users/sriram/Documents/GitHub/JobRadar/backend")
from text_utils import (
    _ACRONYMS_UPPER, _ACRONYM_TITLED, slug_to_title, strip_uuid_prefix,
)

_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

# Common English title words — these are NOT acronyms even if they appear
# often. The stoplist lives in the audit script only; production code
# stays free of it.
_STOPLIST = frozenset({
    "engineer", "developer", "architect", "designer", "manager", "lead",
    "head", "director", "principal", "associate", "intern", "internship",
    "senior", "junior", "staff", "product", "platform", "cloud", "data",
    "mobile", "web", "frontend", "backend", "fullstack", "research",
    "analyst", "specialist", "consultant", "strategist", "officer",
    "executive", "advisor", "representative", "student", "graduate",
    "newgrad", "new", "entry", "level", "engineering", "applied",
    "security", "network", "systems", "applications", "embedded",
    "solutions", "support", "marketing", "sales", "growth", "finance",
    "people", "operations", "office", "remote", "hybrid", "onsite",
    "founding", "early", "experienced", "core", "delivery", "ops",
    "leadership", "organization", "global", "country", "state", "city",
    "university", "team", "career", "infrastructure",
    "of", "the", "and", "for", "to", "with", "at", "in", "on", "via",
    # proper nouns sometimes splitting out that are obviously not acronyms
    "uk", "us", "eu",
})

# Verified against `job-boards.greenhouse.io/{slug}` (the current canonical
# subdomain — `boards.greenhouse.io` now redirects). Slugs returning 200
# are included; 4xx slugs (migrated off Greenhouse) were removed in a
# mid-2026 audit. Some entries return 403 under concurrent curl probes
# (rate-limit jitter during batch-testing) — they're kept because they
# served 200 on subsequent single-URL retries.
GREENHOUSE = [
    # Core active (200 verified)
    "stripe", "anthropic", "figma", "vercel", "airtable", "asana",
    "gitlab", "datadog", "cloudflare", "twilio", "okta", "mongodb",
    "elastic", "reddit", "airbnb", "instacart", "brex", "marqeta",
    "affirm", "robinhood", "gemini", "mercury", "sofi", "betterment",
    "gusto", "lattice",
    # Rate-limited (403 during batch-test, returned 200 on retry)
    "pinterest", "dropbox", "coinbase", "chime",
    # New additions from a 2026 sweep of known-Greenhouse companies
    "webflow", "iterable", "remotecom", "zeals", "gomotive", "checkr",
]

# WARNING: Many previously known Lever customers have migrated to other ATS
# platforms (Ashby, Greenhouse, etc.). The slugs below were verified active
# as of mid-2026. If the Lever corpus seems thin, run a board-discovery pass
# to find current Lever customers and their correct slugs (which may differ
# from the brand name — see `leverdemo-8` vs `leverdemo`).
LEVER = [
    "airslate", "lightship", "leverdemo-8",
]

ASHBY = [
    "anthropic", "openai", "ramp", "linear", "notion", "vercel",
    "cursor", "glean", "harvey", "perplexity", "runway", "suno",
    "replicate", "elevenlabs", "weightsbiases", "pinecone", "modal",
    "temporal", "apollo", "krea", "magic", "dust", "fal", "fermat",
    "kadoa", "labelbox", "scale", "character", "synthesia", "descript",
    "eliseai", "browseruse", "cognition", "flux", "foundry", "hex",
    "lambda", "quora", "recraft", "snorkel", "snyk", "speechify",
    "stability", "tag", "together", "incogni", "lavanya",
]

SMARTRECRUITERS = [
    "Salesforce", "Visa", "BoschGroup", "ServiceNow", "Etsy",
    "IKEA", "Square", "Twitch", "Allianz", "CERN", "OECD",
    "Mattel", "Adobe", "Docusign", "Intuit", "Siemens",
    "Mastercard", "Toyota", "Walmart", "Honeywell",
    "NorthropGrumman", "Boeing", "Citigroup", "JP Morgan",
]

# Workday uses a JS-rendered SPA, so the audit hits the JSON API instead of
# scraping HTML. The API endpoint is:
#   POST https://{company}.wd{N}.myworkdayjobs.com/wday/cxs/{company}/{tenant}/jobs
# Each entry is the full landing-page URL (the subdomain prefix varies between
# wd1 and wd5 across tenants). The `_workday_api_url` helper converts it.
WORKDAY = [
    "https://workday.wd5.myworkdayjobs.com/Workday",
    "https://adobe.wd5.myworkdayjobs.com/external_experienced",
    "https://target.wd5.myworkdayjobs.com/targetcareers",
    "https://capgroup.wd1.myworkdayjobs.com/capitalgroupcareers",
    "https://modernatx.wd1.myworkdayjobs.com/M_tx",
    "https://pultegroup.wd1.myworkdayjobs.com/PGI",
    "https://asmglobal.wd1.myworkdayjobs.com/careers",
    "https://exeterfinance.wd1.myworkdayjobs.com/External",
    "https://ntst.wd1.myworkdayjobs.com/Careers",
    "https://interface.wd1.myworkdayjobs.com/interface",
]

# Keep in sync with `text_utils._EMPIRICAL_SCOPE_PLATFORMS` if adding or
# renaming a platform — the docstrings in text_utils reference the tuple
# and would otherwise silently drift from what this script covers.
PLATFORMS = [
    ("greenhouse",      "https://job-boards.greenhouse.io/{}",  GREENHOUSE),
    ("lever",           "https://jobs.lever.co/{}",            LEVER),
    ("ashby",           "https://jobs.ashbyhq.com/{}",         ASHBY),
    ("smartrecruiters", "https://jobs.smartrecruiters.com/{}",  SMARTRECRUITERS),
    ("workday",         None,                                   WORKDAY),
]

_VOWELS = set("aeiouy")

# Auto-promote threshold: ≥10 occurrences AND length ≤4 AND 0 vowels.
# These are nearly-guaranteed acronyms in tech-job-board slugs.
_AUTO_MIN_FREQ = 10
_AUTO_MAX_LEN = 4
_AUTO_MAX_VOWELS = 0

# Lower bar: ≥3 occurrences makes the borderline queue.
_MANUAL_MIN_FREQ = 3

# Pure-numeric Greenhouse last-segment hrefs are just job IDs — skip them
# rather than tokenising the ID digits. The `_UUID_PREFIX_RE` regex lives
# in `text_utils.strip_uuid_prefix` as a single source of truth shared
# with the regression test.
_PURE_NUMERIC_RE = re.compile(r"^\d+$")

# Cities + US-state postal codes that show up when board listings carry
# location-laden slugs ("san-francisco-engineer"). They're not acronyms.
_LOCATION_TOKENS = frozenset({
    # Cities
    "san", "york", "los", "angeles", "francisco", "jose", "seattle",
    "austin", "boston", "chicago", "denver", "atlanta", "plano",
    # US state postal codes (2-letter)
    "al", "ak", "az", "ar", "ca", "co", "ct", "de", "fl", "ga",
    "hi", "id", "il", "in", "ia", "ks", "ky", "la", "me", "md",
    "ma", "mi", "mn", "ms", "mo", "mt", "ne", "nv", "nh", "nj",
    "nm", "ny", "nc", "nd", "oh", "ok", "or", "pa", "ri", "sc",
    "sd", "tn", "tx", "ut", "vt", "va", "wa", "wv", "wi", "wy",
    "dc",
    # Common prepositions + url keywords that show up when the selector
    # catches menu links.
    "for", "the", "and", "with", "from", "into", "jobs", "careers",
    "positions", "all", "view", "apply", "learn", "more", "details",
    # "Sr." coded without trailing dot
    "sr", "jr",
})


def _href_slug(href: str) -> str:
    """The slug that slug_to_title is actually fed: last non-empty path
    segment of the href, with any leading hex/UUID prefix stripped.

    Greenhouse hrefs are typically `/jobs/<numeric-id>` (last segment is
    pure digits) — those yield "" and are skipped. Lever / Ashby use
    `<uuid>-<title-slug>` so the uuid prefix is stripped to expose the
    real title slug.
    """
    seg = href.rstrip("/").split("/")[-1].lower()
    seg = strip_uuid_prefix(seg)
    if not seg or _PURE_NUMERIC_RE.match(seg):
        return ""
    return seg


async def _fetch(client, url):
    try:
        r = await client.get(url, timeout=15.0, follow_redirects=True)
        if r.status_code >= 400:
            return None
        return r.text
    except (httpx.HTTPError, httpx.TimeoutException):
        return None


def _workday_api_url(page_url: str) -> str:
    """Convert a Workday board landing-page URL to its JSON API endpoint.

    Landing page:  https://{company}.wd{N}.myworkdayjobs.com/{tenant}
    API endpoint:  https://{company}.wd{N}.myworkdayjobs.com/wday/cxs/{company}/{tenant}/jobs

    Handles language-path prefixes (/en-US/{tenant}) by taking the last
    path segment as the tenant name.
    """
    parsed = urlparse(page_url)
    domain = parsed.netloc  # e.g. "adobe.wd5.myworkdayjobs.com"
    company = domain.split(".")[0]  # e.g. "adobe"
    tenant = parsed.path.strip("/").split("/")[-1]  # last segment
    return f"https://{domain}/wday/cxs/{company}/{tenant}/jobs"


async def _fetch_workday(page_url: str) -> list[dict] | None:
    """Fetch job listings from a Workday board via its JSON API.

    Workday's API requires a session cookie established by visiting the
    landing page first, and the CSRF token must be forwarded as an
    explicit X-CSRF-Token header. Each board uses its own dedicated
    AsyncClient to avoid cookie-jar conflicts across tenants (wd1 vs
    wd5 subdomains set the same cookie names).

    Returns a list of job dicts with keys 'title', 'locationsText',
    and 'externalPath' (the URL path like /job/San-Jose/Title_R123).
    Returns None on any fetch-level failure.
    """
    api_url = _workday_api_url(page_url)
    # Workday rejects any limit above 20 (tested empirically across wd1/wd5).
    payload = {"limit": 20, "offset": 0}
    try:
        async with httpx.AsyncClient(headers={"User-Agent": _UA}) as client:
            # Establish session: visit landing page to get cookies + CSRF token
            await client.get(page_url, timeout=15.0, follow_redirects=True)
            # Extract CSRF token and forward as header
            csrf = client.cookies.get("CALYPSO_CSRF_TOKEN", domain=urlparse(page_url).netloc)
            headers = {
                "Accept": "application/json",
                "Content-Type": "application/json",
            }
            if csrf:
                headers["X-CSRF-Token"] = csrf
            r = await client.post(
                api_url,
                json=payload,
                timeout=15.0,
                headers=headers,
            )
            if r.status_code >= 400:
                return None
            data = r.json()
            return data.get("jobPostings", [])
    except (httpx.HTTPError, httpx.TimeoutException, ValueError):
        return None


def _workday_slugs(jobs: list[dict]) -> list[str]:
    """Extract unique slug tokens from Workday job `externalPath` values.

    A typical externalPath looks like:
      /job/San-Jose/Software-Engineer_R12345
    We split by `/` and `-`, strip trailing `_R...` numeric suffixes from
    the last segment, and yield each alpha-only token.
    """
    seen = set()
    slugs: list[str] = []
    for j in jobs:
        path = j.get("externalPath", "")
        segments = path.strip("/").split("/")
        for seg in segments:
            # Strip trailing _R{id} suffix from the last segment
            seg = re.sub(r"_[A-Za-z]\d+$", "", seg)
            for tok in seg.split("-"):
                tok = tok.strip()
                if not tok or not re.match(r"^[a-zA-Z]+$", tok):
                    continue
                tok_lower = tok.lower()
                if tok_lower not in seen and len(tok_lower) <= 5:
                    seen.add(tok_lower)
                    slugs.append(tok_lower)
    return slugs


async def audit():
    in_set: Counter = Counter()             # token IN current allowlist
    candidate: Counter = Counter()          # NOT in allowlist, not stoplist
    candidate_examples: dict[str, list[str]] = {}

    async with httpx.AsyncClient(headers={"User-Agent": _UA}) as client:
        for platform_name, url_template, boards in PLATFORMS:
            print(f"\n=== {platform_name} ({len(boards)} boards) ===", flush=True)
            is_workday = platform_name == "workday"
            n_ok = 0
            for company in boards:
                if is_workday:
                    # Workday is JS-rendered and needs a dedicated session —
                    # _fetch_workday creates its own AsyncClient per board.
                    jobs = await _fetch_workday(company)
                    if jobs is None:
                        continue
                    n_ok += 1
                    raw_slugs = _workday_slugs(jobs)
                    seen_slugs = set()
                    for tok in raw_slugs:
                        if tok in seen_slugs:
                            continue
                        seen_slugs.add(tok)
                        if len(tok) > 5:
                            continue
                        if tok in _ACRONYMS_UPPER or tok in _ACRONYM_TITLED:
                            in_set[tok] += 1
                        elif tok not in _STOPLIST and tok not in _LOCATION_TOKENS:
                            candidate[tok] += 1
                            # Workday tokens come from externalPath segments,
                            # not href slugs — no example context available.
                else:
                    url = company if company.startswith("http") else url_template.format(company)
                    html = await _fetch(client, url)
                    if html is None:
                        continue
                    n_ok += 1
                    soup = BeautifulSoup(html, "html.parser")
                    sel = ("a[href*='/job'], "
                           "a[href*='/career'], "
                           "a[href*='/position']")
                    seen_slugs = set()
                    for a in soup.select(sel):
                        href = a.get("href", "").strip()
                        slug = _href_slug(href)
                        if not slug or slug in seen_slugs:
                            continue
                        seen_slugs.add(slug)
                        for tok in slug.split("-"):
                            tok = tok.strip()
                            if not tok or not re.match(r"^[a-z]+$", tok):
                                continue
                            if len(tok) > 5:
                                continue
                            if tok in _ACRONYMS_UPPER or tok in _ACRONYM_TITLED:
                                in_set[tok] += 1
                            elif tok not in _STOPLIST and tok not in _LOCATION_TOKENS:
                                candidate[tok] += 1
                                candidate_examples.setdefault(tok, [])
                                if len(candidate_examples[tok]) < 3:
                                    candidate_examples[tok].append(slug)
                await asyncio.sleep(random.uniform(1.5, 3.0))
            print(f"  fetched ok: {n_ok}/{len(boards)}", flush=True)

    print("\n=== Already-in-allowlist coverage (top 40) ===")
    for tok, n in in_set.most_common(40):
        print(f"  {tok:12s}  x{n}")

    print("\n=== Candidate acronyms (NOT in allowlist, top 60 by freq) ===")
    auto_promote: list[str] = []
    manual_review: list[tuple[str, int, int, str]] = []
    for tok, n in sorted(candidate.items(), key=lambda x: -x[1]):
        if n < _MANUAL_MIN_FREQ:
            continue
        n_vowels = sum(1 for c in tok if c in _VOWELS)
        ex = ", ".join(candidate_examples.get(tok, [])[:2])
        is_auto = (
            n >= _AUTO_MIN_FREQ
            and len(tok) <= _AUTO_MAX_LEN
            and n_vowels <= _AUTO_MAX_VOWELS
        )
        marker = "AUTO *" if is_auto else "      "
        flag = ""
        # Print everything for visibility, but also partition.
        print(f"  [{marker}] {tok:10s}  x{n:5d}  v={n_vowels}  l={len(tok)}  e.g. {ex}")
        if is_auto:
            auto_promote.append(tok)
        else:
            manual_review.append((tok, n, n_vowels, ex))

    print("\n\n### AUTO-PROMOTE INTO _ACRONYMS_UPPER: " + repr(sorted(auto_promote)))


if __name__ == "__main__":
    asyncio.run(audit())
