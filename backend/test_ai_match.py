"""ai_match tests: degrade paths + verdict parsing.

Run: pytest backend/test_ai_match.py
"""
import asyncio

import httpx
import pytest

import ai_match
import state


def _run(coro):
    return asyncio.run(coro)


class _Resp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=self)


def _mock_post(content: str):
    async def post(self, url, json=None, headers=None):
        return _Resp(200, {"choices": [{"message": {"content": content}}]})
    return post


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "DATA_DIR", tmp_path)
    monkeypatch.setattr(ai_match, "DAILY_BUDGET_FILE", tmp_path / "ai_budget.json")
    (tmp_path / "resume_backend.txt").write_text("backend resume text")
    (tmp_path / "resume_frontend.txt").write_text("frontend resume text")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("OPENROUTER_MODEL", "meta-llama/llama-3.1-8b-instruct:free")


def test_review_returns_none_without_api_key(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert _run(ai_match.review({"title": "SWE", "description": "x"})) is None


def test_review_refuses_non_free_model(monkeypatch):
    monkeypatch.setenv("OPENROUTER_MODEL", "openai/gpt-4o")
    assert _run(ai_match.review({"title": "SWE", "description": "x"})) is None


def test_review_returns_none_without_resumes(tmp_path, monkeypatch):
    (tmp_path / "resume_backend.txt").unlink()
    (tmp_path / "resume_frontend.txt").unlink()
    assert _run(ai_match.review({"title": "SWE", "description": "x"})) is None


def test_review_parses_match_verdict(monkeypatch):
    content = '{"verdict": "match", "resume": "backend", "score": 88, "reason": "good fit"}'
    monkeypatch.setattr(httpx.AsyncClient, "post", _mock_post(content))
    result = _run(ai_match.review({"title": "SWE", "description": "x"}))
    assert result == {"verdict": "match", "resume": "backend", "score": 88, "reason": "good fit"}


def test_review_parses_reject_verdict(monkeypatch):
    content = '{"verdict": "reject", "resume": null, "score": 10, "reason": "needs 5 yrs"}'
    monkeypatch.setattr(httpx.AsyncClient, "post", _mock_post(content))
    result = _run(ai_match.review({"title": "SWE", "description": "x"}))
    assert result["verdict"] == "reject"


def test_review_returns_none_on_malformed_json(monkeypatch):
    monkeypatch.setattr(httpx.AsyncClient, "post", _mock_post("not json"))
    assert _run(ai_match.review({"title": "SWE", "description": "x"})) is None


def test_review_returns_none_when_daily_cap_hit(monkeypatch):
    monkeypatch.setattr(ai_match, "DAILY_CALL_CAP", 0)
    content = '{"verdict": "match", "resume": "backend", "score": 88, "reason": "good fit"}'
    monkeypatch.setattr(httpx.AsyncClient, "post", _mock_post(content))
    assert _run(ai_match.review({"title": "SWE", "description": "x"})) is None


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
