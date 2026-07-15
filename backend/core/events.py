"""Core event / message types shared across the platform.

These are the canonical interfaces from CLAUDE.md §5 — do not change
signatures without an explicit decision.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Literal


@dataclass(frozen=True)
class Bar:
    symbol: str
    interval: str            # "1m" | "5m" | "15m" | "1h" | "1d"
    timestamp: datetime      # bar OPEN time, tz-aware UTC
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class Tick:
    symbol: str
    timestamp: datetime
    price: float
    size: float


@dataclass
class Signal:
    strategy_id: str
    symbol: str
    direction: Literal["LONG", "SHORT", "FLAT"]
    confidence: float          # 0.0–1.0 from the strategy itself
    bar_time: datetime
    metadata: dict             # strategy-specific context, never used by Risk/Execution


@dataclass
class StageResult:
    stage: str
    passed: bool
    measured: dict
    reason: str


@dataclass
class ValidatedSignal:
    signal: Signal
    score: float               # 0–100 from the Validation Pipeline
    stage_results: list[StageResult]
    regime: str
    validated_at: datetime


class Regime(str, Enum):
    TREND_UP = "TREND_UP"
    TREND_DOWN = "TREND_DOWN"
    RANGE = "RANGE"
    HIGH_VOL = "HIGH_VOL"
    TRANSITION = "TRANSITION"


@dataclass
class RegimeState:
    symbol: str                # symbol or benchmark (e.g. SPY)
    regime: Regime
    as_of: datetime
    metrics: dict = field(default_factory=dict)   # adx, ema slope, vol percentile...


class OrderStatus(str, Enum):
    PENDING = "PENDING"
    SUBMITTED = "SUBMITTED"
    PARTIAL = "PARTIAL"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"


@dataclass
class Order:
    client_order_id: str        # stable idempotency key — REQUIRED on every order
    strategy_id: str
    symbol: str
    side: OrderSide
    qty: float
    order_type: OrderType
    limit_price: float | None
    stop_loss: float            # an entry without a stop is rejected by the Risk Engine
    take_profit: float | None
    time_in_force: str
    status: OrderStatus = OrderStatus.PENDING
    created_at: datetime | None = None
    broker_order_id: str | None = None
    filled_qty: float = 0.0
    avg_fill_price: float | None = None
    metadata: dict = field(default_factory=dict)


@dataclass
class OrderAck:
    client_order_id: str
    broker_order_id: str
    status: OrderStatus
    at: datetime


@dataclass
class Fill:
    client_order_id: str
    symbol: str
    side: OrderSide
    qty: float
    price: float
    at: datetime
    commission: float = 0.0


@dataclass
class Position:
    symbol: str
    qty: float                  # signed: negative = short
    avg_entry_price: float
    strategy_id: str | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    unrealized_pnl: float = 0.0
    sector: str | None = None
