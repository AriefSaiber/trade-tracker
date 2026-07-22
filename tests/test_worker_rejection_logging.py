"""Worker-side validation diagnostics keep the primary rejection attributable."""
from __future__ import annotations

import asyncio
from datetime import timedelta
from types import SimpleNamespace

from backend.core.events import Regime, RegimeState, Signal, StageResult
from backend.portfolio.journal import TradeJournal
from backend.validation.funnel_logger import FunnelLogger
from backend.worker import TradingRuntime


def _runtime(now):
    runtime = object.__new__(TradingRuntime)
    runtime.funnel = FunnelLogger()
    runtime.journal = TradeJournal()
    runtime._worker_cfg = {"worker.signal_log_cap": 10}
    runtime._signal_log = []
    runtime._rejection_cooldowns = {}
    runtime.now = now
    return runtime


def test_rejection_uses_matching_signal_not_another_symbol_at_same_time(now):
    runtime = _runtime(now)
    target = Signal("trend_pullback", "NVDA", "LONG", 0.8, now, {})
    other = Signal("gpt_pro", "QQQ", "LONG", 0.8, now, {})
    runtime.funnel.record(other, StageResult("regime_gate", False, {}, "QQQ reason"))
    runtime.funnel.record(target, StageResult("regime_gate", False, {}, "NVDA reason"))

    slot = SimpleNamespace(rejection_cooldown_bars=0, interval="1h")
    regime = RegimeState("SPY", Regime.RANGE, now)
    asyncio.run(runtime._journal_rejection(target, slot, regime))

    entry = runtime.journal.entries[-1]["payload"]
    assert entry["reason"] == "NVDA reason"
    assert runtime._signal_log[-1]["reason"] == "NVDA reason"


def test_rejection_cooldown_is_limited_to_the_same_regime(now):
    runtime = _runtime(now)
    signal = Signal("gpt_pro", "QQQ", "LONG", 0.8, now, {})
    slot = SimpleNamespace(rejection_cooldown_bars=6, interval="1h")
    ranging = RegimeState("SPY", Regime.RANGE, now)
    runtime._remember_rejection(slot, signal, ranging)

    repeated = Signal("gpt_pro", "QQQ", "LONG", 0.8, now + timedelta(hours=1), {})
    assert runtime._rejection_is_on_cooldown(slot, repeated, ranging)
    assert not runtime._rejection_is_on_cooldown(
        slot, repeated, RegimeState("SPY", Regime.TREND_UP, now))
