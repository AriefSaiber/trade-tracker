"""Data staleness policy (MVP §13).

A quote older than 2x the strategy interval blocks new entries. A quote older
than a hard limit WHILE positions are open triggers the configured action —
`auto_flatten` (close everything at market) or `hold_with_alert`. A symbol we
have never seen a quote for is treated as stale. Fail flat on stale data.

All events are logged as structured JSON via structlog.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Awaitable, Callable

import structlog

from backend.core.config import YamlConfig, load_yaml_config
from backend.core.event_bus import TOPIC_ALERT, EventBus

log = structlog.get_logger(__name__)

Flatten = Callable[[], Awaitable[None]]

_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def interval_to_seconds(interval: str) -> float:
    """Parse an interval like '1m', '5m', '1h', '1d' into seconds."""
    text = interval.strip().lower()
    if len(text) < 2 or text[-1] not in _UNIT_SECONDS or not text[:-1].isdigit():
        raise ValueError(f"unrecognized interval: {interval!r}")
    return int(text[:-1]) * _UNIT_SECONDS[text[-1]]


@dataclass
class StalenessResult:
    block_entries: bool
    stale: list[str] = field(default_factory=list)        # older than 2x interval
    hard_stale: list[str] = field(default_factory=list)   # older than hard limit
    action: str = "none"                                  # none|hold_with_alert|auto_flatten
    at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class StalenessMonitor:
    def __init__(self, bus: EventBus | None = None,
                 config: YamlConfig | None = None) -> None:
        cfg = config or load_yaml_config("watchdog")
        self._bus = bus
        self._multiplier = float(cfg.get("staleness.interval_multiplier", 2.0))
        self._hard_multiplier = float(cfg.get("staleness.hard_limit_multiplier", 6.0))
        self._on_hard_limit = str(cfg.get("staleness.on_hard_limit", "hold_with_alert"))
        self._last_quote: dict[str, datetime] = {}
        self._interval_s: dict[str, float] = {}
        self.entries_blocked = False
        self.stale_symbols: list[str] = []
        # Avoid publishing the same alert on every watchdog tick.  The
        # safety state is still evaluated on every tick; only unchanged
        # notifications are coalesced until the stale set/action changes.
        self._last_alert_signature: tuple[tuple[str, ...], tuple[str, ...], str] | None = None

    def record_quote(self, symbol: str, at: datetime, interval: str) -> None:
        """Record the timestamp of the latest quote/bar for `symbol`."""
        self._last_quote[symbol] = at
        self._interval_s[symbol] = interval_to_seconds(interval)

    def _age(self, symbol: str, now: datetime) -> float | None:
        at = self._last_quote.get(symbol)
        return None if at is None else (now - at).total_seconds()

    def is_stale(self, symbol: str, now: datetime | None = None) -> bool:
        now = now or datetime.now(timezone.utc)
        age = self._age(symbol, now)
        if age is None:
            return True  # never seen a quote => stale, fail flat
        return age > self._multiplier * self._interval_s[symbol]

    def _is_hard_stale(self, symbol: str, now: datetime) -> bool:
        age = self._age(symbol, now)
        if age is None:
            return True
        return age > self._hard_multiplier * self._interval_s[symbol]

    async def check(self, *, now: datetime | None = None,
                    has_open_positions: bool = False,
                    flatten: Flatten | None = None) -> StalenessResult:
        """Evaluate all tracked symbols. Blocks entries if any is 2x stale;
        on a hard-limit breach with open positions, applies the configured
        action (auto-flatten via `flatten`, or hold-with-alert)."""
        now = now or datetime.now(timezone.utc)
        stale = sorted(s for s in self._last_quote if self.is_stale(s, now))
        hard = sorted(s for s in self._last_quote if self._is_hard_stale(s, now))

        self.stale_symbols = stale
        self.entries_blocked = bool(stale)

        action = "none"
        if hard and has_open_positions:
            action = self._on_hard_limit

        result = StalenessResult(bool(stale), stale, hard, action, now)

        if stale:
            log.warning("data_stale_entries_blocked", stale=stale,
                        hard_stale=hard, action=action)
            signature = (tuple(stale), tuple(hard), action)
            if self._bus is not None and signature != self._last_alert_signature:
                await self._bus.publish(TOPIC_ALERT, {
                    "level": "critical" if hard and has_open_positions else "warning",
                    "source": "watchdog.staleness",
                    "message": f"stale quotes {stale} — new entries blocked",
                    "hard_stale": hard,
                    "action": action,
                    "at": now.isoformat(),
                })
            self._last_alert_signature = signature
        else:
            # Permit a fresh alert if the same symbols become stale again.
            self._last_alert_signature = None

        if action == "auto_flatten" and flatten is not None:
            log.critical("data_stale_auto_flatten", hard_stale=hard)
            await flatten()

        return result
