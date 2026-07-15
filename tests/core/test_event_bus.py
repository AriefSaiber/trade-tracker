"""In-process async event bus (pub/sub). Tests drive the coroutine API via
asyncio.run() so they don't depend on any pytest-asyncio configuration."""
from __future__ import annotations

import asyncio

import pytest

from backend.core.event_bus import (
    TOPIC_ALERT,
    TOPIC_BAR,
    TOPIC_FILL,
    TOPIC_ORDER_EVENT,
    TOPIC_REGIME,
    TOPIC_SIGNAL_RAW,
    TOPIC_SIGNAL_REJECTED,
    TOPIC_SIGNAL_VALIDATED,
    EventBus,
)


def test_publish_delivers_to_subscriber():
    bus = EventBus()
    received: list[int] = []

    async def handler(payload):
        received.append(payload)

    bus.subscribe(TOPIC_BAR, handler)
    asyncio.run(bus.publish(TOPIC_BAR, 7))
    assert received == [7]


def test_publish_fans_out_to_all_subscribers():
    bus = EventBus()
    a: list[str] = []
    b: list[str] = []

    async def ha(p):
        a.append(p)

    async def hb(p):
        b.append(p)

    bus.subscribe(TOPIC_SIGNAL_RAW, ha)
    bus.subscribe(TOPIC_SIGNAL_RAW, hb)
    assert bus.subscriber_count(TOPIC_SIGNAL_RAW) == 2

    asyncio.run(bus.publish(TOPIC_SIGNAL_RAW, "x"))
    assert a == ["x"] and b == ["x"]


def test_publish_with_no_subscribers_is_noop():
    bus = EventBus()
    # must not raise even though nobody is listening
    asyncio.run(bus.publish("nobody.listening", object()))


def test_topics_are_isolated():
    bus = EventBus()
    got: list[str] = []

    async def handler(p):
        got.append(p)

    bus.subscribe(TOPIC_FILL, handler)
    asyncio.run(bus.publish(TOPIC_REGIME, "regime-payload"))
    assert got == []  # different topic, no delivery


def test_handler_exception_propagates_fail_flat():
    """A failing handler in the execution path must surface, not be swallowed."""
    bus = EventBus()

    async def boom(_):
        raise ValueError("handler failed")

    bus.subscribe(TOPIC_ORDER_EVENT, boom)
    with pytest.raises(ValueError, match="handler failed"):
        asyncio.run(bus.publish(TOPIC_ORDER_EVENT, {"id": 1}))


def test_unsubscribe_stops_delivery():
    bus = EventBus()
    seen: list[int] = []

    async def handler(p):
        seen.append(p)

    bus.subscribe(TOPIC_ALERT, handler)
    bus.unsubscribe(TOPIC_ALERT, handler)
    assert bus.subscriber_count(TOPIC_ALERT) == 0
    asyncio.run(bus.publish(TOPIC_ALERT, 1))
    assert seen == []


def test_well_known_topics_are_distinct():
    topics = [
        TOPIC_BAR,
        TOPIC_SIGNAL_RAW,
        TOPIC_SIGNAL_VALIDATED,
        TOPIC_SIGNAL_REJECTED,
        TOPIC_ORDER_EVENT,
        TOPIC_FILL,
        TOPIC_REGIME,
        TOPIC_ALERT,
    ]
    assert len(set(topics)) == len(topics)
