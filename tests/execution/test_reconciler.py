"""Reconciler tests: broker truth vs local portfolio, alert + block on mismatch."""
import asyncio

from backend.core.config import YamlConfig
from backend.core.event_bus import TOPIC_ALERT, EventBus
from backend.core.events import Position
from backend.execution.broker_adapter import BrokerAdapter
from backend.execution.reconciler import Reconciler, diff_positions


class FakeBroker(BrokerAdapter):
    def __init__(self, positions: list[Position]) -> None:
        self.positions = positions

    async def submit_order(self, order):  # pragma: no cover - unused here
        raise NotImplementedError

    async def cancel_order(self, client_order_id):  # pragma: no cover
        raise NotImplementedError

    async def get_positions(self):
        return list(self.positions)

    async def get_orders(self, status=None):  # pragma: no cover
        return []


def pos(symbol: str, qty: float) -> Position:
    return Position(symbol=symbol, qty=qty, avg_entry_price=100.0)


def cfg(interval_minutes: float = 5.0) -> YamlConfig:
    return YamlConfig(name="broker", data={"reconciliation": {
        "interval_minutes": interval_minutes,
        "on_mismatch": "block_new_entries",
        "qty_tolerance": 1e-6,
    }})


def make_reconciler(broker, bus, interval_minutes=5.0):
    return Reconciler(broker, bus, config=cfg(interval_minutes))


def alert_recorder(bus: EventBus) -> list:
    alerts: list = []

    async def on_alert(payload):
        alerts.append(payload)

    bus.subscribe(TOPIC_ALERT, on_alert)
    return alerts


# ── pure diff ────────────────────────────────────────────────────────────────
def test_diff_positions_detects_mismatch_and_missing():
    local = [pos("AAPL", 10), pos("MSFT", 5)]
    broker = [pos("AAPL", 10), pos("TSLA", 3)]
    diffs = diff_positions(local, broker)
    by_symbol = {d.symbol: d for d in diffs}
    assert set(by_symbol) == {"MSFT", "TSLA"}
    assert by_symbol["MSFT"].broker_qty == 0.0 and by_symbol["MSFT"].local_qty == 5
    assert by_symbol["TSLA"].broker_qty == 3 and by_symbol["TSLA"].local_qty == 0.0


def test_diff_positions_ignores_dust_within_tolerance():
    local = [pos("AAPL", 10.0)]
    broker = [pos("AAPL", 10.0000001)]
    assert diff_positions(local, broker, qty_tolerance=1e-6) == []


# ── reconcile ────────────────────────────────────────────────────────────────
def test_reconcile_ok_when_matching():
    bus = EventBus()
    alerts = alert_recorder(bus)
    broker = FakeBroker([pos("AAPL", 10)])
    rec = make_reconciler(broker, bus)

    result = asyncio.run(rec.reconcile([pos("AAPL", 10)]))
    assert result.ok is True
    assert rec.blocked is False
    assert alerts == []


def test_reconcile_mismatch_alerts_and_blocks():
    bus = EventBus()
    alerts = alert_recorder(bus)
    broker = FakeBroker([pos("AAPL", 10)])
    rec = make_reconciler(broker, bus)

    result = asyncio.run(rec.reconcile([]))   # local thinks flat, broker holds AAPL
    assert result.ok is False
    assert rec.blocked is True
    assert len(alerts) == 1
    assert alerts[0]["level"] == "critical"
    assert alerts[0]["diffs"][0]["symbol"] == "AAPL"


def test_reconcile_clears_block_on_recovery():
    bus = EventBus()
    alert_recorder(bus)
    broker = FakeBroker([pos("AAPL", 10)])
    rec = make_reconciler(broker, bus)

    async def run():
        await rec.reconcile([])                  # mismatch -> blocked
        blocked_after_mismatch = rec.blocked
        broker.positions = [pos("AAPL", 10)]
        await rec.reconcile([pos("AAPL", 10)])   # now matches -> cleared
        return blocked_after_mismatch, rec.blocked

    blocked_after_mismatch, blocked_after_recovery = asyncio.run(run())
    assert blocked_after_mismatch is True
    assert blocked_after_recovery is False


def test_run_periodic_runs_bounded_iterations():
    bus = EventBus()
    alert_recorder(bus)
    broker = FakeBroker([pos("AAPL", 10)])
    rec = make_reconciler(broker, bus, interval_minutes=0.0)  # no real sleep
    calls = {"n": 0}

    def provider():
        calls["n"] += 1
        return [pos("AAPL", 10)]

    asyncio.run(rec.run(provider, iterations=3))
    assert calls["n"] == 3
    assert rec.blocked is False
