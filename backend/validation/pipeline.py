"""Signal Validation Pipeline (MVP §8).

Runs stages 0..7 in order. Every stage result — pass or fail — is logged to
the funnel. Identical execution in backtest, paper, and live: the pipeline
receives a deterministic ValidationContext and has no mode branches.
"""
from __future__ import annotations

from datetime import datetime, timezone

import structlog

from backend.core.config import YamlConfig, load_yaml_config
from backend.core.events import Signal, StageResult, ValidatedSignal
from backend.validation.context import ValidationContext
from backend.validation.base import ValidationStage
from backend.validation.funnel_logger import FunnelLogger
from backend.validation.stage0_data_sanity import DataSanityStage
from backend.validation.stage1_regime_gate import RegimeGateStage
from backend.validation.stage2_mtf_alignment import MtfAlignmentStage
from backend.validation.stage3_volume_confirmation import VolumeConfirmationStage
from backend.validation.stage4_volatility_band import VolatilityBandStage
from backend.validation.stage5_confluence_score import ConfluenceScoreStage
from backend.validation.stage6_event_filter import EventFilterStage
from backend.validation.stage7_portfolio_correlation import PortfolioCorrelationStage

log = structlog.get_logger(__name__)

_STAGE_CLASSES: dict[str, type[ValidationStage]] = {
    "data_sanity": DataSanityStage,
    "regime_gate": RegimeGateStage,
    "mtf_alignment": MtfAlignmentStage,
    "volume_confirmation": VolumeConfirmationStage,
    "volatility_band": VolatilityBandStage,
    "confluence_score": ConfluenceScoreStage,
    "event_filter": EventFilterStage,
    "portfolio_correlation": PortfolioCorrelationStage,
}


class SignalValidationPipeline:
    def __init__(
        self,
        config: YamlConfig | None = None,
        market_config: YamlConfig | None = None,
        funnel: FunnelLogger | None = None,
    ) -> None:
        self._cfg = config or load_yaml_config("validation")
        market = market_config or load_yaml_config("market")
        self.funnel = funnel or FunnelLogger()

        self._stages: list[ValidationStage] = []
        for name in self._cfg.get("pipeline.stages", []):
            cls = _STAGE_CLASSES.get(name)
            if cls is None:
                raise ValueError(f"Unknown validation stage: {name}")
            stage_cfg = self._cfg.get(name, {}) or {}
            if cls is EventFilterStage:
                self._stages.append(EventFilterStage(stage_cfg, market.get("session", {})))
            else:
                self._stages.append(cls(stage_cfg))

    def validate(self, signal: Signal, ctx: ValidationContext) -> ValidatedSignal | None:
        """Returns a ValidatedSignal or None (rejected). All results logged."""
        results: list[StageResult] = []
        score = 0.0
        for stage in self._stages:
            result = stage.validate(signal, ctx)
            self.funnel.record(signal, result, thresholds=stage.config)
            results.append(result)
            if stage.name == "confluence_score" and "score" in result.measured:
                score = float(result.measured["score"])
            if not result.passed:
                log.info(
                    "signal_rejected",
                    strategy_id=signal.strategy_id,
                    symbol=signal.symbol,
                    stage=result.stage,
                    reason=result.reason,
                    measured=result.measured,
                )
                return None

        validated = ValidatedSignal(
            signal=signal,
            score=score,
            stage_results=results,
            regime=ctx.regime.regime.value,
            validated_at=datetime.now(timezone.utc),
        )
        log.info(
            "signal_validated",
            strategy_id=signal.strategy_id,
            symbol=signal.symbol,
            score=score,
            regime=validated.regime,
        )
        return validated
