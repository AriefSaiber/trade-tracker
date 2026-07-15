"""Stage 1 — Regime gate: signal regime must be in strategy allowed_regimes.
HIGH_VOL blocks all new entries unless the strategy explicitly opts in."""
from __future__ import annotations

from backend.core.events import Regime, Signal, StageResult
from backend.validation.base import ValidationStage
from backend.validation.context import ValidationContext


class RegimeGateStage(ValidationStage):
    name = "regime_gate"

    def validate(self, signal: Signal, context: ValidationContext) -> StageResult:
        current = context.regime.regime
        allowed = [Regime(r) for r in context.strategy_config.get("allowed_regimes", [])]
        opt_in_high_vol = bool(context.strategy_config.get("opt_in_high_vol", False))
        measured = {
            "current_regime": current.value,
            "allowed_regimes": [r.value for r in allowed],
            **context.regime.metrics,
        }
        if signal.direction == "FLAT":
            return StageResult(self.name, True, measured, "exit signals always pass")
        if current == Regime.HIGH_VOL and not opt_in_high_vol \
                and bool(self.config.get("high_vol_blocks_all", True)):
            return StageResult(self.name, False, measured, "HIGH_VOL blocks new entries")
        if current not in allowed:
            return StageResult(
                self.name, False, measured,
                f"regime {current.value} not in allowed_regimes",
            )
        return StageResult(self.name, True, measured, "ok")
