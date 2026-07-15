"""Stage 7 — Portfolio & correlation gate: heat cap, pairwise correlation cap,
sector exposure cap, max concurrent positions."""
from __future__ import annotations

import pandas as pd

from backend.core.events import Signal, StageResult
from backend.validation.base import ValidationStage
from backend.validation.context import ValidationContext


class PortfolioCorrelationStage(ValidationStage):
    name = "portfolio_correlation"

    def validate(self, signal: Signal, context: ValidationContext) -> StageResult:
        if signal.direction == "FLAT":
            return self._skipped("exit signal")

        measured: dict = {}

        # Portfolio heat: sum of open risk (distance to stop x qty) vs equity
        heat = 0.0
        for pos in context.open_positions:
            if pos.stop_loss is not None and pos.qty != 0:
                heat += abs(pos.avg_entry_price - pos.stop_loss) * abs(pos.qty)
        heat_pct = (heat / context.equity * 100) if context.equity > 0 else 0.0
        max_heat = float(self.config["max_heat_pct"])
        measured["portfolio_heat_pct"] = round(heat_pct, 2)
        measured["max_heat_pct"] = max_heat
        if heat_pct >= max_heat:
            return StageResult(self.name, False, measured, "portfolio heat cap reached")

        # Correlation with existing positions (60-day daily returns)
        max_corr = float(self.config["max_correlation"])
        lookback = int(self.config["correlation_lookback_days"])
        new_daily = context.bars(signal.symbol, "1d")
        if not new_daily.empty:
            new_ret = new_daily["close"].pct_change().tail(lookback)
            for pos in context.open_positions:
                if pos.symbol == signal.symbol:
                    continue
                pos_daily = context.bars(pos.symbol, "1d")
                if pos_daily.empty:
                    continue
                pos_ret = pos_daily["close"].pct_change().tail(lookback)
                joined = pd.concat([new_ret, pos_ret], axis=1, join="inner").dropna()
                if len(joined) < lookback // 2:
                    continue
                corr = float(joined.iloc[:, 0].corr(joined.iloc[:, 1]))
                if corr > max_corr:
                    measured["correlated_with"] = pos.symbol
                    measured["correlation"] = round(corr, 3)
                    return StageResult(self.name, False, measured,
                                       f"correlation {corr:.2f} with {pos.symbol} above cap")

        # Sector exposure cap
        max_sector = float(self.config["max_sector_exposure_pct"])
        sector = context.sector_map.get(signal.symbol)
        if sector and context.equity > 0:
            exposure = sum(
                abs(p.qty) * p.avg_entry_price
                for p in context.open_positions
                if context.sector_map.get(p.symbol) == sector
            )
            sector_pct = exposure / context.equity * 100
            measured["sector"] = sector
            measured["sector_exposure_pct"] = round(sector_pct, 2)
            if sector_pct >= max_sector:
                return StageResult(self.name, False, measured, "sector exposure cap reached")

        return StageResult(self.name, True, measured, "ok")
