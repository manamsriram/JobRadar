"""Outreach phase 1: find who to email at a company, and guess their address.

Nothing here spends a Hunter credit and nothing here sends mail — discovery
runs through search_api.py (the same backend ladder the job search uses), and
every address it produces is a *guess* carrying its evidence. Verification
(phase 2) and drafting/sending (phase 3) come later; see
docs/superpowers/specs/2026-08-30-outreach-automation-design.md.

Pure functions (name parsing, pattern generation, ranking, pattern status) are
kept apart from the network path so they test without network, matching
test_board_scraper.py's convention.
"""
import logging
import os
import re
from datetime import datetime, timedelta, timezone

import state

logger = logging.getLogger(__name__)

PATTERNS_FILE = state.DATA_DIR / "outreach_patterns.json"

# A discovered candidate list is reused for a week before searching again, so
# a second job at the same company costs zero searches.
DISCOVERY_TTL_DAYS = 7
# Companies change email providers; past this age a pattern stops counting as
# verified (decay on read — nothing recomputes on a schedule).
PATTERN_STALE_DAYS = 180


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso() -> str:
    return _now().isoformat()


# ---- Name parsing ----
# Suffixes and credentials ride along in SERP titles ("Jane Doe, MBA") and
# would otherwise be parsed as the last name.
_NAME_NOISE = {"jr", "sr", "ii", "iii", "iv", "phd", "mba", "cpa", "msc", "ma", "bs"}
_NAME_TOKEN = re.compile(r"^[^\W\d_]+(?:[-'][^\W\d_]+)*$", re.UNICODE)


def parse_name(full_name: str | None) -> tuple[str, str] | None:
    """(first, last) lowercased, or None when the name doesn't split cleanly.

    None is a meaningful answer, not a failure: an unparseable name is exactly
    the case where a correct domain pattern still yields a wrong address, so
    callers must not auto-promote it.
    """
    tokens = [t.strip(".,") for t in (full_name or "").replace(" ", " ").split()]
    tokens = [t for t in tokens if t and t.strip(".,-'").lower() not in _NAME_NOISE]
    # Initials ("J. Doe") leave no usable first name for most patterns.
    tokens = [t for t in tokens if _NAME_TOKEN.match(t) and len(t) > 1]
    if len(tokens) < 2:
        return None
    return tokens[0].lower(), tokens[-1].lower()


# ---- Pattern engine ----
# Global frequency order, most common first. Ranking falls back to this
# whenever the domain has no learned evidence either way.
PATTERN_ORDER = [
    "first.last", "first", "flast", "firstlast", "f.last", "first_last", "last.first",
]

_PATTERN_BUILDERS = {
    "first.last": lambda f, l: f"{f}.{l}",
    "first": lambda f, l: f,
    "flast": lambda f, l: f"{f[0]}{l}",
    "firstlast": lambda f, l: f"{f}{l}",
    "f.last": lambda f, l: f"{f[0]}.{l}",
    "first_last": lambda f, l: f"{f}_{l}",
    "last.first": lambda f, l: f"{l}.{f}",
}


def _ascii_local(part: str) -> str:
    """Strip anything an SMTP local part can't carry (accents, hyphens)."""
    import unicodedata
    folded = unicodedata.normalize("NFKD", part)
    return re.sub(r"[^a-z0-9]", "", folded.encode("ascii", "ignore").decode().lower())


def generate_guesses(full_name: str, domain: str) -> list[dict]:
    """Every pattern permutation for one name at one domain, unranked."""
    parsed = parse_name(full_name)
    if not parsed or not domain:
        return []
    first, last = (_ascii_local(p) for p in parsed)
    if not first or not last:
        return []
    return [
        {"email": f"{_PATTERN_BUILDERS[p](first, last)}@{domain}", "pattern": p}
        for p in PATTERN_ORDER
    ]


# ---- Pattern memory ----
# {domain: {"patterns": {name: {"verified": int, "failed": int,
#                               "last_verified_at": iso|null}},
#           "accept_all": bool, "updated_at": iso}}
def load_patterns() -> dict:
    return state._read_json(PATTERNS_FILE, {})


def save_patterns(patterns: dict) -> None:
    state._write_json_atomic(PATTERNS_FILE, patterns)


def pattern_status(entry: dict | None, accept_all: bool = False,
                   now: datetime | None = None) -> str:
    """"verified" | "probable" | "unknown" for one pattern record.

    Two clean verifications earn "verified"; an accept-all domain says yes to
    anything, so its evidence never proves more than "probable"; and evidence
    older than PATTERN_STALE_DAYS decays to "probable" on read.
    """
    if not entry:
        return "unknown"
    verified = int(entry.get("verified") or 0)
    if verified < 1 or verified <= int(entry.get("failed") or 0):
        return "unknown"
    status = "verified" if verified >= 2 else "probable"
    if status == "verified" and (accept_all or _is_stale(entry, now)):
        status = "probable"
    return status


def _is_stale(entry: dict, now: datetime | None = None) -> bool:
    last = entry.get("last_verified_at")
    if not last:
        return True
    try:
        return (now or _now()) - datetime.fromisoformat(last) > timedelta(days=PATTERN_STALE_DAYS)
    except ValueError:
        logger.warning("outreach: unparseable last_verified_at %r, treating as stale", last)
        return True


def domain_memory(domain: str, patterns: dict | None = None) -> dict:
    return (patterns if patterns is not None else load_patterns()).get(domain) or {}


# ---- Ranking ----
# Reasons are fixed identifiers, never prose: the UI decides the wording, and
# tests can assert on them.
_TITLE_TIERS = [
    (3, "high_recruiter_title_relevance",
     ("recruiter", "recruiting", "talent acquisition", "sourcer", "talent partner")),
    (2, "medium_people_title_relevance",
     ("people ops", "people operations", "human resources", " hr ", "talent")),
    (1, "low_hiring_manager_title_relevance",
     ("hiring manager", "engineering manager", "head of engineering")),
]


def _title_tier(title: str | None) -> tuple[int, str | None]:
    padded = f" {(title or '').lower()} "
    for weight, reason, needles in _TITLE_TIERS:
        if any(n in padded for n in needles):
            return weight, reason
    return 0, None


def rank_contacts(candidates: list[dict], now: datetime | None = None) -> list[dict]:
    """Sort discovered candidates best-first, annotating score and reasons.

    Hiring managers only ever place below recruiters — the tier weights, not a
    filter, express "only when the first two are empty".
    """
    ranked = []
    for c in candidates:
        score, reasons = 0, []
        tier, tier_reason = _title_tier(c.get("title"))
        score += tier * 10
        if tier_reason:
            reasons.append(tier_reason)

        if c.get("source_type") == "company_site":
            score += 5
            reasons.append("source_company_domain")
        elif c.get("source_type") == "linkedin":
            score += 3
            reasons.append("source_linkedin_profile")

        if parse_name(c.get("name")):
            score += 2
            reasons.append("name_parses_cleanly")
        else:
            reasons.append("name_unparseable")

        age = _age_days(c.get("discovered_at"), now)
        if age is not None and age <= DISCOVERY_TTL_DAYS:
            score += 1
            reasons.append("recently_discovered")

        ranked.append({**c, "score": score, "reasons": reasons})
    ranked.sort(key=lambda c: -c["score"])
    return ranked


def _age_days(iso: str | None, now: datetime | None = None) -> float | None:
    if not iso:
        return None
    try:
        return ((now or _now()) - datetime.fromisoformat(iso)).total_seconds() / 86400
    except ValueError:
        return None


def rank_guesses(guesses: list[dict], memory: dict | None = None,
                 now: datetime | None = None) -> list[dict]:
    """Sort address guesses best-first using what the domain has taught us.

    A verified domain pattern outranks everything; a pattern with recorded
    failures ranks below one with no evidence at all.
    """
    memory = memory or {}
    known = memory.get("patterns") or {}
    accept_all = bool(memory.get("accept_all"))
    ranked = []
    for g in guesses:
        entry = known.get(g["pattern"])
        status = pattern_status(entry, accept_all, now)
        score, reasons = 0, []
        if status == "verified":
            score += 100
            reasons.append("matched_verified_domain_pattern")
        elif status == "probable":
            score += 50
            reasons.append("matched_probable_domain_pattern")
        if accept_all and entry:
            reasons.append("accept_all_domain_penalty")
        if entry and int(entry.get("failed") or 0) > 0:
            score -= 20 * int(entry["failed"])
            reasons.append("pattern_failed_at_domain")
        # Frequency prior: earlier in PATTERN_ORDER is more common.
        score += len(PATTERN_ORDER) - PATTERN_ORDER.index(g["pattern"])
        ranked.append({**g, "status": status, "score": score, "reasons": reasons})
    ranked.sort(key=lambda g: -g["score"])
    return ranked


# ---- Recruiter discovery (Google) ----
# Templates are tried in order until one returns candidates — one query per
# run, not one per template, so discovery costs the same as a job search page.
_RECRUITER_TEMPLATES = [
    'site:linkedin.com/in "{company}" (recruiter OR "talent acquisition" OR sourcer)',
    'site:{domain} (recruiting OR "talent acquisition" OR careers)',
    '"{company}" (recruiter OR "university recruiter" OR "campus recruiter")',
]


def build_recruiter_queries(company: str, domain: str) -> list[str]:
    """Recruiter queries for one company, each under Google's 32-word cap."""
    from scrapers.board_scraper import GOOGLE_MAX_QUERY_TOKENS
    company = (company or "").strip()
    if not company:
        return []
    queries = []
    for tpl in _RECRUITER_TEMPLATES:
        if "{domain}" in tpl and not domain:
            continue
        q = tpl.format(company=company, domain=domain)
        if len(q.split()) <= GOOGLE_MAX_QUERY_TOKENS:
            queries.append(q)
    return queries


# "Jane Doe - Technical Recruiter - Acme | LinkedIn" and the em-dash variants
# Google renders. Everything after the first separator is the title.
_TITLE_SPLIT = re.compile(r"\s+[-–—|]\s+")


def _looks_like_person(text: str) -> bool:
    """A page title ("Careers at Acme") parses as first/last just as happily as
    a person does, so the SERP path asks for the shape of a name too: two or
    three capitalised tokens, no lowercase connectives."""
    tokens = text.split()
    return 2 <= len(tokens) <= 3 and all(t[:1].isupper() for t in tokens)


def parse_candidate_title(anchor_text: str) -> tuple[str | None, str | None]:
    """(name, title) from SERP link text; either may be None."""
    parts = [p.strip() for p in _TITLE_SPLIT.split(anchor_text or "") if p.strip()]
    parts = [p for p in parts if p.lower() != "linkedin"]
    if not parts:
        return None, None
    name = parts[0] if _looks_like_person(parts[0]) and parse_name(parts[0]) else None
    title = parts[1] if len(parts) > 1 else None
    return name, title


_COMPANY_SUFFIXES = re.compile(r"\b(inc|llc|corp|corporation|co|ltd|company)\b\.?",
                                re.IGNORECASE)


def _normalize_company(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", _COMPANY_SUFFIXES.sub("", text or "").lower())


def _company_matches(anchor_text: str, company: str) -> bool:
    """Whether the target company name shows up anywhere in the SERP text.

    LinkedIn titles read "Name - Title - Company | LinkedIn" — without this
    check any recruiter-shaped name anywhere gets kept, including people at a
    similarly-named company or who no longer work there. No target company
    (empty string) can't be checked, so it passes rather than dropping every
    LinkedIn result.
    """
    norm_company = _normalize_company(company)
    return not norm_company or norm_company in _normalize_company(anchor_text)


def candidates_from_results(results: list[dict], domain: str,
                            company: str = "") -> list[dict]:
    """Keep the results that point at a LinkedIn profile or the company's own
    site, and that carry something shaped like a person's name.

    A company-site URL already proves the company match via `domain`; a
    LinkedIn profile does not, so it additionally needs the company name
    somewhere in the SERP title.
    """
    out, seen = [], set()
    for r in results:
        href = r.get("url") or ""
        if not href.startswith("http") or href in seen:
            continue
        anchor_text = r.get("title") or ""
        if "linkedin.com/in/" in href:
            source_type = "linkedin"
            if not _company_matches(anchor_text, company):
                continue
        elif domain and domain in href:
            source_type = "company_site"
        else:
            continue
        name, title = parse_candidate_title(anchor_text)
        if not name:
            continue
        seen.add(href)
        out.append({
            "name": name, "title": title, "source_url": href,
            "source_type": source_type, "origin": "search",
            "discovered_at": _iso(),
        })
    return out


def _parse_recruiter_serp(html: str, domain: str, company: str = "") -> list[dict]:
    """Raw-HTML path, used by the Chromium fallback backend and its tests."""
    import search_api
    return candidates_from_results(search_api.parse_serp_anchors(html), domain, company)


async def discover_candidates(company: str, domain: str,
                              health: dict) -> tuple[list[dict], dict, str | None]:
    """Run recruiter queries through the shared Google path.

    Returns (candidates, health, blocked_until). `blocked_until` is set when
    the CAPTCHA backoff is active — discovery is user-triggered, so the caller
    must say "rate-limited until X" rather than show an empty result as though
    the company had no recruiters.
    """
    from scrapers import board_scraper as bs

    if bs.is_google_backing_off(health):
        return [], health, bs.google_blocked_until(health)

    candidates: list[dict] = []
    for query in build_recruiter_queries(company, domain):
        html_jobs, ok, blocked = await _fetch_serp(query, domain, company)
        if blocked:
            health = bs.mark_google_blocked(health)
            return [], health, bs.google_blocked_until(health)
        if ok:
            health = bs.clear_google_block(health)
        candidates.extend(html_jobs)
        if candidates:
            break
    return rank_contacts(candidates), health, None


async def _fetch_serp(query: str, domain: str, company: str = "") -> tuple[list[dict], bool, bool]:
    """Recruiter twin of fetch_google_boolean — same backend ladder, different
    parser. No recency filter: a recruiter's profile page is worth finding
    whether it was indexed today or last year."""
    import search_api

    results, ok, blocked = await search_api.search(query, days=None)
    if blocked or not ok:
        return [], ok, blocked
    try:
        return candidates_from_results(results, domain, company), True, False
    except Exception as e:
        logger.error("recruiter SERP parse failed: %s", e)
        return [], False, False


# ---- Pattern learning (phase 2) ----
def learn_pattern(domain: str, pattern: str, ok: bool, accept_all: bool = False,
                  patterns: dict | None = None, now: datetime | None = None) -> dict:
    """Record one verification outcome and return the updated patterns dict.

    Failures decrement standing but never delete a pattern — a company can use
    two patterns at once, and forgetting the loser would make us re-learn it.
    `last_failed_at` is what the auto-promotion guard reads to require two
    clean successes *after* a failure.
    """
    patterns = load_patterns() if patterns is None else patterns
    stamp = (now or _now()).isoformat()
    entry = patterns.setdefault(domain, {"patterns": {}, "accept_all": False})
    entry["accept_all"] = bool(entry.get("accept_all")) or accept_all
    record = entry["patterns"].setdefault(
        pattern, {"verified": 0, "failed": 0, "last_verified_at": None, "last_failed_at": None})
    if ok:
        record["verified"] = int(record.get("verified") or 0) + 1
        record["last_verified_at"] = stamp
    else:
        record["failed"] = int(record.get("failed") or 0) + 1
        record["last_failed_at"] = stamp
    entry["updated_at"] = stamp
    return patterns


def _clean_successes_since_failure(record: dict) -> bool:
    """True when the pattern has 2+ successes recorded after its last failure.
    A pattern that has never failed passes trivially."""
    last_failed = record.get("last_failed_at")
    if not last_failed:
        return True
    last_verified = record.get("last_verified_at")
    if not last_verified:
        return False
    try:
        if datetime.fromisoformat(last_verified) <= datetime.fromisoformat(last_failed):
            return False
    except ValueError:
        return False
    # Successes aren't timestamped individually, so require the count to have
    # outrun the failures by the same margin a clean pattern needs.
    return int(record.get("verified") or 0) - int(record.get("failed") or 0) >= 2


def send_eligibility(guess: dict, contact: dict, memory: dict,
                     now: datetime | None = None) -> dict:
    """Can this guess be sent without spending a verification credit?

    Auto-promotion is what makes a 50-credit month go far: 2 verifications buy
    a domain, then every later contact there is free. Each guard closes a real
    false-positive path, so none of them are optional — see the design doc's
    "Auto-promotion (decided)" section.
    """
    if guess.get("verified_status") == "valid":
        return {"eligible": True, "basis": "verified_address", "blockers": []}

    blockers = []
    record = ((memory.get("patterns") or {}).get(guess.get("pattern"))) or {}
    if pattern_status(record, bool(memory.get("accept_all")), now) != "verified":
        # Covers all three of: too few samples, accept-all cap, staleness decay.
        blockers.append("pattern_not_verified")
    if not _clean_successes_since_failure(record):
        blockers.append("pattern_failed_since_last_success")
    if not parse_name(contact.get("name")):
        # The pattern can be right while the name plugged into it is wrong.
        blockers.append("name_unparseable")
    if blockers:
        return {"eligible": False, "basis": "needs_verification", "blockers": blockers}
    return {"eligible": True, "basis": "auto_promoted_pattern", "blockers": []}


# ---- Verified-address store ----
# A confirmed address is the cheapest thing we can own: the next job at that
# company reuses it for free, no search and no Hunter credit. It lives in the
# same company_contacts.json entry the UI already reads, keyed by source_url
# (a person can appear twice with different guesses, never twice from the same
# profile URL).
VERIFIED_STALE_DAYS = 365


def stored_verified_email(domain: str, contact: dict,
                          now: datetime | None = None) -> dict | None:
    """A previously confirmed address for this person, or None.

    People change jobs, so a year-old confirmation stops counting — better to
    spend one credit than to mail an address that bounced six months ago.
    """
    key = contact.get("source_url") or contact.get("name")
    for stored in _stored_contacts(domain):
        if (stored.get("source_url") or stored.get("name")) != key:
            continue
        if not stored.get("email") or stored.get("verified_status") != "valid":
            continue
        return None if _stale_stored(stored, now) else stored
    return None


def _stale_stored(stored: dict, now: datetime | None = None) -> bool:
    age = _age_days(stored.get("verified_at"), now)
    return age is None or age > VERIFIED_STALE_DAYS


def _stored_contacts(domain: str) -> list[dict]:
    return (state._read_json(enricher_cache_file(), {}).get(domain) or {}).get("contacts") or []


def store_verified_email(domain: str, contact: dict, email: str, pattern: str,
                         verification: dict, now: datetime | None = None) -> None:
    """Persist a confirmed address against the person it belongs to."""
    cache = state._read_json(enricher_cache_file(), {})
    entry = cache.setdefault(domain, {"contacts": [], "domain_guessed": False})
    key = contact.get("source_url") or contact.get("name")
    contacts = entry.setdefault("contacts", [])
    for stored in contacts:
        if (stored.get("source_url") or stored.get("name")) == key:
            target = stored
            break
    else:
        target = {**contact}
        contacts.append(target)
    target.update({
        "email": email,
        "email_pattern": pattern,
        "verified_status": verification.get("status"),
        "verified_score": verification.get("score"),
        "verified_at": (now or _now()).isoformat(),
    })
    state._write_json_atomic(enricher_cache_file(), cache)


async def verify_contact(domain: str, contact: dict) -> dict:
    """Confirm one contact's address, spending at most one Hunter credit.

    Three ladders of thrift, cheapest first: a stored confirmation for this
    person costs nothing, then an auto-promotion from a verified domain
    pattern costs nothing, and only a genuinely new domain reaches Hunter.
    Whatever the verifier says is fed back into the pattern memory, so the
    credit buys knowledge about every future contact there too.
    """
    import enricher

    memory = domain_memory(domain)
    guesses = rank_guesses(generate_guesses(contact.get("name") or "", domain), memory)
    if not guesses:
        return {"error": "unparseable_name"}

    stored = stored_verified_email(domain, contact)
    if stored:
        return {"email": stored["email"], "pattern": stored.get("email_pattern"),
                "status": "valid", "basis": "stored_verified_address", "spent_credit": False}

    top = guesses[0]
    eligibility = send_eligibility(top, contact, memory)
    if eligibility["basis"] == "auto_promoted_pattern":
        return {"email": top["email"], "pattern": top["pattern"], "status": "auto_promoted",
                "basis": "auto_promoted_pattern", "spent_credit": False}

    result = await enricher.verify_email(top["email"])
    if result.get("error"):
        # A provider failure is not evidence about the pattern — record nothing.
        return {"error": result["error"], "email": top["email"], "spent_credit": False}

    ok = result["status"] == "valid"
    save_patterns(learn_pattern(domain, top["pattern"], ok, result.get("accept_all", False)))
    if ok:
        store_verified_email(domain, contact, top["email"], top["pattern"], result)
    return {
        "email": top["email"], "pattern": top["pattern"], "status": result["status"],
        "score": result.get("score"), "accept_all": result.get("accept_all"),
        "basis": "hunter_verified", "spent_credit": True,
    }


# ---- Cache ----
def cached_candidates(domain: str, now: datetime | None = None) -> list[dict] | None:
    """Discovered candidates for a domain, or None when absent/stale."""
    cached = state._read_json(enricher_cache_file(), {}).get(domain) or {}
    found = [c for c in (cached.get("contacts") or []) if c.get("origin") == "search"]
    if not found:
        return None
    age = _age_days(cached.get("discovered_at") or cached.get("researched_at"), now)
    if age is None or age > DISCOVERY_TTL_DAYS:
        return None
    return found


def enricher_cache_file():
    import enricher
    return enricher.CONTACTS_CACHE_FILE


def store_candidates(domain: str, candidates: list[dict], guessed: bool) -> None:
    """Merge discovered candidates into the existing contacts cache by
    source_url, leaving Hunter-returned contacts untouched."""
    cache = state._read_json(enricher_cache_file(), {})
    entry = cache.get(domain) or {"contacts": [], "domain_guessed": guessed}
    by_key = {c.get("source_url") or c.get("email"): c for c in entry.get("contacts", [])}
    for c in candidates:
        by_key[c.get("source_url") or c.get("email")] = c
    entry["contacts"] = list(by_key.values())
    entry["discovered_at"] = _iso()
    entry.setdefault("researched_at", entry["discovered_at"])
    entry["domain_guessed"] = entry.get("domain_guessed", guessed)
    cache[domain] = entry
    state._write_json_atomic(enricher_cache_file(), cache)


# ---- Orchestration ----
def build_report(domain: str, candidates: list[dict],
                 patterns: dict | None = None, now: datetime | None = None) -> dict:
    """Ranked candidates, each with its ranked address guesses. Pure — the
    whole phase-1 payload, no network, no credits."""
    memory = domain_memory(domain, patterns)
    people = []
    for c in rank_contacts(candidates, now):
        guesses = rank_guesses(generate_guesses(c.get("name") or "", domain), memory, now)
        # A stored confirmation outranks any guess — that address cost a credit
        # once and is free forever after.
        if c.get("email") and c.get("verified_status") == "valid":
            top = {"email": c["email"], "pattern": c.get("email_pattern"),
                   "status": "verified", "verified_status": "valid",
                   "score": 1000, "reasons": ["stored_verified_address"]}
            guesses = [top] + [g for g in guesses if g["email"] != c["email"]]
        eligibility = send_eligibility(guesses[0], c, memory, now) if guesses else {
            "eligible": False, "basis": "no_guess", "blockers": ["unparseable_name"]}
        people.append({**c, "guesses": guesses, "eligibility": eligibility})
    return {
        "domain": domain,
        "accept_all": bool(memory.get("accept_all")),
        "candidates": people,
    }


async def research_company(company: str, posting_url: str | None,
                           force: bool = False) -> dict:
    """Cached-first recruiter discovery for one company. Never raises past
    here; on a CAPTCHA block it returns blocked_until so the UI can say why
    the result is empty."""
    import enricher

    resolved = enricher.resolve_domain(company, posting_url)
    if resolved is None:
        return {"error": "no_domain"}
    domain, guessed = resolved

    cached = None if force else cached_candidates(domain)
    if cached is not None:
        return {**build_report(domain, cached), "domain_guessed": guessed, "cached": True}

    health = state.load_health()
    candidates, health, blocked_until = await discover_candidates(company, domain, health)
    state.save_health(health)
    if blocked_until:
        return {"error": "search_backoff", "blocked_until": blocked_until, "domain": domain}
    if candidates:
        store_candidates(domain, candidates, guessed)
    return {**build_report(domain, candidates), "domain_guessed": guessed, "cached": False}


# ---- Outreach log (dedup + audit in one file) ----
LOG_FILE = state.DATA_DIR / "outreach_log.json"
DEDUP_COOLDOWN_DAYS = int(os.getenv("OUTREACH_DEDUP_COOLDOWN_DAYS", "30"))
DAILY_SEND_CAP = int(os.getenv("OUTREACH_DAILY_SEND_CAP", "10"))
SEND_ENABLED = os.getenv("OUTREACH_SEND_ENABLED", "false").lower() == "true"

# Gmail label per stage: everything sent lands in the first, and a detected
# reply moves it to the second (backend/outreach_mail.py does the IMAP work).
LABEL_SENT = os.getenv("OUTREACH_LABEL_SENT", "reach out")
LABEL_REPLIED = os.getenv("OUTREACH_LABEL_REPLIED", "hiring")


def load_log() -> list[dict]:
    return state._read_json(LOG_FILE, [])


def save_log(records: list[dict]) -> None:
    state._write_json_atomic(LOG_FILE, records)


def find_duplicate(job_id: str, email: str, log: list[dict] | None = None,
                   domain: str | None = None, now: datetime | None = None) -> dict | None:
    """The prior record that blocks this send, or None.

    Returned rather than boolean on purpose: a blocked duplicate should show
    the user *when* and *what* was already sent, not silently vanish.
    """
    email = (email or "").lower()
    for record in reversed(log if log is not None else load_log()):
        if (record.get("email") or "").lower() != email:
            continue
        if record.get("job_id") == job_id:
            return record
        if domain and record.get("domain") == domain and record.get("status") == "sent":
            age = _age_days(record.get("sent_at") or record.get("created_at"), now)
            if age is not None and age < DEDUP_COOLDOWN_DAYS:
                return record
    return None


def sent_today(log: list[dict] | None = None, now: datetime | None = None) -> int:
    today = (now or _now()).date()
    count = 0
    for record in log if log is not None else load_log():
        if record.get("status") != "sent" or not record.get("sent_at"):
            continue
        try:
            if datetime.fromisoformat(record["sent_at"]).date() == today:
                count += 1
        except ValueError:
            continue
    return count


def check_send_allowed(job_id: str, email: str, domain: str, eligibility: dict,
                       override: bool = False, log: list[dict] | None = None,
                       now: datetime | None = None) -> dict:
    """Every gate that stands between a draft and an actual send.

    Pure so the rules are testable and so the caller can show the user exactly
    which gate is closed. `override` covers only the confidence gate — the
    kill switch, the daily cap and dedup are not overridable from a request.
    """
    log = load_log() if log is None else log
    if not SEND_ENABLED:
        return {"allowed": False, "reason": "sending_disabled"}
    if sent_today(log, now) >= DAILY_SEND_CAP:
        return {"allowed": False, "reason": "daily_cap_reached"}
    duplicate = find_duplicate(job_id, email, log, domain, now)
    if duplicate:
        return {"allowed": False, "reason": "duplicate", "prior": duplicate}
    if not eligibility.get("eligible") and not override:
        return {"allowed": False, "reason": "unverified_address",
                "blockers": eligibility.get("blockers", [])}
    return {"allowed": True, "override_used": bool(override and not eligibility.get("eligible")),
            "basis": eligibility.get("basis")}


def record_outreach(record: dict, now: datetime | None = None) -> dict:
    """Append one draft/send record. Every draft is logged whether or not it
    is ever sent — the log is the audit trail as well as the dedup index."""
    log = load_log()
    stamp = (now or _now()).isoformat()
    record = {**record, "created_at": record.get("created_at") or stamp, "updated_at": stamp}
    record.setdefault("id", f"{record.get('job_id','')}:{(record.get('email') or '').lower()}")
    for i, existing in enumerate(log):
        if existing.get("id") == record["id"]:
            log[i] = {**existing, **record}
            break
    else:
        log.append(record)
    save_log(log)
    return record


def mark_replied(record_id: str, now: datetime | None = None) -> dict | None:
    log = load_log()
    for record in log:
        if record.get("id") == record_id:
            record["status"] = "replied"
            record["replied_at"] = (now or _now()).isoformat()
            record["label"] = LABEL_REPLIED
            save_log(log)
            return record
    return None
