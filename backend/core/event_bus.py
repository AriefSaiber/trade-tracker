"""In-process async event bus (pub/sub).

One bus instance is shared by backtest, paper, and live runtimes so the
decision path (data -> signal -> validation -> risk -> order) is a single
code path. Redis pub/sub mirrors selected topics for the dashboard.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any, Awaitable, Callable

import structlog

log = structlog.get_logger(__name__)

Handler = Callable[[Any], Awaitable[None]]


class EventBus:
    def __init__(self) -> None:
        self._subscribers: dict[str, list[Handler]] = defaultdict(list)

    def subscribe(self, topic: str, handler: Handler) -> None:
        self._subscribers[topic].append(handler)

    def unsubscribe(self, topic: str, handler: Handler) -> None:
        handlers = self._subscribers.get(topic)
        if handlers and handler in handlers:
            handlers.remove(handler)

    def subscriber_count(self, topic: str) -> int:
        return len(self._subscribers.get(topic, []))

    async def publish(self, topic: str, payload: Any) -> None:
        handlers = list(self._subscribers.get(topic, []))
        if not handlers:
            return
        results = await asyncio.gather(
            *(h(payload) for h in handlers), return_exceptions=True
        )
        for handler, result in zip(handlers, results):
            if isinstance(result, Exception):
                # Fail flat: an unhandled error in the execution path must
                # surface, not be swallowed.
                log.error(
                    "event_handler_error",
                    topic=topic,
                    handler=getattr(handler, "__qualname__", str(handler)),
                    error=str(result),
                )
                raise result


# Well-known topics
TOPIC_BAR = "bar"
TOPIC_SIGNAL_RAW = "signal.raw"
TOPIC_SIGNAL_VALIDATED = "signal.validated"
TOPIC_SIGNAL_REJECTED = "signal.rejected"
TOPIC_ORDER_APPROVED = "order.approved"
TOPIC_ORDER_EVENT = "order.event"
TOPIC_FILL = "fill"
TOPIC_REGIME = "regime"
TOPIC_ALERT = "alert"
TOPIC_HEARTBEAT = "heartbeat"
