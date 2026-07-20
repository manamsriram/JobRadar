"""Tests for text_utils.slug_to_title — acronym-aware slug casing.

Run: pytest backend/test_text_utils.py
"""
import pytest

from text_utils import (
    get_speculative_acronyms,
    slug_to_title,
    strip_uuid_prefix,
)


def test_simple_two_word_slug():
    assert slug_to_title("software-engineer") == "Software Engineer"


def test_single_word_slug():
    assert slug_to_title("engineer") == "Engineer"


def test_leading_all_caps_acronym():
    assert slug_to_title("ml-engineer") == "ML Engineer"


def test_trailing_all_caps_acronym():
    assert slug_to_title("engineer-ml") == "Engineer ML"


def test_multiple_all_caps_acronyms():
    assert slug_to_title("senior-ml-ai-engineer") == "Senior ML AI Engineer"


def test_titled_acronym_mlops():
    assert slug_to_title("mlops-engineer") == "MLOps Engineer"


def test_titled_acronym_devops():
    assert slug_to_title("devops-engineer") == "DevOps Engineer"


def test_titled_acronym_saas():
    assert slug_to_title("saas-engineer") == "SaaS Engineer"


def test_titled_acronym_genai():
    assert slug_to_title("genai-engineer") == "GenAI Engineer"


def test_non_acronym_words_capitalized_only():
    # "eng", "staff", "intern" intentionally NOT in acronym sets — they
    # read fine as plain Title Case words.
    assert slug_to_title("chief-of-eng") == "Chief Of Eng"
    assert slug_to_title("staff-engineer") == "Staff Engineer"
    assert slug_to_title("intern-developer") == "Intern Developer"


def test_c_level_acronyms():
    assert slug_to_title("cto-platform") == "CTO Platform"
    assert slug_to_title("vp-engineering") == "VP Engineering"


def test_individual_contributor_acronym():
    # Speculative-but-defensible: IC (Individual Contributor) is a heavily
    # used career-track label. Listed under `_SPECULATIVE_ACRONYMS` so its
    # addition is explicit (and the corpus can later promote it to
    # data-driven via `_ACRONYMS_UPPER`).
    assert slug_to_title("ic-engineer") == "IC Engineer"
    assert slug_to_title("staff-ic-platform") == "Staff IC Platform"


def test_speculative_acronym_set_governance():
    # `_SPECULATIVE_ACRONYMS` is kept separate from `_ACRONYMS_UPPER`
    # precisely so additions here are visible in code review and force
    # an explicit decision (keep speculative OR move to data-driven).
    # Pinning the set's contents in a test makes any addition surface as
    # a failed assertion — reviewers see the new token and decide.
    # Path-to-data: re-run `scripts/audit_slug_acronyms.py` against the
    # real corpus (or extend it to a new platform) to gather evidence
    # the token is used widely enough to promote.
    assert get_speculative_acronyms() == frozenset({"ic"}), (
        f"_SPECULATIVE_ACRONYMS changed; pick is now: "
        f"{sorted(get_speculative_acronyms())}. Either revert, or move "
        f"it to `_ACRONYMS_UPPER` if the addition is data-driven (run "
        f"`python3 scripts/audit_slug_acronyms.py` to confirm empirical "
        f"coverage). Keeping it here means reviewers see this assert fail."
    )


def test_get_speculative_acronyms_returns_frozenset():
    # `get_speculative_acronyms` must return a `frozenset` (immutable).
    # Caller-side mutation safety hinges on this: if the helper ever
    # returned a regular `set`, `.add()` / `.update()` calls would either
    # mutate the snapshot alone (less harmful) or, if CPython's
    # frozenset interning caches the object identity, leak into
    # `_SPECULATIVE_ACRONYMS` itself.
    #
    # We deliberately do NOT assert `snap1 is not snap2`: CPython
    # interns single-element frozensets with the same hashable value
    # (verified empirically) so two consecutive `frozenset(<existing>)`
    # calls can return the same object — that's a CPython optimisation,
    # not a contract violation. The contract is "returns a frozenset
    # with the right contents", not "returns a fresh object per call".
    snap = get_speculative_acronyms()
    assert isinstance(snap, frozenset)
    assert snap == frozenset({"ic"})


def test_uuid_strip_pipeline_round_trip():
    # Lock in the post-uuid-strip pipeline. Real Greenhouse / Lever /
    # Ashby hrefs flag role titles with a leading uuid/hex prefix
    # (`<uuid>-platform-engineer`); `text_utils.strip_uuid_prefix` is the
    # single source of truth shared with `scripts/audit_slug_acronyms.py`.
    # A future change to either piece surfaces here as a regression.
    for raw, expected in [
        ("a1b2c3d4-platform-engineer",    "Platform Engineer"),
        ("deadbeef01-senior-ml-engineer", "Senior ML Engineer"),
    ]:
        stripped = strip_uuid_prefix(raw.lower())
        assert slug_to_title(stripped) == expected, raw


def test_empty_returns_empty():
    assert slug_to_title("") == ""


def test_dashes_only_returns_empty():
    assert slug_to_title("---") == ""


def test_consecutive_hyphens_filter_empties():
    # Redundant hyphens are silently collapsed — non-alphanumeric tokens
    # would otherwise produce "- " decorations in the title.
    assert slug_to_title("senior---ml--engineer") == "Senior ML Engineer"


def test_leading_and_trailing_hyphens_filtered():
    assert slug_to_title("-engineer-") == "Engineer"


def test_digit_segments_pass_through_capitalize():
    # "2025".capitalize() is "2025" — digits pass through unchanged.
    assert slug_to_title("engineer-q1-2025") == "Engineer Q1 2025"


def test_uppercase_input_normalized():
    # Acronym matching is case-insensitive at the lookup level; trailing
    # letters outside the acronym set still get capitalized.
    assert slug_to_title("ML-Engineer") == "ML Engineer"
    assert slug_to_title("MLOps-Engineer") == "MLOps Engineer"


def test_real_world_greenhouse_slugs():
    """Spot-check a few real-looking career-page slugs that motivated the
    acronym table in the first place."""
    assert slug_to_title("ciso-2025") == "CISO 2025"
    assert slug_to_title("frontend-react-engineer") == "Frontend React Engineer"
    assert slug_to_title("sre-platform") == "SRE Platform"
    assert slug_to_title("ai-research-engineer") == "AI Research Engineer"


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
