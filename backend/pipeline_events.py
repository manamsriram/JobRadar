"""In-process subscriber registry for pipeline transition side effects. No
queue/broker: a missed notification isn't data loss since the ledger row is
already durably committed by the time subscribers run.
"""
from typing import Awaitable, Callable

Subscriber = Callable[[dict, dict], Awaitable[None]]

SUBSCRIBERS: list[Subscriber] = []


def register(subscriber: Subscriber) -> None:
    SUBSCRIBERS.append(subscriber)


async def notify(application: dict, event: dict) -> None:
    """Invoked synchronously right after a transition commits. A subscriber
    raising doesn't roll back the (already-committed) DB write — it's a best
    effort notification, not part of the transaction."""
    for subscriber in SUBSCRIBERS:
        try:
            await subscriber(application, event)
        except Exception as e:
            print(f"[pipeline_events] subscriber error: {e}")
