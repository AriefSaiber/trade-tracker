"""Watchdog drills (MVP §13, required for Promotion Gate B).

Deliberately breaks things in paper mode and verifies the system fails flat:

  Drill 1 — data feed dies      -> staleness blocks new entries + alert
  Drill 2 — kill switch trips   -> orders cancelled, entries blocked, KILL persists
  Drill 3 — heartbeat missed    -> trading halted + alert

Run:  python scripts/watchdog_drill.py
Exit code 0 = all drills passed; 1 = a drill failed (do NOT promote).
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from backend.core.config import YamlConfig, get_settings  # noqa: E402
from backend.core.event_bus import TOPIC_ALERT, EventBus  # noqa: E402
from backend.core.events import Regime, RegimeState, Signal, ValidatedSignal  # noqa: E402
from backend.core.state import InMemoryStateStore  # noqa: E402
from backend.data.simulated_provider import SimulatedDataProvider  # noqa: E402
from backend.risk.engine import RiskEngine  # noqa: E402
from backend.watchdog.monitor import HeartbeatMonitor, InMemoryHeartbeatStore  # noqa: E402
from backend.worker import TradingRuntime  # noqa: E402

PASS = "PASS"
FAIL = "FAIL"


class StallableProvider(SimulatedDataProvider):
    """Simulated provider whose feed can be killed mid-run."""

    def __init__(self, *a, **kw) -> None:
        super().__init__(*a, **kw)
        self.stalled = False

    async def get_bars(self, symbol, interval, start, end):
        if self.stalled:
            return []
        return await super().get_bars(symbol, interval, start, end)


def _probe_entry(runtime: TradingRuntime) -> str:
    """Ask the risk engine to approve a fresh entry; return the rejection reason
    ('' means it was approved — which, mid-drill, is a failure)."""
    signal = Signal(strategy_id="drill_probe", symbol="AAPL", direction="LONG",
                    confidence=1.0, bar_time=runtime.now, metadata={})
    validated = ValidatedSignal(signal=signal, score=100.0, stage_results=[],
                                regime="TREND_UP",
                                validated_at=datetime.now(timezone.utc))
    decision = RiskEngine().evaluate(
        validated, runtime._account_state(dict(runtime.latest_price)),
        entry_price=100.0, atr_value=2.0)
    return "" if decision.approved else decision.reason


async def drill_1_data_feed() -> bool:
    provider = StallableProvider()
    runtime = TradingRuntime(provider=provider, state=InMemoryStateStore(),
                             poll_seconds=0)
    alerts: list[dict] = []

    async def capture(payload) -> None:
        alerts.append(payload if isinstance(payload, dict) else {"message": payload})

    runtime.bus.subscribe(TOPIC_ALERT, capture)
    await runtime.start()
    # a few healthy cycles, then kill the feed and keep the clock moving
    for _ in range(5):
        runtime.now = runtime.clock.tick()
        await runtime._cycle_once()
    provider.stalled = True
    for _ in range(20):
        now = runtime.clock.tick()
        if now is None:
            break
        runtime.now = now
        await runtime._cycle_once()
    reason = _probe_entry(runtime)
    blocked = runtime.staleness.entries_blocked and "stale" in reason
    alerted = any("stale" in str(a.get("message", "")) for a in alerts)
    await runtime.shutdown()
    print(f"  [{PASS if (blocked and alerted) else FAIL}] data feed killed -> "
          f"entries_blocked={runtime.staleness.entries_blocked}, "
          f"probe_rejection={reason!r}, alert_fired={alerted}")
    return blocked and alerted


async def drill_2_kill_switch(tmp_kill: Path) -> bool:
    runtime = TradingRuntime(provider=StallableProvider(),
                             state=InMemoryStateStore(), poll_seconds=0)
    # isolate the drill's KILL file from the real data/KILL
    runtime.kill_switch._file = tmp_kill
    await runtime.start()
    for _ in range(3):
        runtime.now = runtime.clock.tick()
        await runtime._cycle_once()

    tmp_kill.parent.mkdir(parents=True, exist_ok=True)
    tmp_kill.touch()
    if runtime.kill_switch.check_file() and not runtime.kill_switch.active:
        await runtime.kill_switch.trigger_from_file()

    reason = _probe_entry(runtime)
    active = runtime.kill_switch.active
    disarmed = not runtime.kill_switch.live_armed
    persists = tmp_kill.exists()
    open_orders = [o for o in await runtime.broker.get_orders()
                   if o.status.value in ("PENDING", "SUBMITTED", "PARTIAL")]
    await runtime.shutdown()
    tmp_kill.unlink(missing_ok=True)
    ok = active and disarmed and persists and "kill" in reason and not open_orders
    print(f"  [{PASS if ok else FAIL}] kill switch tripped -> active={active}, "
          f"live_disarmed={disarmed}, file_persists={persists}, "
          f"probe_rejection={reason!r}, open_orders={len(open_orders)}")
    return ok


async def drill_3_heartbeat() -> bool:
    bus = EventBus()
    alerts: list[dict] = []

    async def capture(payload) -> None:
        alerts.append(payload)

    bus.subscribe(TOPIC_ALERT, capture)
    monitor = HeartbeatMonitor(
        InMemoryHeartbeatStore(), bus=bus,
        config=YamlConfig(name="watchdog", data={"heartbeat": {
            "timeout_seconds": 1, "check_interval_seconds": 0.1,
            "components": ["worker"],
        }}))
    await monitor.beat("worker")
    await asyncio.sleep(1.2)          # let the heartbeat go overdue
    overdue = await monitor.check()
    halted = monitor.trading_halted
    alerted = any("heartbeat" in str(a.get("message", "")) for a in alerts)
    ok = bool(overdue) and halted and alerted
    print(f"  [{PASS if ok else FAIL}] heartbeat missed -> overdue={overdue}, "
          f"trading_halted={halted}, alert_fired={alerted}")
    return ok


async def main() -> int:
    get_settings()
    print("Watchdog drills (paper mode) — MVP §13 / Promotion Gate B\n")
    results = []
    print("Drill 1: kill the data feed")
    results.append(await drill_1_data_feed())
    print("Drill 2: trip the kill switch")
    results.append(await drill_2_kill_switch(REPO_ROOT / "data" / "KILL.drill"))
    print("Drill 3: miss a heartbeat")
    results.append(await drill_3_heartbeat())
    print(f"\n{'ALL DRILLS PASSED' if all(results) else 'DRILL FAILURE — do not promote'}")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
