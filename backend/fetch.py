"""Shared HTTP GET with retry: exponential backoff + jitter, transient
failures only. Findings-doc #2 — only retry connection/timeout/5xx, never
4xx (permanent) or exhausted retries; a per-cycle RetryBudget caps total
retries so a network-wide blip can't stack into an overrun poll cycle
(senior-pass guardrail)."""
import asyncio
import random

import httpx

_RETRYABLE_STATUS = {500, 502, 503, 504}


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


async def fetch_with_retry(
    client: httpx.AsyncClient, url: str, *, retries: int = 2, timeout: float = 15
) -> httpx.Response:
    attempt = 0
    while True:
        try:
            r = await client.get(url, timeout=timeout, follow_redirects=True)
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
