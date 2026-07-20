"""Shared text helpers — primarily acronym-aware slug title casing.

Career-page href slugs (`/jobs/ml-engineer`) are the most consistent
identity for a role: anchor text is unreliable (often a "See role" CTA,
sometimes a stray location list, sometimes nothing). Plain
`slug.replace("-", " ").title()` miscases acronyms (`"ml"` -> `"Ml"`,
`"ceo"` -> `"Ceo"`); this helper keeps a small explicit allowlist of
tech-job acronyms and a few mixed-case exceptions (MLOps, DevOps —
the ones that aren't all-caps) so slugs read like real titles.
"""

# All-caps tech acronyms. Populate from observed slugs on Greenhouse,
# Lever, Ashby, YC, LinkedIn. Missing entries fall through to
# .capitalize() — at worst that yields "Mlops" which is still legible.
_ACRONYMS_UPPER = frozenset({
    # AI / ML
    "ai", "ml", "nlp", "cv", "llm",
    # Engineering discipline abbreviations
    "sre", "sde", "sdet", "swe", "api", "sdk",
    # Front/back ends and testing
    "ui", "ux", "fe", "be", "qa",
    # Mobile
    "ios", "android", "rn",
    # Generic IT
    "it",
    # Product / business
    "pm", "tpm", "gtm", "b2b", "b2c",
    # Leadership
    "ceo", "cto", "cfo", "coo", "vp", "ciso",
    # Early-career marker (LinkedIn uses "EC - Software Engineer")
    "ec",
})

# Platform enumeration: the three ATS platforms whose href-derived slugs
# have been empirically audited (see `scripts/audit_slug_acronyms.py`).
# Used to scope the empirical claim across docstrings below. Keep in
# sync with `slug_to_title`'s docstring if a new platform is added.
_EMPIRICAL_SCOPE_PLATFORMS = ("greenhouse", "lever", "ashby", "workday")

# Mixed-case acronyms — can't be expressed in `_ACRONYMS_UPPER` because
# .title() / .upper() would either lowercase or uppercase the rest.
# Ordered so the title-cased form wins over the .upper() fallback.
_ACRONYM_TITLED = {
    "devops": "DevOps",
    "mlops": "MLOps",
    "genai": "GenAI",
    "saas": "SaaS",
}

# Speculative picks (without empirical evidence — keep under
# `_SPECULATIVE_ACRONYMS` so additions are deliberate, not auto-grow):
#   "ic"          → "IC"  (Individual Contributor track)
# Empirical coverage validated for `_EMPIRICAL_SCOPE_PLATFORMS` href-
# derived slugs only (see `scripts/audit_slug_acronyms.py`). Other ATS
# platforms (iCIMS, SmartRecruiters, custom-hosted boards) have not
# been sampled — solicit data before adding entries for them.
_SPECULATIVE_ACRONYMS = frozenset({"ic"})

# Shared regex: Lever / Ashby encode role-detail hrefs as
# `<uuid>-<title-slug>`. The audit script and a regression test both
# strip the leading hex prefix before tokenising; keeping the regex
# here gives them a single source of truth.
import re
_UUID_PREFIX_RE = re.compile(r"^[a-f0-9]{8,}-")


def strip_uuid_prefix(href_segment: str) -> str:
    """Return `href_segment` with any leading 8+ char hex/UUID prefix
    stripped. Used by the audit script (corpus extraction) and the
    regression test (post-uuid round-trip). Centralising the regex here
    keeps the two callers in lockstep."""
    return _UUID_PREFIX_RE.sub("", href_segment)


def get_speculative_acronyms() -> frozenset[str]:
    """Public read-only view of `_SPECULATIVE_ACRONYMS` for tests and
    tooling that need to introspect the speculative bucket without
    importing a private module name. Returns a fresh frozenset copy so
    caller-side mutation cannot corrupt the underlying module state."""
    return frozenset(_SPECULATIVE_ACRONYMS)


def slug_to_title(slug: str) -> str:
    """Convert a hyphen-separated href slug to a human-readable title.

    Acronyms keep their canonical case (`"ml-engineer"` -> `"ML Engineer"`,
    `"mlops-engineer"` -> `"MLOps Engineer"`). Empty / dash-only inputs
    return `""`. Tokens like `"eng"` / `"staff"` / `"intern"` are NOT
    treated as acronyms on purpose — they read fine as Title Case.

    The speculative set is included to pre-empt obvious mis-casing when
    a future synthetic slug (e.g. auto-discovered careers page) carries
    one of these tokens. The corpus audit confirmed real href-derived
    slugs from `_EMPIRICAL_SCOPE_PLATFORMS` boards carry *no* acronyms
    at high frequency — the speculative set exists for the synthetic-
    slug path instead. Keep this claim in sync with the
    `_EMPIRICAL_SCOPE_PLATFORMS` tuple above if a new ATS platform is
    added to the audit corpus.
    """
    parts = [p for p in slug.split("-") if p.strip()]
    out: list[str] = []
    for p in parts:
        low = p.lower()
        if low in _ACRONYM_TITLED:
            out.append(_ACRONYM_TITLED[low])
        elif low in _ACRONYMS_UPPER or low in _SPECULATIVE_ACRONYMS:
            out.append(low.upper())
        else:
            out.append(p.capitalize())
    return " ".join(out)
