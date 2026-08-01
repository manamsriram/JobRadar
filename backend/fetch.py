"""Shared HTTP GET with retry: exponential backoff + jitter, transient
failures only. Findings-doc #2 — only retry connection/timeout/5xx, never
4xx (permanent) or exhausted retries; a per-cycle RetryBudget caps total
retries so a network-wide blip can't stack into an overrun poll cycle
(senior-pass guardrail)."""
import asyncio
import random

import httpx

from net_safety import is_safe_url

_RETRYABLE_STATUS = {500, 502, 503, 504}
_MAX_REDIRECTS = 5


class RetryBudget:
    """Shared retry allowance for one poll cycle. Each fetch spends from it;
    once exhausted, callers get 0 retries (fail fast) instead of stacking
    more backoff on top of an already-bad cycle."""

    def __init__(self, total: int):
        self.remaining = total

    def take(self, want: int) -> int:
        got = min(want, max(self.remaining, 0))
        self.remaining -= got
        return got


async def _get_validating_redirects(
    client: httpx.AsyncClient, url: str, timeout: float
) -> httpx.Response:
    """GET with redirects followed manually so every hop — not just the
    initial URL — passes is_safe_url. `follow_redirects=True` alone would
    let a "safe" host 302 to a private/loopback/metadata address."""
    if not is_safe_url(url):
        raise httpx.UnsupportedProtocol(f"refusing to fetch unsafe URL: {url}")
    # CodeQL's py/full-ssrf doesn't recognize is_safe_url (DNS-resolves and
    # checks against private/loopback/link-local/reserved ranges in
    # net_safety.py) as a sanitizer — it only models inline urlparse/allowlist
    # idioms, not calls into another module. Guard runs unconditionally above.
    r = await client.get(url, timeout=timeout, follow_redirects=False)  # lgtm[py/full-ssrf]
    hops = 0
    while getattr(r, "next_request", None) is not None:
        if hops >= _MAX_REDIRECTS:
            raise httpx.TooManyRedirects(f"exceeded {_MAX_REDIRECTS} redirects fetching {url}")
        next_url = str(r.next_request.url)
        if not is_safe_url(next_url):
            raise httpx.UnsupportedProtocol(f"refusing to follow unsafe redirect: {next_url}")
        r = await client.send(r.next_request, follow_redirects=False)  # lgtm[py/full-ssrf]
        hops += 1
    return r


async def fetch_with_retry(
    client: httpx.AsyncClient, url: str, *, retries: int = 2, timeout: float = 15
) -> httpx.Response:
    attempt = 0
    while True:
        try:
            r = await _get_validating_redirects(client, url, timeout)
            if r.status_code in _RETRYABLE_STATUS and attempt < retries:
                raise httpx.HTTPStatusError("retryable status", request=r.request, response=r)
            r.raise_for_status()
            return r
        except httpx.HTTPStatusError as e:
            if e.response.status_code not in _RETRYABLE_STATUS or attempt >= retries:
                raise
        except (httpx.TimeoutException, httpx.ConnectError):
            if attempt >= retries:
                raise
        await asyncio.sleep((2**attempt) + random.uniform(0, 1))
        attempt += 1
