"""Reconciler: broker truth vs local portfolio.

The broker is authoritative — we never "correct" the broker to match us. On any
mismatch we emit a critical alert and (per configs/broker.yaml
reconciliation.on_mismatch) block new entries until an operator resolves it.
This is the fail-flat backstop for the whole execution path.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone

import structlog

from backend.core.event_bus import TOPIC_ALERT, EventBus
from backend.core.config import load_yaml_config
from backend.core.events import Position
from backend.execution.broker_adapter import BrokerAdapter

log = structlog.get_logger(__name__)


@dataclass
class PositionDiff:
    symbol: str
    broker_qty: float
    local_qty: float


@dataclass
class ReconciliationResult:
    ok: bool
    diffs: list[PositionDiff] = field(default_factory=list)
    at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


def diff_positions(
    local: list[Position],
    broker: list[Position],
    *,
    qty_tolerance: float = 1e-6,
) -> list[PositionDiff]:
    """Symbols whose signed quantity differs by more than `qty_tolerance`.
    Flat positions (qty == 0) on either side are treated as absent."""
    local_qty = {p.symbol: p.qty for p in local if p.qty != 0}
    broker_qty = {p.symbol: p.qty for p in broker if p.qty != 0}
    diffs: list[PositionDiff] = []
    for symbol in sorted(set(local_qty) | set(broker_qty)):
        bq = broker_qty.get(symbol, 0.0)
        lq = local_qty.get(symbol, 0.0)
        if abs(bq - lq) > qty_tolerance:
            diffs.append(PositionDiff(symbol=symbol, broker_qty=bq, local_qty=lq))
    return diffs


class Reconciler:
    def __init__(self, broker: BrokerAdapter, bus: EventBus, config=None) -> None:
        cfg = config or load_yaml_config("broker")
        self._broker = broker
        self._bus = bus
        self._interval_s = float(cfg.get("reconciliation.interval_minutes", 5)) * 60.0
        self._on_mismatch = cfg.get("reconciliation.on_mismatch", "block_new_entries")
        self._tol = float(cfg.get("reconciliation.qty_tolerance", 1e-6))
        self.blocked = False
        self.last_result: ReconciliationResult | None = None

    async def reconcile(self, local_positions: list[Position]) -> ReconciliationResult:
        """Compare broker truth to `local_positions`. On mismatch: alert + (per
        config) set `blocked`. A clean pass clears `blocked`."""
        broker_positions = await self._broker.get_positions()
        diffs = diff_positions(local_positions, broker_positions, qty_tolerance=self._tol)
        result = ReconciliationResult(ok=not diffs, diffs=diffs)
        self.last_result = result

        if diffs:
            if self._on_mismatch == "block_new_entries":
                self.blocked = True
            payload = {
                "level": "critical",
                "message": "reconciliation mismatch — new entries blocked",
                "diffs": [
                    {"symbol": d.symbol, "broker": d.broker_qty, "local": d.local_qty}
                    for d in diffs
                ],
                "at": result.at.isoformat(),
            }
            log.error("reconciliation_mismatch", **payload)
            await self._bus.publish(TOPIC_ALERT, payload)
            return result

        self.blocked = False
        log.info("reconciliation_ok", positions=len(broker_positions))
        return result

    async def run(
        self,
        positions_provider,
        *,
        iterations: int | None = None,
    ) -> None:
        """Periodic loop. `positions_provider` returns (or awaits to) the current
        local positions each cycle. `iterations` bounds the loop for tests /
        controlled shutdown; None runs forever."""
        count = 0
        while iterations is None or count < iterations:
            local = positions_provider()
            if asyncio.iscoroutine(local):
                local = await local
            await self.reconcile(local)
            count += 1
            if iterations is not None and count >= iterations:
                return
            await asyncio.sleep(self._interval_s)
