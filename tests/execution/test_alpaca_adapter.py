"""AlpacaAdapter tests — every Alpaca REST/websocket call and Redis access is
mocked, so nothing here touches the network."""
import asyncio
import json
from datetime import datetime, timezone

import httpx
import pytest

from backend.core.config import Settings
from backend.core.events import Order, OrderSide, OrderStatus, OrderType
from backend.execution.alpaca_adapter import AlpacaAdapter


def make_order(coid: str = "coid-1", exit: bool = False) -> Order:
    return Order(
        client_order_id=coid,
        strategy_id="trend_pullback",
        symbol="NVDA",
        side=OrderSide.BUY,
        qty=10,
        order_type=OrderType.MARKET,
        limit_price=None,
        stop_loss=95.0,
        take_profit=110.0,
        time_in_force="day",
        created_at=datetime(2026, 7, 10, tzinfo=timezone.utc),
        metadata={"exit": True} if exit else {},
    )


class FakeRedis:
    """Minimal async subset used by the adapter: get / set(ex=...)."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, ex=None):
        self.store[key] = value


def make_adapter(handler, redis=None) -> AlpacaAdapter:
    return AlpacaAdapter(
        Settings(),
        redis_client=redis or FakeRedis(),
        transport=httpx.MockTransport(handler),
    )


# ── submission ─────────────────────────────────────────────────────────────
def test_submit_order_places_bracket_and_returns_ack():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/v2/orders"
        captured["payload"] = json.loads(request.content)
        return httpx.Response(200, json={"id": "brk-1", "status": "new"})

    adapter = make_adapter(handler)
    ack = asyncio.run(adapter.submit_order(make_order()))

    assert ack.broker_order_id == "brk-1"
    assert ack.status == OrderStatus.SUBMITTED
    assert ack.client_order_id == "coid-1"
    # entry orders go out as bracket with the mandatory stop attached
    assert captured["payload"]["order_class"] == "bracket"
    assert captured["payload"]["stop_loss"]["stop_price"] == "95.0"
    assert captured["payload"]["client_order_id"] == "coid-1"


def test_submit_order_idempotent_via_redis():
    """A retry with the same client_order_id must NOT POST a second order."""
    posts: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        posts.append(json.loads(request.content))
        return httpx.Response(200, json={"id": "brk-1", "status": "new"})

    redis = FakeRedis()
    adapter = make_adapter(handler, redis=redis)

    async def run():
        ack1 = await adapter.submit_order(make_order("dup-1"))
        ack2 = await adapter.submit_order(make_order("dup-1"))  # retry
        return ack1, ack2

    ack1, ack2 = asyncio.run(run())
    assert len(posts) == 1                       # only the first hit the broker
    assert ack1.broker_order_id == ack2.broker_order_id == "brk-1"
    assert "alpaca:idempotency:dup-1" in redis.store


def test_submit_order_rejects_entry_without_stop_loss():
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("must not reach the broker")

    order = make_order()
    order.stop_loss = None  # type: ignore[assignment]
    adapter = make_adapter(handler)
    with pytest.raises(ValueError):
        asyncio.run(adapter.submit_order(order))


def test_duplicate_client_order_id_resolved_via_get():
    """Cold Redis cache: broker returns 422, adapter resolves the existing order."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(422, text="client_order_id must be unique")
        assert request.url.path == "/v2/orders:by_client_order_id"
        return httpx.Response(200, json={"id": "existing-1", "status": "accepted"})

    adapter = make_adapter(handler)
    ack = asyncio.run(adapter.submit_order(make_order()))
    assert ack.broker_order_id == "existing-1"
    assert ack.status == OrderStatus.SUBMITTED


# ── reads / cancel ──────────────────────────────────────────────────────────
def test_get_positions_parses_broker_payload():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v2/positions"
        return httpx.Response(200, json=[
            {"symbol": "NVDA", "qty": "10", "avg_entry_price": "100.5",
             "unrealized_pl": "12.0"},
        ])

    adapter = make_adapter(handler)
    positions = asyncio.run(adapter.get_positions())
    assert len(positions) == 1
    assert positions[0].symbol == "NVDA"
    assert positions[0].qty == 10.0
    assert positions[0].unrealized_pnl == 12.0


def test_cancel_order_resolves_then_deletes():
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        if request.method == "GET":
            return httpx.Response(200, json={"id": "o1", "status": "new"})
        return httpx.Response(204)

    adapter = make_adapter(handler)
    asyncio.run(adapter.cancel_order("coid-1"))
    assert ("GET", "/v2/orders:by_client_order_id") in seen
    assert ("DELETE", "/v2/orders/o1") in seen


# ── websocket trade updates ─────────────────────────────────────────────────
class FakeWS:
    def __init__(self, messages: list[str]) -> None:
        self._messages = messages
        self.sent: list[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def send(self, data):
        self.sent.append(data)

    def __aiter__(self):
        self._it = iter(self._messages)
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration


def test_stream_order_updates_dispatches_mapped_status():
    messages = [
        json.dumps({"stream": "authorization", "data": {"status": "authorized"}}),
        json.dumps({"stream": "trade_updates", "data": {
            "event": "fill",
            "order": {"id": "brk-1", "client_order_id": "coid-1", "status": "filled"},
        }}),
    ]
    ws = FakeWS(messages)
    acks = []

    async def on_update(ack):
        acks.append(ack)

    def make_adapter_only():
        return AlpacaAdapter(Settings(), redis_client=FakeRedis(),
                             transport=httpx.MockTransport(lambda r: httpx.Response(200)))

    adapter = make_adapter_only()
    asyncio.run(adapter.stream_order_updates(
        on_update, connect=lambda url: ws, reconnect=False))

    # auth + listen handshake was sent
    assert len(ws.sent) == 2
    # the non-order authorization frame is ignored; only the fill is dispatched
    assert len(acks) == 1
    assert acks[0].status == OrderStatus.FILLED
    assert acks[0].broker_order_id == "brk-1"
    assert acks[0].client_order_id == "coid-1"
