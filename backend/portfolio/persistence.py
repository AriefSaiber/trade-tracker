"""Journal/portfolio persistence (SQLAlchemy 2 async).

Mirrors database/migrations/001_initial_schema.sql for the tables the worker
writes: journal, fills, closed_trades, orders, equity_snapshots. Runs on
Postgres/TimescaleDB in Docker and on SQLite locally (zero-setup dev).

Fail-soft by design: persistence going down must never stop paper trading —
entries stay buffered in the in-memory TradeJournal, an alert fires, and the
writer retries on the next flush. The audit trail is the point of this module;
trading safety is the Risk Engine's job.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import structlog
from sqlalchemy import JSON, DateTime, Float, Integer, String, Text, select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from backend.core.events import Fill, Order
from backend.portfolio.journal import TradeJournal, _serialize
from backend.portfolio.portfolio import ClosedTrade, Portfolio

log = structlog.get_logger(__name__)


class Base(DeclarativeBase):
    pass


class JournalRow(Base):
    __tablename__ = "journal"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    at: Mapped[str] = mapped_column(String(40))
    kind: Mapped[str] = mapped_column(String(64), index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)


class OrderRow(Base):
    __tablename__ = "orders"
    client_order_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    broker_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    strategy_id: Mapped[str] = mapped_column(String(64))
    symbol: Mapped[str] = mapped_column(String(16))
    side: Mapped[str] = mapped_column(String(8))
    qty: Mapped[float] = mapped_column(Float)
    order_type: Mapped[str] = mapped_column(String(12))
    limit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    stop_loss: Mapped[float] = mapped_column(Float)
    take_profit: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(12))
    filled_qty: Mapped[float] = mapped_column(Float, default=0.0)
    avg_fill_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    meta: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[str | None] = mapped_column(String(40), nullable=True)


class FillRow(Base):
    __tablename__ = "fills"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    client_order_id: Mapped[str] = mapped_column(String(64), index=True)
    symbol: Mapped[str] = mapped_column(String(16))
    side: Mapped[str] = mapped_column(String(8))
    qty: Mapped[float] = mapped_column(Float)
    price: Mapped[float] = mapped_column(Float)
    commission: Mapped[float] = mapped_column(Float, default=0.0)
    at: Mapped[str] = mapped_column(String(40))


class ClosedTradeRow(Base):
    __tablename__ = "closed_trades"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(16))
    strategy_id: Mapped[str] = mapped_column(String(64))
    qty: Mapped[float] = mapped_column(Float)
    entry_price: Mapped[float] = mapped_column(Float)
    exit_price: Mapped[float] = mapped_column(Float)
    entry_at: Mapped[str] = mapped_column(String(40))
    exit_at: Mapped[str] = mapped_column(String(40))
    pnl: Mapped[float] = mapped_column(Float)
    commission: Mapped[float] = mapped_column(Float, default=0.0)


class EquitySnapshotRow(Base):
    __tablename__ = "equity_snapshots"
    at: Mapped[str] = mapped_column(String(40), primary_key=True)
    equity: Mapped[float] = mapped_column(Float)
    cash: Mapped[float] = mapped_column(Float)
    daily_pnl: Mapped[float] = mapped_column(Float, default=0.0)


class PersistenceService:
    """Owns the async engine and flushes runtime state on an interval.

    ``start()`` connects and creates missing tables (idempotent; the Alembic /
    initdb SQL owns the schema in Postgres deployments — create_all is a no-op
    there when tables exist). ``flush()`` drains the TradeJournal and writes
    orders/fills/closed-trades/equity it hasn't seen yet.
    """

    def __init__(self, database_url: str, flush_interval_seconds: float = 5.0) -> None:
        self._url = database_url
        self._interval = flush_interval_seconds
        self._engine: AsyncEngine | None = None
        self._session_factory: async_sessionmaker | None = None
        self._persisted_fills = 0
        self._persisted_trades = 0
        self._persisted_equity = 0
        self._pending_journal: list[dict] = []
        self.available = False

    async def start(self) -> None:
        try:
            if self._url.startswith("sqlite"):
                # ensure the parent directory for a file-backed SQLite DB exists
                from pathlib import Path
                db_path = self._url.split("///", 1)[-1]
                if db_path and db_path != ":memory:":
                    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
            self._engine = create_async_engine(self._url)
            async with self._engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            self._session_factory = async_sessionmaker(self._engine, expire_on_commit=False)
            self.available = True
            log.info("persistence_started", url=self._safe_url())
        except Exception as exc:
            self.available = False
            log.error("persistence_unavailable", error=str(exc), url=self._safe_url())

    def _safe_url(self) -> str:
        # never log credentials
        return self._url.split("@")[-1] if "@" in self._url else self._url

    async def flush(self, journal: TradeJournal, portfolio: Portfolio,
                    orders: list[Order] | None = None,
                    fills: list[Fill] | None = None) -> int:
        """Persist everything new. Returns rows written (0 when unavailable)."""
        if not self.available or self._session_factory is None:
            return 0
        # Drain the journal even if the write fails — keep drained entries in
        # a pending buffer so nothing is lost across a transient DB outage.
        self._pending_journal.extend(journal.drain())
        written = 0
        try:
            async with self._session_factory() as session:
                for entry in self._pending_journal:
                    session.add(JournalRow(at=entry["at"], kind=entry["kind"],
                                           payload=entry["payload"]))
                    written += 1

                for fill in (fills or [])[self._persisted_fills:]:
                    session.add(FillRow(
                        client_order_id=fill.client_order_id, symbol=fill.symbol,
                        side=fill.side.value, qty=fill.qty, price=fill.price,
                        commission=fill.commission, at=fill.at.isoformat(),
                    ))
                    written += 1

                for trade in portfolio.closed_trades[self._persisted_trades:]:
                    session.add(self._trade_row(trade))
                    written += 1

                for at, equity in portfolio.equity_curve[self._persisted_equity:]:
                    await session.merge(EquitySnapshotRow(
                        at=at.isoformat(), equity=equity, cash=portfolio.cash,
                        daily_pnl=portfolio.daily_pnl,
                    ))
                    written += 1

                for order in orders or []:
                    await session.merge(OrderRow(
                        client_order_id=order.client_order_id,
                        broker_order_id=order.broker_order_id,
                        strategy_id=order.strategy_id, symbol=order.symbol,
                        side=order.side.value, qty=order.qty,
                        order_type=order.order_type.value,
                        limit_price=order.limit_price, stop_loss=order.stop_loss,
                        take_profit=order.take_profit, status=order.status.value,
                        filled_qty=order.filled_qty,
                        avg_fill_price=order.avg_fill_price,
                        meta=_serialize(order.metadata),
                        created_at=order.created_at.isoformat()
                        if order.created_at else None,
                    ))
                await session.commit()

            self._pending_journal.clear()
            if fills is not None:
                self._persisted_fills = len(fills)
            self._persisted_trades = len(portfolio.closed_trades)
            self._persisted_equity = len(portfolio.equity_curve)
            return written
        except Exception as exc:
            log.error("persistence_flush_failed", error=str(exc),
                      pending=len(self._pending_journal))
            return 0

    @staticmethod
    def _trade_row(trade: ClosedTrade) -> ClosedTradeRow:
        return ClosedTradeRow(
            symbol=trade.symbol, strategy_id=trade.strategy_id, qty=trade.qty,
            entry_price=trade.entry_price, exit_price=trade.exit_price,
            entry_at=trade.entry_at.isoformat(), exit_at=trade.exit_at.isoformat(),
            pnl=trade.pnl, commission=trade.commission,
        )

    async def run(self, journal: TradeJournal, portfolio: Portfolio,
                  get_orders, get_fills, *, iterations: int | None = None) -> None:
        """Periodic flush loop; ``iterations`` bounds it for tests/shutdown."""
        count = 0
        while iterations is None or count < iterations:
            await self.flush(journal, portfolio, get_orders(), get_fills())
            count += 1
            if iterations is not None and count >= iterations:
                return
            await asyncio.sleep(self._interval)

    async def count(self, model) -> int:
        """Row count helper (drills/tests/reporting)."""
        if not self.available or self._session_factory is None:
            return 0
        async with self._session_factory() as session:
            result = await session.execute(select(model))
            return len(result.scalars().all())

    async def stop(self, journal: TradeJournal | None = None,
                   portfolio: Portfolio | None = None,
                   orders: list[Order] | None = None,
                   fills: list[Fill] | None = None) -> None:
        if journal is not None and portfolio is not None:
            await self.flush(journal, portfolio, orders, fills)
        if self._engine is not None:
            await self._engine.dispose()
            log.info("persistence_stopped")
