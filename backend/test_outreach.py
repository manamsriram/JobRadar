"""Outreach tests: pure functions and stubbed I/O — no network, no credits."""
import asyncio
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp())
sys.path.insert(0, os.path.dirname(__file__))

import outreach  # noqa: E402

NOW = datetime(2026, 8, 30, tzinfo=timezone.utc)


def _entry(verified=0, failed=0, age_days=1):
    return {
        "verified": verified,
        "failed": failed,
        "last_verified_at": (NOW - timedelta(days=age_days)).isoformat(),
    }


def test_parse_name():
    assert outreach.parse_name("Jane Doe") == ("jane", "doe")
    assert outreach.parse_name("Mary-Jane Van Doe") == ("mary-jane", "doe")
    assert outreach.parse_name("Jane Doe, MBA") == ("jane", "doe")
    assert outreach.parse_name("José Álvarez") == ("josé", "álvarez")
    assert outreach.parse_name("Cher") is None          # single token
    assert outreach.parse_name("J. Doe") is None        # initials
    assert outreach.parse_name("") is None


def test_generate_guesses_permutations():
    guesses = outreach.generate_guesses("Mary-Jane Doe", "acme.com")
    assert {g["email"] for g in guesses} == {
        "maryjane.doe@acme.com", "maryjane@acme.com", "mdoe@acme.com",
        "maryjanedoe@acme.com", "m.doe@acme.com", "maryjane_doe@acme.com",
        "doe.maryjane@acme.com",
    }
    assert outreach.generate_guesses("Cher", "acme.com") == []
    assert outreach.generate_guesses("Jane Doe", "") == []


def test_generate_guesses_folds_accents():
    emails = {g["email"] for g in outreach.generate_guesses("José Álvarez", "acme.com")}
    assert "jose.alvarez@acme.com" in emails


def test_pattern_status():
    assert outreach.pattern_status(None) == "unknown"
    assert outreach.pattern_status(_entry(verified=1), now=NOW) == "probable"
    assert outreach.pattern_status(_entry(verified=2), now=NOW) == "verified"
    # accept-all caps at probable — a yes to everything proves nothing.
    assert outreach.pattern_status(_entry(verified=5), accept_all=True, now=NOW) == "probable"
    # more failures than successes ranks below no evidence at all.
    assert outreach.pattern_status(_entry(verified=1, failed=2), now=NOW) == "unknown"
    # staleness decay on read.
    assert outreach.pattern_status(_entry(verified=3, age_days=200), now=NOW) == "probable"


def test_rank_guesses_verified_pattern_wins():
    memory = {"patterns": {"flast": _entry(verified=2)}}
    ranked = outreach.rank_guesses(
        outreach.generate_guesses("Jane Doe", "acme.com"), memory, now=NOW)
    assert ranked[0]["email"] == "jdoe@acme.com"
    assert "matched_verified_domain_pattern" in ranked[0]["reasons"]


def test_rank_guesses_demotes_failed_pattern():
    memory = {"patterns": {"first.last": _entry(failed=2)}}
    ranked = outreach.rank_guesses(
        outreach.generate_guesses("Jane Doe", "acme.com"), memory, now=NOW)
    failed = next(g for g in ranked if g["pattern"] == "first.last")
    assert "pattern_failed_at_domain" in failed["reasons"]
    assert ranked[-1]["pattern"] == "first.last"


def test_rank_guesses_falls_back_to_frequency_order():
    ranked = outreach.rank_guesses(outreach.generate_guesses("Jane Doe", "acme.com"), {}, now=NOW)
    assert [g["pattern"] for g in ranked] == outreach.PATTERN_ORDER
    assert all(g["status"] == "unknown" for g in ranked)


def test_rank_contacts_prefers_recruiters_and_company_source():
    ranked = outreach.rank_contacts([
        {"name": "Ed Manager", "title": "Engineering Manager", "source_type": "linkedin"},
        {"name": "Rae Cruiter", "title": "Technical Recruiter", "source_type": "linkedin"},
        {"name": "Perry Ops", "title": "People Ops Lead", "source_type": "company_site"},
    ], now=NOW)
    assert [c["name"] for c in ranked] == ["Rae Cruiter", "Perry Ops", "Ed Manager"]
    assert "high_recruiter_title_relevance" in ranked[0]["reasons"]


def test_rank_contacts_flags_unparseable_name():
    ranked = outreach.rank_contacts([{"name": "Cher", "title": "Recruiter"}], now=NOW)
    assert "name_unparseable" in ranked[0]["reasons"]


def test_recruiter_queries_fit_google_word_cap():
    from scrapers.board_scraper import GOOGLE_MAX_QUERY_TOKENS
    queries = outreach.build_recruiter_queries("Acme Robotics", "acme.com")
    assert queries
    assert all(len(q.split()) <= GOOGLE_MAX_QUERY_TOKENS for q in queries)
    # No domain: the site: template drops out rather than emitting "site: ".
    assert all("site:" not in q or "linkedin" in q
               for q in outreach.build_recruiter_queries("Acme", ""))
    assert outreach.build_recruiter_queries("", "acme.com") == []


def test_parse_candidate_title():
    assert outreach.parse_candidate_title(
        "Jane Doe - Technical Recruiter - Acme | LinkedIn") == ("Jane Doe", "Technical Recruiter")
    assert outreach.parse_candidate_title("Careers at Acme")[0] is None


def test_parse_recruiter_serp_keeps_only_profiles_and_company_pages():
    html = """
    <a href="https://www.linkedin.com/in/janedoe">Jane Doe - Recruiter - Acme | LinkedIn</a>
    <a href="https://acme.com/team">Rae Cruiter - Head of Talent</a>
    <a href="https://randomblog.com/x">Jane Doe - Recruiter</a>
    <a href="https://www.linkedin.com/in/nobody">Careers at Acme</a>
    """
    found = outreach._parse_recruiter_serp(html, "acme.com", "Acme")
    assert [c["source_type"] for c in found] == ["linkedin", "company_site"]
    assert found[0]["origin"] == "search"


def test_parse_recruiter_serp_drops_linkedin_profiles_at_other_companies():
    # A recruiter-shaped name whose LinkedIn title names a different (or former)
    # employer must not be surfaced as an Acme contact.
    html = """
    <a href="https://www.linkedin.com/in/janedoe">Jane Doe - Recruiter - Acme Robotics | LinkedIn</a>
    <a href="https://www.linkedin.com/in/johnroe">John Roe - Recruiter - Widgets Co | LinkedIn</a>
    """
    found = outreach._parse_recruiter_serp(html, "acme.com", "Acme")
    assert [c["source_url"] for c in found] == ["https://www.linkedin.com/in/janedoe"]


def test_build_report_pairs_candidates_with_guesses():
    report = outreach.build_report(
        "acme.com",
        [{"name": "Jane Doe", "title": "Recruiter", "source_type": "linkedin"}],
        patterns={"acme.com": {"patterns": {"flast": _entry(verified=2)}}},
        now=NOW,
    )
    assert report["domain"] == "acme.com"
    assert report["candidates"][0]["guesses"][0]["email"] == "jdoe@acme.com"


# ---- Phase 2: verification, pattern learning, auto-promotion ----
def test_learn_pattern_accumulates_and_flags_accept_all():
    patterns = outreach.learn_pattern("acme.com", "first.last", True, patterns={}, now=NOW)
    assert patterns["acme.com"]["patterns"]["first.last"]["verified"] == 1
    patterns = outreach.learn_pattern("acme.com", "first.last", True, patterns=patterns, now=NOW)
    assert outreach.pattern_status(
        patterns["acme.com"]["patterns"]["first.last"], now=NOW) == "verified"
    patterns = outreach.learn_pattern("acme.com", "flast", False, accept_all=True,
                                      patterns=patterns, now=NOW)
    assert patterns["acme.com"]["accept_all"] is True
    assert patterns["acme.com"]["patterns"]["flast"]["failed"] == 1
    # accept_all caps the whole domain, including the already-verified pattern.
    memory = patterns["acme.com"]
    assert outreach.pattern_status(
        memory["patterns"]["first.last"], memory["accept_all"], now=NOW) == "probable"


CONTACT = {"name": "Jane Doe", "title": "Recruiter", "source_url": "https://x.test/1"}


def _memory(verified=2, failed=0, accept_all=False, age_days=1, last_failed=None):
    entry = _entry(verified, failed, age_days)
    if last_failed:
        entry["last_failed_at"] = last_failed.isoformat()
    return {"patterns": {"first.last": entry}, "accept_all": accept_all}


def _top_guess():
    return outreach.generate_guesses("Jane Doe", "acme.com")[0]


def test_auto_promotion_on_verified_pattern():
    result = outreach.send_eligibility(_top_guess(), CONTACT, _memory(), now=NOW)
    assert result == {"eligible": True, "basis": "auto_promoted_pattern", "blockers": []}


def test_probable_pattern_does_not_auto_promote():
    result = outreach.send_eligibility(_top_guess(), CONTACT, _memory(verified=1), now=NOW)
    assert not result["eligible"] and "pattern_not_verified" in result["blockers"]


def test_accept_all_domain_never_auto_promotes():
    result = outreach.send_eligibility(_top_guess(), CONTACT,
                                       _memory(verified=5, accept_all=True), now=NOW)
    assert not result["eligible"] and "pattern_not_verified" in result["blockers"]


def test_stale_pattern_stops_auto_promoting():
    result = outreach.send_eligibility(_top_guess(), CONTACT,
                                       _memory(age_days=200), now=NOW)
    assert not result["eligible"] and "pattern_not_verified" in result["blockers"]


def test_pattern_needs_two_clean_successes_after_a_failure():
    # Verified twice, then failed: not promotable until it re-earns the margin.
    memory = _memory(verified=2, failed=1, last_failed=NOW - timedelta(days=1))
    result = outreach.send_eligibility(_top_guess(), CONTACT, memory, now=NOW)
    assert not result["eligible"]
    assert "pattern_failed_since_last_success" in result["blockers"]

    memory = _memory(verified=4, failed=1, last_failed=NOW - timedelta(days=2))
    assert outreach.send_eligibility(_top_guess(), CONTACT, memory, now=NOW)["eligible"]


def test_unparseable_name_falls_back_to_manual_verification():
    result = outreach.send_eligibility(_top_guess(), {"name": "Cher"}, _memory(), now=NOW)
    assert not result["eligible"] and "name_unparseable" in result["blockers"]


def test_verified_address_is_eligible_regardless_of_pattern():
    guess = {**_top_guess(), "verified_status": "valid"}
    assert outreach.send_eligibility(guess, CONTACT, {}, now=NOW)["basis"] == "verified_address"


def test_verify_contact_reuses_a_stored_address_without_spending(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    outreach.store_candidates("acme.com", [CONTACT], guessed=False)
    outreach.store_verified_email("acme.com", CONTACT, "jane.doe@acme.com", "first.last",
                                  {"status": "valid", "score": 95})

    def boom(email):
        raise AssertionError("Hunter must not be called when we already know the address")

    monkeypatch.setattr("enricher.verify_email", boom)
    result = asyncio.run(outreach.verify_contact("acme.com", CONTACT))
    assert result["spent_credit"] is False
    assert result["basis"] == "stored_verified_address"
    assert result["email"] == "jane.doe@acme.com"


def test_stored_address_expires():
    stale = {**CONTACT, "email": "jane.doe@acme.com", "verified_status": "valid",
             "verified_at": (NOW - timedelta(days=400)).isoformat()}
    assert outreach._stale_stored(stale, NOW) is True


def test_verify_contact_learns_and_stores_on_success(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    outreach.store_candidates("acme.com", [CONTACT], guessed=False)

    async def fake_verify(email):
        assert email == "jane.doe@acme.com"
        return {"status": "valid", "score": 92, "accept_all": False}

    monkeypatch.setattr("enricher.verify_email", fake_verify)
    result = asyncio.run(outreach.verify_contact("acme.com", CONTACT))
    assert result["spent_credit"] is True and result["status"] == "valid"
    assert outreach.load_patterns()["acme.com"]["patterns"]["first.last"]["verified"] == 1
    assert outreach.stored_verified_email("acme.com", CONTACT)["email"] == "jane.doe@acme.com"


def test_verify_contact_records_nothing_on_provider_error(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    outreach.store_candidates("acme.com", [CONTACT], guessed=False)

    async def failing(email):
        return {"error": "provider_error"}

    monkeypatch.setattr("enricher.verify_email", failing)
    result = asyncio.run(outreach.verify_contact("acme.com", CONTACT))
    assert result["error"] == "provider_error"
    # A network blip must not teach the engine that a good pattern failed.
    assert outreach.load_patterns() == {}


# ---- Phase 3: log, dedup, send gates ----
def test_dedup_blocks_same_job_and_same_domain_in_cooldown():
    log = [{"id": "j1:a@acme.com", "job_id": "j1", "domain": "acme.com",
            "email": "a@acme.com", "status": "sent",
            "sent_at": (NOW - timedelta(days=3)).isoformat()}]
    assert outreach.find_duplicate("j1", "a@acme.com", log, "acme.com", NOW)
    assert outreach.find_duplicate("j2", "a@acme.com", log, "acme.com", NOW)
    # Outside the cooldown, a different job at the same company is allowed.
    old = [{**log[0], "sent_at": (NOW - timedelta(days=90)).isoformat()}]
    assert outreach.find_duplicate("j2", "a@acme.com", old, "acme.com", NOW) is None
    assert outreach.find_duplicate("j2", "other@acme.com", log, "acme.com", NOW) is None


ELIGIBLE = {"eligible": True, "basis": "auto_promoted_pattern", "blockers": []}
INELIGIBLE = {"eligible": False, "basis": "needs_verification",
              "blockers": ["pattern_not_verified"]}


def test_send_refused_while_the_kill_switch_is_off(monkeypatch):
    monkeypatch.setattr(outreach, "SEND_ENABLED", False)
    gate = outreach.check_send_allowed("j1", "a@acme.com", "acme.com", ELIGIBLE,
                                       log=[], now=NOW)
    assert gate == {"allowed": False, "reason": "sending_disabled"}


def test_send_refused_over_the_daily_cap(monkeypatch):
    monkeypatch.setattr(outreach, "SEND_ENABLED", True)
    monkeypatch.setattr(outreach, "DAILY_SEND_CAP", 2)
    log = [{"id": str(i), "status": "sent", "sent_at": NOW.isoformat()} for i in range(2)]
    gate = outreach.check_send_allowed("j1", "a@acme.com", "acme.com", ELIGIBLE,
                                       log=log, now=NOW)
    assert gate["reason"] == "daily_cap_reached"


def test_send_refused_for_unverified_unless_overridden(monkeypatch):
    monkeypatch.setattr(outreach, "SEND_ENABLED", True)
    gate = outreach.check_send_allowed("j1", "a@acme.com", "acme.com", INELIGIBLE,
                                       log=[], now=NOW)
    assert gate["reason"] == "unverified_address"
    over = outreach.check_send_allowed("j1", "a@acme.com", "acme.com", INELIGIBLE,
                                       override=True, log=[], now=NOW)
    assert over["allowed"] and over["override_used"] is True


def test_override_cannot_defeat_the_cap_or_dedup(monkeypatch):
    monkeypatch.setattr(outreach, "SEND_ENABLED", True)
    log = [{"id": "j1:a@acme.com", "job_id": "j1", "domain": "acme.com",
            "email": "a@acme.com", "status": "sent", "sent_at": NOW.isoformat()}]
    gate = outreach.check_send_allowed("j1", "a@acme.com", "acme.com", INELIGIBLE,
                                       override=True, log=log, now=NOW)
    assert gate["reason"] == "duplicate" and gate["prior"]["id"] == "j1:a@acme.com"


def test_record_and_mark_replied_round_trip(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    record = outreach.record_outreach({"job_id": "j1", "email": "a@acme.com",
                                       "domain": "acme.com", "status": "sent",
                                       "sent_at": NOW.isoformat(), "message_id": "<m1>"})
    assert outreach.sent_today(now=NOW) == 1
    updated = outreach.mark_replied(record["id"], now=NOW)
    assert updated["status"] == "replied" and updated["label"] == outreach.LABEL_REPLIED
    assert outreach.mark_replied("nope") is None


def _isolate(monkeypatch, tmp_path):
    """Point every outreach file at a fresh temp dir for one test."""
    import enricher
    monkeypatch.setattr(outreach, "PATTERNS_FILE", tmp_path / "outreach_patterns.json")
    monkeypatch.setattr(outreach, "LOG_FILE", tmp_path / "outreach_log.json")
    monkeypatch.setattr(enricher, "CONTACTS_CACHE_FILE", tmp_path / "company_contacts.json")
