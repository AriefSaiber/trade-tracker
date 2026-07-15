"""Position sizing (MVP §10): fixed fractional risk with volatility-based stops."""
from __future__ import annotations

import math
from dataclasses import dataclass

# Alpaca supports crypto quantities to 9 decimal places; flooring at that
# precision keeps realized risk <= the computed risk budget.
FRACTIONAL_DECIMALS = 9


@dataclass(frozen=True)
class SizedPosition:
    shares: float
    stop_price: float
    take_profit: float | None
    risk_amount: float


def volatility_stop(entry: float, atr_value: float, atr_multiplier: float,
                    is_long: bool) -> float:
    offset = atr_value * atr_multiplier
    return entry - offset if is_long else entry + offset


def fixed_fractional_size(
    equity: float,
    risk_per_trade_pct: float,
    entry: float,
    stop_price: float,
    max_position_pct: float,
    is_long: bool = True,
    take_profit_r_multiple: float | None = None,
    fractional: bool = False,
) -> SizedPosition:
    """shares = floor((equity x risk%) / (entry - stop)). Size shrinks
    automatically as volatility (stop distance) expands.

    ``fractional=True`` (crypto) floors to FRACTIONAL_DECIMALS instead of whole
    shares — at six-figure BTC prices whole-share flooring would round nearly
    every size to zero and the system would simply never trade.
    """
    if equity <= 0 or entry <= 0:
        return SizedPosition(0, stop_price, None, 0.0)
    stop_distance = (entry - stop_price) if is_long else (stop_price - entry)
    if stop_distance <= 0:
        return SizedPosition(0, stop_price, None, 0.0)   # invalid stop => no trade

    risk_amount = equity * (risk_per_trade_pct / 100.0)
    raw = risk_amount / stop_distance

    # cap by max position value
    max_value = equity * (max_position_pct / 100.0)
    raw = min(raw, max_value / entry)

    if fractional:
        factor = 10 ** FRACTIONAL_DECIMALS
        shares = math.floor(raw * factor) / factor
    else:
        shares = float(math.floor(raw))
    shares = max(shares, 0.0)

    take_profit: float | None = None
    if take_profit_r_multiple is not None and shares > 0:
        offset = stop_distance * take_profit_r_multiple
        take_profit = entry + offset if is_long else entry - offset

    return SizedPosition(
        shares=shares,
        stop_price=stop_price,
        take_profit=take_profit,
        risk_amount=shares * stop_distance,
    )
