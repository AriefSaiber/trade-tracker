"""Kill switch (MVP §13).

A file trigger (`touch data/KILL`), a dashboard button, and a Telegram command —
ANY of them cancels open orders, optionally flattens all positions, and DISARMS
live trading until a manual re-arm. The trip is persisted as the KILL file so a
process restart stays disarmed. Doing nothing is always a safe state.

All events are logged as structured JSON via structlog.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable

import structlog

from backend.core.config import YamlConfig, load_yaml_config
from backend.core.event_bus import TOPIC_ALERT, EventBus

log = structlog.get_logger(__name__)

# Async hooks the switch calls on trigger. Kept as callables so the watchdog
# never imports the execution layer directly (one-way dependency).
CancelOrders = Callable[[], Awaitable[None]]
Flatten = Callable[[], Awaitable[None]]


class KillSwitch:
    def __init__(
        self,
        bus: EventBus | None = None,
        cancel_orders: CancelOrders | None = None,
        flatten: Flatten | None = None,
        config: YamlConfig | None = None,
    ) -> None:
        cfg = config or load_yaml_config("watchdog")
        self._bus = bus
        self._cancel_orders = cancel_orders
        self._flatten = flatten
        self._file = Path(str(cfg.get("kill_switch.file_path", "data/KILL")))
        self._flatten_default = bool(cfg.get("kill_switch.flatten_on_trigger", False))
        self.active = False
        # Live trading is disarmed on any trigger and stays disarmed through a
        # re-arm — re-enabling live requires the full arming procedure (§15).
        self.live_armed = True

    @property
    def file_path(self) -> Path:
        return self._file

    def check_file(self) -> bool:
        """True if the file trigger (data/KILL) is present."""
        return self._file.exists()

    async def trigger(self, source: str, *, flatten: bool | None = None) -> None:
        """Cancel open orders, optionally flatten, disarm live, and alert.

        Idempotent: re-triggering while already active still runs the cancel /
        flatten hooks (belt-and-suspenders) but is logged as a repeat.
        """
        do_flatten = self._flatten_default if flatten is None else flatten
        repeat = self.active
        self.active = True
        self.live_armed = False

        # Persist the trip so a restart comes up disarmed.
        self._file.parent.mkdir(parents=True, exist_ok=True)
        if not self._file.exists():
            self._file.touch()

        if self._cancel_orders is not None:
            await self._cancel_orders()
        if do_flatten and self._flatten is not None:
            await self._flatten()

        log.critical(
            "kill_switch_triggered",
            source=source,
            flatten=do_flatten,
            repeat=repeat,
        )
        if self._bus is not None:
            await self._bus.publish(TOPIC_ALERT, {
                "level": "critical",
                "source": f"kill_switch.{source}",
                "message": f"KILL SWITCH via {source} — orders cancelled, live disarmed",
                "flatten": do_flatten,
                "at": datetime.now(timezone.utc).isoformat(),
            })

    # Explicit provenance for the three trigger sources (§13). Same effect,
    # different `source` in the log and alert.
    async def trigger_from_file(self, *, flatten: bool | None = None) -> None:
        await self.trigger("file", flatten=flatten)

    async def trigger_from_dashboard(self, *, flatten: bool | None = None) -> None:
        await self.trigger("dashboard", flatten=flatten)

    async def trigger_from_telegram(self, *, flatten: bool | None = None) -> None:
        await self.trigger("telegram", flatten=flatten)

    def rearm(self) -> None:
        """Manual re-arm: removes the KILL file and clears the halt. Live stays
        disarmed — that requires the full arming procedure (MVP §15)."""
        if self._file.exists():
            self._file.unlink()
        self.active = False
        log.warning("kill_switch_rearmed", live_armed=self.live_armed)

    async def watch(self, *, iterations: int | None = None,
                    interval_seconds: float = 1.0) -> None:
        """Poll the file trigger. `iterations` bounds the loop for tests /
        shutdown; None runs forever."""
        count = 0
        while iterations is None or count < iterations:
            if self.check_file() and not self.active:
                await self.trigger("file")
            count += 1
            if iterations is not None and count >= iterations:
                return
            await asyncio.sleep(interval_seconds)
