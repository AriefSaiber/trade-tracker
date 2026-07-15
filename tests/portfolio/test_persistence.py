"""Persistence round-trip against SQLite (same SQLAlchemy models as Postgres)."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from backend.core.events import Fill, OrderSide
from backend.portfolio.journal import TradeJournal
from backend.portfolio.persistence import (
    ClosedTradeRow, EquitySnapshotRow, FillRow, JournalRow, PersistenceService,
)
from backend.portfolio.portfolio import Portfolio

T0 = datetime(2026, 7, 1, 15, 0, tzinfo=timezone.utc)


def _service(tmp_path) -> PersistenceService:
    return PersistenceService(f"sqlite+aiosqlite:///{tmp_path}/journal.db")


def test_journal_entries_survive_flush(tmp_path):
    async def run() -> None:
        svc = _service(tmp_path)
        await svc.start()
        assert svc.available

        journal = TradeJournal()
        portfolio = Portfolio(starting_cash=100_000.0)
        journal.record("signal_rejected", {"stage": "regime_gate", "reason": "RANGE"})
        journal.record("fill", {"symbol": "AAPL"})

        written = await svc.flush(journal, portfolio)
        assert written == 2
        assert journal.entries == []                 # drained
        assert await svc.count(JournalRow) == 2
        await svc.stop()

    asyncio.run(run())


def test_fills_trades_equity_persist_incrementally(tmp_path):
    async def run() -> None:
        svc = _service(tmp_path)
        await svc.start()
        journal = TradeJournal()
        portfolio = Portfolio(starting_cash=100_000.0)

        fill_in = Fill("co-1", "AAPL", OrderSide.BUY, 10, 100.0, T0)
        portfolio.apply_fill(fill_in, "s1", 95.0, 110.0)
        portfolio.snapshot_equity(T0, {"AAPL": 100.0})
        await svc.flush(journal, portfolio, orders=[], fills=[fill_in])

        fill_out = Fill("co-2", "AAPL", OrderSide.SELL, 10, 108.0, T0)
        portfolio.apply_fill(fill_out)
        portfolio.snapshot_equity(T0, {"AAPL": 108.0})
        await svc.flush(journal, portfolio, orders=[], fills=[fill_in, fill_out])

        assert await svc.count(FillRow) == 2          # no double-writes
        assert await svc.count(ClosedTradeRow) == 1
        assert await svc.count(EquitySnapshotRow) >= 1
        await svc.stop()

    asyncio.run(run())


def test_unavailable_database_never_raises(tmp_path):
    async def run() -> None:
        svc = PersistenceService("postgresql+asyncpg://nobody:x@127.0.0.1:1/nope")
        await svc.start()                              # connection refused
        assert not svc.available

        journal = TradeJournal()
        journal.record("alert", {"m": 1})
        written = await svc.flush(journal, Portfolio(starting_cash=1.0))
        assert written == 0                            # degraded, not crashed
        await svc.stop()

    asyncio.run(run())
