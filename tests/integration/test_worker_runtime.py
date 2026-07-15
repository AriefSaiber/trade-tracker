"""End-to-end runtime test: the ACTUAL worker (backend/worker.py), not a
hand-wired replica, paper-trades the simulated feed through the full path —

    SimulatedDataProvider -> RegimeDetector -> strategies -> pipeline
        -> RiskEngine -> ExecutionManager -> PaperBroker -> Portfolio
        -> TradeJournal -> SQLite persistence -> StateStore (dashboard)

This is the proof behind "docker compose up starts a working paper trader":
at least one complete trade (entry AND exit) with P&L, every fill journaled
and persisted, dashboard state populated, kill switch honored.
"""
from __future__ import annotations

import asyncio

import pytest

from backend.core.config import YamlConfig
from backend.core.state import (
    KEY_FUNNEL_SUMMARY, KEY_PORTFOLIO, KEY_SIGNALS, KEY_STRATEGIES, KEY_WORKER,
    InMemoryStateStore,
)
from backend.data.simulated_provider import SimulatedDataProvider
from backend.portfolio.persistence import ClosedTradeRow, FillRow, JournalRow
from backend.worker import TradingRuntime

FAST_BROKER = YamlConfig(name="broker", data={
    "paper_simulator": {"slippage_bps": 3, "latency_ms": 0},
    "reconciliation": {"on_mismatch": "block_new_entries"},
})
CYCLES = 80


@pytest.fixture(scope="module")
def runtime_result(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("worker-e2e")
    state = InMemoryStateStore()
    runtime = TradingRuntime(
        # explicit: this test exercises the sim feed regardless of the
        # provider configured in market.yaml (alpaca would hit the network)
        provider=SimulatedDataProvider(),
        state=state,
        poll_seconds=0,
        broker_config=FAST_BROKER,
        database_url=f"sqlite+aiosqlite:///{tmp}/e2e.db",
    )
    runtime.kill_switch._file = tmp / "KILL"   # isolate from repo data/KILL

    asyncio.run(runtime.run(max_cycles=CYCLES))
    return runtime, state


def test_at_least_one_complete_paper_trade(runtime_result):
    runtime, _ = runtime_result
    assert len(runtime.broker.fills) >= 2, "expected an entry and an exit fill"
    assert runtime.portfolio.closed_trades, "no trade completed entry->exit"
    trade = runtime.portfolio.closed_trades[0]
    assert trade.pnl != 0.0
    assert trade.exit_at > trade.entry_at


def test_every_fill_is_journaled(runtime_result):
    runtime, _ = runtime_result
    journaled = {e["payload"]["client_order_id"]
                 for e in runtime.journal.entries + runtime.persistence._pending_journal
                 if e["kind"] == "fill"}
    # journal drains into the DB each cycle; re-read persisted rows instead
    persisted = asyncio.run(_journal_fill_ids(runtime))
    for fill in runtime.broker.fills:
        assert fill.client_order_id in journaled | persisted


async def _journal_fill_ids(runtime) -> set[str]:
    from sqlalchemy import select
    async with runtime.persistence._session_factory() as session:
        rows = (await session.execute(select(JournalRow))).scalars().all()
    return {r.payload.get("client_order_id") for r in rows if r.kind == "fill"}


def test_persistence_has_fills_and_closed_trades(runtime_result):
    runtime, _ = runtime_result
    assert asyncio.run(runtime.persistence.count(FillRow)) >= 2
    assert asyncio.run(runtime.persistence.count(ClosedTradeRow)) >= 1


def test_pnl_and_cash_are_consistent(runtime_result):
    runtime, _ = runtime_result
    p = runtime.portfolio
    realized = sum(t.pnl for t in p.closed_trades)
    commissions = sum(t.commission for t in p.closed_trades)
    open_cost = sum(pos.qty * pos.avg_entry_price for pos in p.positions.values())
    assert p.cash == pytest.approx(
        p.starting_cash + realized - commissions - open_cost, rel=1e-6)


def test_dashboard_state_is_published(runtime_result):
    runtime, state = runtime_result

    async def read(key, default=None):
        return await state.get(key, default)

    portfolio = asyncio.run(read(KEY_PORTFOLIO, {}))
    assert portfolio["equity"] > 0
    assert portfolio["equity_curve"], "equity curve missing from dashboard state"

    signals = asyncio.run(read(KEY_SIGNALS, []))
    assert signals, "no signal rows published"
    assert all("validated" in row for row in signals)
    rejected = [r for r in signals if not r["validated"]]
    for row in rejected:
        assert row.get("stage_failed") and row.get("reason")

    summary = asyncio.run(read(KEY_FUNNEL_SUMMARY, []))
    assert [s["stage"] for s in summary] == [
        "data_sanity", "regime_gate", "mtf_alignment", "volume_confirmation",
        "volatility_band", "confluence_score", "event_filter",
        "portfolio_correlation",
    ]

    strategies = asyncio.run(read(KEY_STRATEGIES, []))
    assert {s["strategy_id"] for s in strategies} == {
        "trend_pullback", "rsi2_mean_reversion", "gpt_pro", "btc_trend_momentum"}
    assert all(s["enabled"] for s in strategies)   # no toggle file => all on

    worker = asyncio.run(read(KEY_WORKER, {}))
    assert worker["closed_trades"] == len(runtime.portfolio.closed_trades)
    assert worker["alive"] is False          # clean shutdown flips it


def test_kill_switch_blocks_new_entries(tmp_path):
    state = InMemoryStateStore()
    runtime = TradingRuntime(
        provider=SimulatedDataProvider(),
        state=state, poll_seconds=0, broker_config=FAST_BROKER,
        database_url=f"sqlite+aiosqlite:///{tmp_path}/kill.db",
    )
    runtime.kill_switch._file = tmp_path / "KILL"

    async def run() -> None:
        await runtime.start()
        for _ in range(5):
            runtime.now = runtime.clock.tick()
            await runtime._cycle_once()
        (tmp_path / "KILL").touch()
        await runtime.kill_switch.trigger_from_file()
        fills_at_kill = len(runtime.broker.fills)
        for _ in range(20):
            now = runtime.clock.tick()
            if now is None:
                break
            runtime.now = now
            await runtime._cycle_once()
        # entries are blocked; the only permissible activity is flatten/cancel
        entry_fills = [f for f in runtime.broker.fills[fills_at_kill:]
                       if f.side.value == "BUY"]
        assert entry_fills == []
        assert runtime.kill_switch.active
        await runtime.shutdown()

    asyncio.run(run())
