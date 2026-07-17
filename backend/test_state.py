"""State store tests: pure job-list logic plus atomic JSON read/write.

Run: pytest backend/test_state.py
"""
from datetime import datetime, timedelta, timezone

import pytest

import state


def _now_iso(days_ago: int = 0) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


def test_get_new_jobs_filters_already_seen():
    seen = {"a": {}}
    fetched = [{"id": "a"}, {"id": "b"}]
    assert state.get_new_jobs(seen, fetched) == [{"id": "b"}]


def test_get_new_jobs_skips_entries_without_id():
    fetched = [{"id": ""}, {"title": "no id field"}]
    assert state.get_new_jobs({}, fetched) == []


def test_purge_old_drops_stale_unapplied():
    seen = {"a": {"scraped_at": _now_iso(30)}}
    assert state.purge_old(seen, days=3) == {}


def test_purge_old_keeps_applied_regardless_of_age():
    seen = {"a": {"scraped_at": _now_iso(30), "applied": True}}
    assert state.purge_old(seen, days=3) == seen


def test_purge_old_keeps_jobs_missing_scraped_at():
    seen = {"a": {}}
    assert state.purge_old(seen, days=3) == seen


def test_purge_old_keeps_fresh_jobs():
    seen = {"a": {"scraped_at": _now_iso(0)}}
    assert state.purge_old(seen, days=3) == seen


def test_get_matched_filters_and_sorts_newest_first():
    seen = {
        "a": {"matched": True, "posted_at": _now_iso(2)},
        "b": {"matched": True, "posted_at": _now_iso(0)},
        "c": {"matched": False, "posted_at": _now_iso(1)},
    }
    result = state.get_matched(seen)
    assert [j["posted_at"] for j in result] == [seen["b"]["posted_at"], seen["a"]["posted_at"]]


def test_read_json_missing_file_returns_default(tmp_path):
    assert state._read_json(tmp_path / "missing.json", {"fallback": True}) == {"fallback": True}


def test_write_json_atomic_roundtrip(tmp_path):
    path = tmp_path / "nested" / "data.json"
    state._write_json_atomic(path, {"k": "v"})
    assert state._read_json(path, None) == {"k": "v"}
    assert not path.with_suffix(".json.tmp").exists()


def test_mark_applied_flags_existing_job(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "SEEN_JOBS_FILE", tmp_path / "seen_jobs.json")
    state.save_seen({"a": {"applied": False}})
    assert state.mark_applied("a") is True
    assert state.load_seen()["a"]["applied"] is True


def test_mark_applied_returns_false_for_unknown_job(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "SEEN_JOBS_FILE", tmp_path / "seen_jobs.json")
    state.save_seen({})
    assert state.mark_applied("nope") is False


def test_record_health_resets_streak_on_success():
    health = {"yc": {"consecutive_failures": 3, "status": "failing"}}
    health = state.record_health(health, "yc", ok=True)
    assert health["yc"]["consecutive_failures"] == 0
    assert health["yc"]["status"] == "ok"


def test_record_health_increments_streak_on_failure():
    health = state.record_health({}, "yc", ok=False)
    health = state.record_health(health, "yc", ok=False)
    assert health["yc"]["consecutive_failures"] == 2
    assert health["yc"]["status"] == "failing"


def test_save_seen_creates_bounded_backups(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "SEEN_JOBS_FILE", tmp_path / "seen_jobs.json")
    for i in range(state.BACKUP_KEEP + 2):
        state.save_seen({"a": {"n": i}})
    backups = sorted(tmp_path.glob("seen_jobs.json.*.bak"))
    assert len(backups) <= state.BACKUP_KEEP
    assert state.load_seen() == {"a": {"n": state.BACKUP_KEEP + 1}}


def test_load_company_aliases_defaults_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "COMPANY_ALIASES_FILE", tmp_path / "missing.json")
    assert state.load_company_aliases() == {}


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
