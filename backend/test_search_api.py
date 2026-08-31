"""search_api tests — parsers and backend-ladder logic, no live network."""
import asyncio
import os
import sys
import tempfile

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp())
sys.path.insert(0, os.path.dirname(__file__))

import search_api  # noqa: E402

DDG_HTML = """
<div class="result">
  <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fboards.greenhouse.io%2Facme%2Fjobs%2F1">
    Software Engineer, New Grad
  </a>
  <a class="result__snippet">Acme is hiring new grads.</a>
</div>
<div class="result">
  <a class="result__a" href="https://jobs.lever.co/acme/2">Backend Engineer</a>
</div>
<a href="https://duckduckgo.com/settings">Settings</a>
"""


def test_parse_ddg_html_unwraps_redirects():
    results = search_api.parse_ddg_html(DDG_HTML)
    assert [r["url"] for r in results] == [
        "https://boards.greenhouse.io/acme/jobs/1",
        "https://jobs.lever.co/acme/2",
    ]
    assert results[0]["title"] == "Software Engineer, New Grad"
    assert results[0]["snippet"] == "Acme is hiring new grads."


def test_parse_ddg_html_empty():
    assert search_api.parse_ddg_html("") == []


def test_parse_serp_anchors_skips_google_and_short_links():
    html = """
    <a href="https://jobs.lever.co/acme/1">Backend Engineer</a>
    <a href="https://www.google.com/preferences">Settings page</a>
    <a href="https://jobs.lever.co/acme/2">go</a>
    <a href="/relative">Relative link text</a>
    """
    results = search_api.parse_serp_anchors(html)
    # google.com links dropped, sub-5-char text dropped, relative resolved then
    # dropped as a google.com URL.
    assert [r["url"] for r in results] == ["https://jobs.lever.co/acme/1"]


def _run(query="q", days=1):
    return asyncio.run(search_api.search(query, days))


def _with_backends(monkeypatch_map, order):
    search_api._BACKENDS = dict(monkeypatch_map)
    search_api.SEARCH_BACKEND = ""
    search_api.backend_order = lambda: list(order)


def test_search_falls_through_to_next_backend_on_block(request):
    async def blocked(q, d):
        raise search_api.Blocked("nope")

    async def works(q, d):
        return [{"title": "t", "url": "https://x.test/1", "snippet": ""}]

    original = dict(search_api._BACKENDS), search_api.backend_order
    request.addfinalizer(lambda: _restore(original))
    _with_backends({"a": blocked, "b": works}, ["a", "b"])
    results, ok, is_blocked = _run()
    assert ok and not is_blocked and len(results) == 1


def test_search_reports_blocked_when_every_backend_is_walled(request):
    async def blocked(q, d):
        raise search_api.Blocked("nope")

    original = dict(search_api._BACKENDS), search_api.backend_order
    request.addfinalizer(lambda: _restore(original))
    _with_backends({"a": blocked, "b": blocked}, ["a", "b"])
    results, ok, is_blocked = _run()
    assert results == [] and not ok and is_blocked


def test_search_empty_result_is_success_not_a_block(request):
    async def empty(q, d):
        return []

    original = dict(search_api._BACKENDS), search_api.backend_order
    request.addfinalizer(lambda: _restore(original))
    _with_backends({"a": empty}, ["a"])
    results, ok, is_blocked = _run()
    assert results == [] and ok and not is_blocked


def test_search_survives_a_backend_raising(request):
    async def boom(q, d):
        raise RuntimeError("network gone")

    async def works(q, d):
        return [{"title": "t", "url": "https://x.test/1", "snippet": ""}]

    original = dict(search_api._BACKENDS), search_api.backend_order
    request.addfinalizer(lambda: _restore(original))
    _with_backends({"a": boom, "b": works}, ["a", "b"])
    assert _run()[1] is True


def _restore(original):
    backends, order_fn = original
    search_api._BACKENDS = backends
    search_api.backend_order = order_fn


def test_backend_order_prefers_serper_only_with_a_key(monkeypatch):
    monkeypatch.setattr(search_api, "SEARCH_BACKEND", "")
    monkeypatch.setattr(search_api, "SERPER_API_KEY", "")
    assert search_api.backend_order() == ["duckduckgo"]
    monkeypatch.setattr(search_api, "SERPER_API_KEY", "k")
    assert search_api.backend_order() == ["serper", "duckduckgo"]
    monkeypatch.setattr(search_api, "SEARCH_BACKEND", "playwright")
    assert search_api.backend_order() == ["playwright"]


def test_serper_maps_organic_results(monkeypatch):
    import httpx

    payload = {"organic": [
        {"title": "SWE", "link": "https://jobs.lever.co/acme/1", "snippet": "s"},
        {"title": "no link"},
    ]}

    def handler(request):
        assert request.headers["X-API-KEY"] == "k"
        return httpx.Response(200, json=payload)

    monkeypatch.setattr(search_api, "SERPER_API_KEY", "k")
    _patch_transport(monkeypatch, handler)
    results = asyncio.run(search_api._serper("q", 1))
    assert results == [{"title": "SWE", "url": "https://jobs.lever.co/acme/1", "snippet": "s"}]


def test_serper_out_of_credits_is_a_block(monkeypatch):
    import httpx
    import pytest

    _patch_transport(monkeypatch, lambda request: httpx.Response(429))
    with pytest.raises(search_api.Blocked):
        asyncio.run(search_api._serper("q", 1))


def test_duckduckgo_throttle_is_a_block(monkeypatch):
    import httpx
    import pytest

    _patch_transport(monkeypatch, lambda request: httpx.Response(202, text=""))
    with pytest.raises(search_api.Blocked):
        asyncio.run(search_api._duckduckgo("q", 1))


def _patch_transport(monkeypatch, handler):
    """Route every AsyncClient through a MockTransport so no request leaves."""
    import httpx

    original = httpx.AsyncClient.__init__

    def init(self, *args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        original(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", init)


def test_serper_requests_at_most_ten_results(monkeypatch):
    """Serper's free tier answers num > 10 with a 400, which would demote every
    search to DuckDuckGo without saying why."""
    import json

    import httpx

    seen = {}

    def handler(request):
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"organic": []})

    _patch_transport(monkeypatch, handler)
    asyncio.run(search_api._serper("q", 1))
    assert seen["num"] <= 10


def test_serper_rejected_query_carries_the_reason(monkeypatch):
    import httpx
    import pytest

    _patch_transport(monkeypatch, lambda request: httpx.Response(
        400, json={"message": "Query pattern not allowed for free accounts."}))
    with pytest.raises(RuntimeError, match="not allowed for free accounts"):
        asyncio.run(search_api._serper("q", 1))
