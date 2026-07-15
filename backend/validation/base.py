"""Validation stage contract (CLAUDE.md §8): deterministic, returns StageResult."""
from __future__ import annotations

from abc import ABC, abstractmethod

from backend.core.events import Signal, StageResult
from backend.validation.context import ValidationContext


class ValidationStage(ABC):
    name: str = "unnamed"

    def __init__(self, config: dict) -> None:
        self.config = config

    @abstractmethod
    def validate(self, signal: Signal, context: ValidationContext) -> StageResult: ...

    def _skipped(self, reason: str) -> StageResult:
        return StageResult(stage=self.name, passed=True,
                           measured={"skipped": True}, reason=reason)

    def _override(self, context: ValidationContext, key: str, default=None):
        """Per-strategy stage override from the strategy's config.yaml:
        ``validation_overrides.<stage_name>.<key>``. Lets one strategy tune a
        threshold (e.g. crypto volume in coins, not shares) without touching
        the global configs/validation.yaml."""
        overrides = context.strategy_config.get("validation_overrides", {}) or {}
        return (overrides.get(self.name) or {}).get(key, default)
