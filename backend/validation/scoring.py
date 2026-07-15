"""Weighted confluence scoring (0-100).

Pure functions: weights and threshold live in configs/validation.yaml
(`confluence_score` section); stage5_confluence_score computes the component
values and this module turns them into the 0-100 score.
"""
from __future__ import annotations

from backend.core.config import YamlConfig, load_yaml_config


def load_weights(config: YamlConfig | None = None) -> dict[str, float]:
    cfg = config or load_yaml_config("validation")
    weights = cfg.get("confluence_score.weights", {}) or {}
    return {k: float(v) for k, v in weights.items()}


def weighted_confluence_score(components: dict[str, float],
                              weights: dict[str, float]) -> float:
    """Each component in [0, 1] (clamped); result normalized to 0-100."""
    total = sum(weights.values())
    if total <= 0:
        return 0.0
    raw = sum(w * min(1.0, max(0.0, components.get(k, 0.0)))
              for k, w in weights.items())
    return round(100.0 * raw / total, 1)
