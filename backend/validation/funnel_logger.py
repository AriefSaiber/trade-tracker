"""Validation funnel logging: every stage result — pass or fail — is recorded
with measured values and the stage's configured thresholds, and journaled to
the TradeJournal so the funnel can be analyzed and A/B tested."""
from __future__ import annotations

from datetime import datetime, timezone

import structlog

from backend.core.events import Signal, StageResult
from backend.portfolio.journal import TradeJournal

log = structlog.get_logger("validation.funnel")


class FunnelLogger:
    def __init__(self, journal: TradeJournal | None = None) -> None:
        self.records: list[dict] = []
        self.journal = journal or TradeJournal()

    def record(self, signal: Signal, result: StageResult,
               thresholds: dict | None = None) -> None:
        entry = {
            "at": datetime.now(timezone.utc).isoformat(),
            "strategy_id": signal.strategy_id,
            "symbol": signal.symbol,
            "direction": signal.direction,
            "bar_time": signal.bar_time.isoformat(),
            "stage": result.stage,
            "passed": result.passed,
            "measured": result.measured,
            "thresholds": thresholds or {},
            "reason": result.reason,
        }
        self.records.append(entry)
        self.journal.record("validation_stage", entry)
        log.info(
            "stage_result",
            stage=result.stage,
            passed=result.passed,
            strategy_id=signal.strategy_id,
            symbol=signal.symbol,
            measured=result.measured,
            thresholds=entry["thresholds"],
            reason=result.reason,
        )
