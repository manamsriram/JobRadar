"""Filter tests: new-grad title matches, seniority excluded, US-only enforced.

Run: pytest backend/test_filter.py  (or python backend/test_filter.py for asserts)
"""
from datetime import datetime, timezone

from filter import matches

_FRESH = datetime.now(timezone.utc).isoformat()


def _job(**kw) -> dict:
    base = {"title": "Software Engineer", "location": "San Jose, CA",
            "posted_at": _FRESH, "description": ""}
    base.update(kw)
    return base


def test_newgrad_title_matches():
    assert matches(_job(title="Software Engineer", location="San Jose, CA"))
    assert matches(_job(title="Backend Engineer", location="Remote, US"))


def test_seniority_excluded():
    assert not matches(_job(title="Senior Software Engineer"))
    assert not matches(_job(title="Staff Software Engineer"))
    assert not matches(_job(title="Software Engineer III"))


def test_non_title_rejected():
    assert not matches(_job(title="Product Manager"))
    assert not matches(_job(title="Sales Representative"))


def test_non_us_location_rejected():
    assert not matches(_job(location="London, UK"))
    assert not matches(_job(location="Remote - Bengaluru, India"))
    assert not matches(_job(location="Toronto, Canada"))


def test_us_and_remote_accepted():
    assert matches(_job(location="Milpitas, CA"))
    assert matches(_job(location="Remote (US)"))
    assert matches(_job(location="Remote"))  # plain remote, no non-US marker


def test_stale_posting_rejected():
    assert not matches(_job(posted_at="2020-01-01T00:00:00+00:00"))


def test_desc_exclude_drops_senior_body():
    assert not matches(_job(description="Requires 8+ years of experience."))


def test_hard_cap_years_rejected():
    assert not matches(_job(description="Requires 2 years of experience."))
    assert not matches(_job(description="2+ years of experience required."))
    assert not matches(_job(title="Software Engineer, 2 years experience"))


# ---- "yrs" / "yr" abbreviation handling ----
# The years-regex trailing alternation was widened to `years?|yrs?` so JDs
# that drop the "ea" vowel+consonant ("minimum 2 yrs", "5+ yrs experience")
# hit the same hard-cap / max-of-numbers rules as the long form.

def test_yrs_abbrev_at_cap_rejected():
    assert not matches(_job(description="2 yrs of experience required."))
    assert not matches(_job(description="2+ yrs experience required."))
    assert not matches(_job(description="minimum 2 yrs experience"))
    assert not matches(_job(title="Software Engineer, 2 yrs experience"))


def test_yrs_abbrev_over_cap_rejected():
    assert not matches(_job(description="5 yrs of experience required."))
    assert not matches(_job(description="1-5 yrs experience preferred."))


def test_yr_singular_abbrev_under_cap_passes():
    # `yr?` matches `yr` (no s); under-cap digit + bare suffix is fine.
    assert matches(_job(description="1 yr experience preferred."))


def test_yrs_abbrev_under_cap_passes():
    # Bare "1 yrs" is grammatically odd but JDs do write it; treat the
    # `yrs?` suffix uniformly. 1 < 2 cap, not at hard-cap.
    assert matches(_job(description="1 yrs experience preferred."))


def test_yrs_abbrev_no_space_still_matches():
    # `\s{0,3}` between digit and suffix allows zero whitespace — some
    # postings typo "2yrs" with no space at all.
    assert not matches(_job(description="2yrs experience required."))


# ---- Closing-parenthesis between digit and "year" (pre-existing gap) ----
# The `_YEARS_RE` tail gaps were widened from \s{0,3} to [\s)]{0,3} so JDs
# that wrap a parenthetical around just the digit ("(2) years" rather than
# "(2 years)") still hit the years filter.

def test_paren_wrap_at_cap_rejected():
    assert not matches(_job(description="(2) years of experience required."))
    assert not matches(_job(description="(2) yrs of experience required."))
    assert not matches(_job(description="(2)+ years of experience required."))


def test_paren_wrap_under_cap_passes():
    assert matches(_job(description="(1) year of experience preferred."))
    assert matches(_job(description="(0) to (2) years of experience."))


def test_paren_wrap_over_cap_rejected():
    assert not matches(_job(description="(5) years of experience required."))
    assert not matches(_job(description="(8+) years of experience."))
    assert not matches(_job(description="(20) years of experience required."))


def test_closing_paren_after_digit_rejected():
    # No opening paren but closing paren between digit and year.
    assert not matches(_job(description="2) years of experience required."))
    assert not matches(_job(description="5) yrs of experience."))


def test_digit_form_paren_gap_still_rejected():
    # Pre-existing "(2) years" blind spot in _YEARS_RE: same gap exists for
    # both digit form (tested here) and spelled form (tested above in
    # test_spelled_out_punctuation_boundary's comment). Locking both.
    assert not matches(_job(description="Requires (2) years of experience."))


def test_range_under_cap_accepted():
    assert matches(_job(description="0-2 years of experience preferred."))
    assert matches(_job(description="1-2 years of experience preferred."))
    assert matches(_job(description="1 year of experience preferred."))


def test_degree_plus_years_rejected():
    assert not matches(_job(description="Master's degree and 2 years experience required."))
    assert not matches(_job(description="PhD with 1 year of experience."))


def test_degree_alone_without_years_accepted():
    assert matches(_job(description="Master's degree required."))


# ---- Spelled-out number mapper ----
# The `_spelled_to_digit` helper inside filter.py expands "two years" /
# "twenty-two years" / "ONe year" to the digit form before the existing
# years regex runs, so JDs that write numbers in English no longer leak
# through the years-of-experience gate.

def test_spelled_out_year_under_cap_passes():
    # Note: "Two years of experience preferred." is intentionally NOT in
    # this pass-set — the hard-cap policy rejects a flat-minimum at the
    # cap whether or not it's framed as "preferred" (see
    # test_hard_cap_years_rejected and test_spelled_out_year_at_cap_rejected).
    assert matches(_job(description="One year of experience preferred."))
    assert matches(_job(description="Six months of experience helpful."))


def test_spelled_out_year_at_cap_rejected():
    # MAX_YEARS_EXPERIENCE defaults to 2 → flat-spelled "two years" is at
    # the cap and must be rejected (matches the existing digit-form test).
    assert not matches(_job(description="Two years of experience required."))
    assert not matches(_job(title="SWE Engineer, two years experience"))


def test_spelled_out_year_over_cap_rejected():
    assert not matches(_job(description="Five years of experience required."))
    assert not matches(_job(description="Twenty years of experience required."))
    assert not matches(_job(description="Twenty-two years of experience required."))
    # Round-number tens and tens+units must also reject — without these
    # entries the mapper would leave "thirty"/"forty-five" unchanged and
    # _YEARS_RE would not fire.
    assert not matches(_job(description="Thirty years experience required."))
    assert not matches(_job(description="Forty-five years experience required."))
    assert not matches(_job(description="Ninety-nine years experience required."))
    # Round-number tens and tens+units must also reject — without these
    # entries the mapper would leave "thirty"/"forty-five" unchanged and
    # _YEARS_RE would not fire.
    assert not matches(_job(description="Thirty years experience required."))
    assert not matches(_job(description="Forty-five years experience required."))
    assert not matches(_job(description="Ninety-nine years experience required."))


def test_spelled_out_capitalization_insensitive():
    # The spelled-out regex uses re.IGNORECASE.
    assert not matches(_job(description="TWO years of experience required."))
    assert not matches(_job(description="Five YeaRS required."))


def test_spelled_out_punctuation_boundary():
    # `\b` in the spelled-out regex must hold at punctuation boundaries —
    # colon-before-word is fine, "two" -> "2" then the digit-form years
    # regex matches "2 years".
    assert not matches(_job(description="Experience: two years required."))
    # Note: "(two) years required" correctly maps to "(2) years required"
    # via the mapper, but the existing _YEARS_RE doesn't tolerate `)`
    # between digit and "year" — that's a separate pre-existing regex gap
    # (digit-form "(2) years required" has the same blind spot) and is
    # outside the spelled-out mapper's scope.


def test_spelled_out_range_under_cap_passes():
    # Both end points at-or-below the cap pass.
    assert matches(_job(description="Zero to two years experience preferred."))
    assert matches(_job(description="One to two years experience preferred."))


def test_spelled_out_range_over_cap_rejected_by_high_end():
    # "one to three" written out — high end (3) exceeds cap (2), so the
    # max-of-spelled-numbers rule rejects even though the low end passes.
    assert not matches(_job(description="One to three years of experience required."))


def test_spelled_out_range_over_cap_rejected():
    # "two to five years" — high end exceeds cap, rejected.
    assert not matches(_job(description="Two to five years of experience required."))


def test_spelled_out_compound_form_parsed_before_prefix():
    # "twenty-two" must be recognized as 22, not as 20 + 2.
    assert not matches(_job(description="Twenty-two years experience required."))
    assert not matches(_job(description="Twenty two years experience required."))


def test_spelled_out_tens_round_numbers_rejected():
    # Round-number tens (30/40/50/60/70/80/90) get caught as single
    # tokens so neither a JD saying "thirty years" nor one saying
    # "seventy years" can sneak past the years filter.
    for w in ("thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"):
        assert not matches(_job(
            description=f"{w.capitalize()} years experience required."
        )), w


def test_number_word_inside_unrelated_word_is_safe():
    # `\b` boundary must prevent "someone" -> "some1" or "one" in the
    # middle of a word from spuriously firing the years regex.
    assert matches(_job(
        description="Talk to someone who knows the codebase. No specific "
                    "years required."
    ))
    # And one without "no specific years" — still fine, just no match.
    assert matches(_job(description="Talk to someone who knows the codebase."))


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("all filter tests passed")
