"""Realistic cost model (MVP §11): commission + slippage (half spread +
volatility-scaled impact) + simulated latency."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CostModel:
    commission_per_share: float = 0.0
    commission_bps: float = 0.0       # % of notional, e.g. 25 = 0.25% (crypto taker)
    min_commission: float = 0.0
    half_spread_bps: float = 1.0
    impact_coefficient: float = 0.1   # x ATR fraction of price
    latency_ms: float = 300.0

    def commission(self, qty: float, price: float = 0.0) -> float:
        """Per-share plus percentage-of-notional commission. Callers must pass
        the fill price for the bps component to apply (crypto fee schedules)."""
        per_share = abs(qty) * self.commission_per_share
        pct = abs(qty) * price * self.commission_bps / 10_000
        return max(self.min_commission, per_share + pct)

    def slippage(self, price: float, atr_value: float, is_buy: bool) -> float:
        """Signed slippage added to fill price (adverse to the trader)."""
        half_spread = price * self.half_spread_bps / 10_000
        impact = self.impact_coefficient * atr_value if atr_value > 0 else 0.0
        adverse = half_spread + impact
        return adverse if is_buy else -adverse

    def fill_price(self, price: float, atr_value: float, is_buy: bool) -> float:
        return price + self.slippage(price, atr_value, is_buy)
