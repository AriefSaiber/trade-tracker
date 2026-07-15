# Validation Pipeline Definition-of-Done Alignment — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **STATUS: COMPLETED 2026-07-12.** Full suite: 152 passed; `tests/validation/`: 58 tests, ≥3 per stage. Deviations from the literal plan code: `tests/validation/conftest.py` evolved during Tasks 4–9 (`make_spread_frame` replaced the planned `make_range_frame`; `make_ctx` uses `strategy_config=`/`sectors=` kwargs), so Tasks 10–13 tests were adapted to those builders — the pipeline e2e passing context uses `make_daily_frame(trend=0.0025)` + `make_hourly_frame(trend=0.002, last_volume_mult=3.0)`, which clears every real-yaml threshold (ATR percentile 62.7, confluence score 79.9 vs threshold 70).

**Goal:** Align the existing signal-validation pipeline with the definition of done: 8 `stageN_*.py` files directly in `backend/validation/`, a `validate(signal, context) -> StageResult` stage interface, `scoring.py` as the weighted 0–100 confluence scorer fed from `configs/validation.yaml`, `funnel_logger.py` journaling every pass/fail with measured values **and thresholds** to the TradeJournal, and `tests/validation/` with ≥3 test signals per stage (pass / fail-at-stage / edge).

**Architecture:** This is mostly a behavior-preserving refactor of working code plus test buildout. Stage classes move from `backend/validation/stages/<name>.py` to `backend/validation/stageN_<name>.py`; the abstract method renames `evaluate` → `validate`. `scoring.py` becomes the pure weighted-score module consumed by the new `stage5_confluence_score.py`. `FunnelLogger` gains a `TradeJournal` sink and a `thresholds` field (the stage's YAML config section, passed by the pipeline). No mode branches anywhere (already true — keep it that way).

**Tech Stack:** Python 3.12, pandas/numpy, structlog, pytest. Config via `backend.core.config.load_yaml_config`.

## Global Constraints

- The 8 stage files, in pipeline order from `configs/validation.yaml`: `stage0_data_sanity.py`, `stage1_regime_gate.py`, `stage2_mtf_alignment.py`, `stage3_volume_confirmation.py`, `stage4_volatility_band.py`, `stage5_confluence_score.py`, `stage6_event_filter.py`, `stage7_portfolio_correlation.py` — all directly in `backend/validation/`.
- Stage interface: `validate(self, signal: Signal, context: ValidationContext) -> StageResult`.
- `backend/core/events.py` dataclasses are canonical — do NOT change `Signal`, `StageResult`, `ValidatedSignal` signatures.
- No `if backtesting:` / mode branches in `backend/validation/` (grep must stay clean).
- Weights and every threshold stay in `configs/validation.yaml` (no threshold literals in stage code).
- This directory is NOT a git repository — all commit steps are omitted; verification is via pytest.
- Run tests with: `python -m pytest tests/ -q` (full) and `python -m pytest tests/validation/ -q` (scoped).

## Current state (verified 2026-07-12)

- `backend/validation/stages/{base,data_sanity,regime_gate,mtf_alignment,volume_confirmation,volatility_band,event_filter,portfolio_correlation}.py` exist with `evaluate()`.
- Stage 5 lives in `backend/validation/scoring.py` as `ConfluenceScoreStage`.
- `pipeline.py` maps yaml stage names → classes, short-circuits on first failure, returns `ValidatedSignal | None`.
- `funnel_logger.py` keeps in-memory records + structlog only (no journal, no thresholds field).
- Tests: only regime_gate (5) and event_filter (4) covered, in `tests/validation/test_stages.py`.
- Full suite: 103 passed.

---

### Task 1: Relocate stages to `backend/validation/stageN_*.py` with `validate()` interface

**Files:**
- Create: `backend/validation/base.py` (moved from `stages/base.py`, method renamed)
- Create: `backend/validation/stage0_data_sanity.py` … `backend/validation/stage7_portfolio_correlation.py` (8 files; content moved from `stages/*.py` and `scoring.py`)
- Modify: `backend/validation/pipeline.py` (imports + `stage.validate(...)` call)
- Modify: `tests/validation/test_stages.py` (imports + `.validate(` calls)
- Delete: `backend/validation/stages/` (whole package) after the moves
- Test: `tests/validation/test_interface.py`

**Interfaces:**
- Consumes: existing stage class bodies (unchanged logic).
- Produces: `backend.validation.base.ValidationStage` with abstract `validate(signal, context) -> StageResult`; classes `DataSanityStage`, `RegimeGateStage`, `MtfAlignmentStage`, `VolumeConfirmationStage`, `VolatilityBandStage`, `ConfluenceScoreStage`, `EventFilterStage`, `PortfolioCorrelationStage` importable from their `stageN_*` modules.

- [x] **Step 1: Write the failing interface test** — `tests/validation/test_interface.py`:

```python
"""DoD guard: 8 stageN modules, each stage implements validate(signal, context)."""
import inspect

import pytest

from backend.validation.base import ValidationStage

STAGE_MODULES = [
    ("backend.validation.stage0_data_sanity", "DataSanityStage"),
    ("backend.validation.stage1_regime_gate", "RegimeGateStage"),
    ("backend.validation.stage2_mtf_alignment", "MtfAlignmentStage"),
    ("backend.validation.stage3_volume_confirmation", "VolumeConfirmationStage"),
    ("backend.validation.stage4_volatility_band", "VolatilityBandStage"),
    ("backend.validation.stage5_confluence_score", "ConfluenceScoreStage"),
    ("backend.validation.stage6_event_filter", "EventFilterStage"),
    ("backend.validation.stage7_portfolio_correlation", "PortfolioCorrelationStage"),
]


@pytest.mark.parametrize("module_name,class_name", STAGE_MODULES)
def test_stage_module_exports_validate(module_name, class_name):
    module = __import__(module_name, fromlist=[class_name])
    cls = getattr(module, class_name)
    assert issubclass(cls, ValidationStage)
    sig = inspect.signature(cls.validate)
    assert list(sig.parameters) == ["self", "signal", "context"]
```

- [x] **Step 2: Run it** — `python -m pytest tests/validation/test_interface.py -q` — expect FAIL (ModuleNotFoundError `backend.validation.base`).
- [x] **Step 3: Create `backend/validation/base.py`** — content of `stages/base.py` with the abstract method renamed:

```python
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
```

- [x] **Step 4: Create the 8 stage modules.** Each is the byte-for-byte content of its source file with exactly these edits: (a) `from backend.validation.stages.base import ValidationStage` → `from backend.validation.base import ValidationStage`; (b) `def evaluate(self, signal: Signal, ctx: ValidationContext)` → `def validate(self, signal: Signal, context: ValidationContext)` and every `ctx.` in the method body → `context.`.

| New file | Source |
|---|---|
| `stage0_data_sanity.py` | `stages/data_sanity.py` |
| `stage1_regime_gate.py` | `stages/regime_gate.py` |
| `stage2_mtf_alignment.py` | `stages/mtf_alignment.py` |
| `stage3_volume_confirmation.py` | `stages/volume_confirmation.py` |
| `stage4_volatility_band.py` | `stages/volatility_band.py` |
| `stage5_confluence_score.py` | `scoring.py` (class + helpers; scoring.py is rebuilt in Task 2) |
| `stage6_event_filter.py` | `stages/event_filter.py` (keep its custom `__init__(config, session_config)`) |
| `stage7_portfolio_correlation.py` | `stages/portfolio_correlation.py` |

  For `stage5_confluence_score.py` also rename helper param `ctx` → `context` where it is `_breadth(self, ctx, is_long)` and its `ctx.` uses.
- [x] **Step 5: Update `pipeline.py`** — replace the import block (lines 17–25) with:

```python
from backend.validation.base import ValidationStage
from backend.validation.stage0_data_sanity import DataSanityStage
from backend.validation.stage1_regime_gate import RegimeGateStage
from backend.validation.stage2_mtf_alignment import MtfAlignmentStage
from backend.validation.stage3_volume_confirmation import VolumeConfirmationStage
from backend.validation.stage4_volatility_band import VolatilityBandStage
from backend.validation.stage5_confluence_score import ConfluenceScoreStage
from backend.validation.stage6_event_filter import EventFilterStage
from backend.validation.stage7_portfolio_correlation import PortfolioCorrelationStage
```

  and change `result = stage.evaluate(signal, ctx)` → `result = stage.validate(signal, ctx)`.
- [x] **Step 6: Update `tests/validation/test_stages.py`** — imports → `backend.validation.stage1_regime_gate` / `backend.validation.stage6_event_filter`; every `.evaluate(` → `.validate(`. (This file is dissolved into per-stage files in Tasks 6/11.)
- [x] **Step 7: Delete `backend/validation/stages/`** (all files incl. `__init__.py`).
- [x] **Step 8: Run full suite** — `python -m pytest tests/ -q` — expect 103 + 8 = 111 passed.

---

### Task 2: `scoring.py` = pure weighted confluence scorer; stage 5 consumes it

**Files:**
- Rewrite: `backend/validation/scoring.py`
- Modify: `backend/validation/stage5_confluence_score.py` (use scoring functions)
- Test: `tests/validation/test_scoring.py`

**Interfaces:**
- Produces: `scoring.load_weights(config: YamlConfig | None = None) -> dict[str, float]` (reads `confluence_score.weights` from `configs/validation.yaml` by default); `scoring.weighted_confluence_score(components: dict[str, float], weights: dict[str, float]) -> float` (0–100, components clamped to [0,1], normalized by weight sum).

- [x] **Step 1: Write failing tests** — `tests/validation/test_scoring.py`:

```python
"""scoring.py: weighted confluence score 0-100, weights from configs/validation.yaml."""
from backend.core.config import load_yaml_config
from backend.validation.scoring import load_weights, weighted_confluence_score


def test_weights_come_from_validation_yaml():
    weights = load_weights()
    yaml_weights = load_yaml_config("validation").get("confluence_score.weights")
    assert weights == {k: float(v) for k, v in yaml_weights.items()}
    assert sum(weights.values()) > 0


def test_perfect_components_score_100():
    weights = load_weights()
    assert weighted_confluence_score({k: 1.0 for k in weights}, weights) == 100.0


def test_zero_components_score_0():
    weights = load_weights()
    assert weighted_confluence_score({k: 0.0 for k in weights}, weights) == 0.0


def test_partial_score_is_weighted_sum():
    weights = {"a": 30.0, "b": 70.0}
    assert weighted_confluence_score({"a": 1.0, "b": 0.5}, weights) == 65.0


def test_components_clamped_and_missing_treated_as_zero():
    weights = {"a": 50.0, "b": 50.0}
    assert weighted_confluence_score({"a": 2.0}, weights) == 50.0
    assert weighted_confluence_score({}, weights) == 0.0


def test_empty_weights_score_0():
    assert weighted_confluence_score({"a": 1.0}, {}) == 0.0
```

- [x] **Step 2: Run** — `python -m pytest tests/validation/test_scoring.py -q` — expect FAIL (ImportError).
- [x] **Step 3: Rewrite `backend/validation/scoring.py`:**

```python
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
```

- [x] **Step 4: Update `stage5_confluence_score.py`** — import `from backend.validation.scoring import weighted_confluence_score`; replace `score = sum(weights[k] * components.get(k, 0.0) for k in weights)` with `score = weighted_confluence_score(components, {k: float(v) for k, v in weights.items()})` and use `score` (already 0–100) in `measured`/threshold check unchanged.
- [x] **Step 5: Run full suite** — `python -m pytest tests/ -q` — expect all green.

---

### Task 3: FunnelLogger → TradeJournal, with thresholds

**Files:**
- Modify: `backend/validation/funnel_logger.py`
- Modify: `backend/validation/pipeline.py` (pass `thresholds=stage.config`)
- Modify: `backend/worker.py` (share the worker's `TradeJournal` with the pipeline)
- Test: `tests/validation/test_funnel_logger.py`

**Interfaces:**
- Produces: `FunnelLogger(journal: TradeJournal | None = None)` — always has a journal (`self.journal = journal or TradeJournal()`); `record(signal, result, thresholds: dict | None = None)` — entry dict gains `"thresholds"` key and is appended to `self.records` AND journaled as `journal.record("validation_stage", entry)`.

- [x] **Step 1: Write failing tests** — `tests/validation/test_funnel_logger.py`:

```python
"""Funnel logs every pass/fail with measured values AND thresholds to the journal."""
from backend.core.events import StageResult
from backend.portfolio.journal import TradeJournal
from backend.validation.funnel_logger import FunnelLogger


def test_record_includes_measured_and_thresholds(signal):
    funnel = FunnelLogger()
    result = StageResult("volatility_band", False,
                         {"atr_percentile": 95.2, "band": [20.0, 90.0]},
                         "volatility too high")
    funnel.record(signal, result, thresholds={"percentile_min": 20, "percentile_max": 90})
    entry = funnel.records[-1]
    assert entry["stage"] == "volatility_band"
    assert entry["passed"] is False
    assert entry["measured"]["atr_percentile"] == 95.2
    assert entry["thresholds"] == {"percentile_min": 20, "percentile_max": 90}


def test_every_record_lands_in_trade_journal(signal):
    journal = TradeJournal()
    funnel = FunnelLogger(journal)
    ok = StageResult("data_sanity", True, {"bar_age_seconds": 60.0}, "ok")
    bad = StageResult("regime_gate", False, {"current_regime": "RANGE"}, "wrong regime")
    funnel.record(signal, ok, thresholds={"max_bar_age_multiplier": 2.0})
    funnel.record(signal, bad, thresholds={"high_vol_blocks_all": True})
    kinds = [e["kind"] for e in journal.entries]
    assert kinds == ["validation_stage", "validation_stage"]
    payloads = [e["payload"] for e in journal.entries]
    assert payloads[0]["passed"] is True and payloads[1]["passed"] is False
    assert payloads[1]["thresholds"] == {"high_vol_blocks_all": True}


def test_default_journal_created_when_none_given(signal):
    funnel = FunnelLogger()
    funnel.record(signal, StageResult("data_sanity", True, {}, "ok"))
    assert len(funnel.journal.entries) == 1
```

- [x] **Step 2: Run** — expect FAIL (`TypeError`/`AttributeError`).
- [x] **Step 3: Rewrite `funnel_logger.py`:**

```python
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
```

- [x] **Step 4: `pipeline.py`** — `self.funnel.record(signal, result)` → `self.funnel.record(signal, result, thresholds=stage.config)`.
- [x] **Step 5: `worker.py`** — add `from backend.validation.funnel_logger import FunnelLogger`; change `pipeline = SignalValidationPipeline()` → `pipeline = SignalValidationPipeline(funnel=FunnelLogger(journal))`.
- [x] **Step 6: Run full suite** — expect all green.

---

### Task 4: Test data builders in `tests/validation/conftest.py`

**Files:**
- Create: `tests/validation/conftest.py`

**Interfaces:**
- Produces: `make_hourly_frame(bars=200, up_ret=0.003, down_ret=-0.0015, up_vol=2_000_000.0, down_vol=1_000_000.0, last_vol_mult=3.0, end="2026-07-10 14:00", start_price=100.0) -> pd.DataFrame` — alternating up/down closes (net uptrend, RSI in the 40–70 band, OBV rising because up-bars carry more volume), last bar volume spiked for RVOL; `make_range_frame(days=300, ranges: list[float] | None = None, trend=0.0, seed=11, start_price=100.0) -> pd.DataFrame` — daily frame with explicitly controlled high-low ranges so ATR percentile is steerable; `make_ctx(now, regime=Regime.TREND_UP, history=None, strategy_cfg=None, **kwargs) -> ValidationContext`; module constant `STRATEGY_CFG`.

- [x] **Step 1: Create `tests/validation/conftest.py`:**

```python
"""Shared builders for validation-stage tests. All frames deterministic."""
from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

from backend.core.events import Position, Regime, RegimeState
from backend.validation.context import ValidationContext

STRATEGY_CFG = {
    "strategy_id": "trend_pullback",
    "interval": "1h",
    "allowed_regimes": ["TREND_UP"],
    "opt_in_high_vol": False,
}


def make_hourly_frame(bars: int = 200, up_ret: float = 0.003,
                      down_ret: float = -0.0015, up_vol: float = 2_000_000.0,
                      down_vol: float = 1_000_000.0, last_vol_mult: float = 3.0,
                      end: str = "2026-07-10 14:00",
                      start_price: float = 100.0) -> pd.DataFrame:
    """Alternating up/down bars: net uptrend, RSI mid-band, OBV rising
    (up-bars carry heavier volume), last bar volume-spiked for RVOL."""
    rets = np.array([up_ret if i % 2 == 0 else down_ret for i in range(bars)])
    close = start_price * np.cumprod(1 + rets)
    volume = np.array([up_vol if i % 2 == 0 else down_vol for i in range(bars)])
    volume[-1] = up_vol * last_vol_mult
    high = close * 1.002
    low = close * 0.998
    open_ = np.clip(np.roll(close, 1), low, high)
    open_[0] = start_price
    idx = pd.date_range(end=end, periods=bars, freq="h", tz="UTC")
    return pd.DataFrame({"open": open_, "high": high, "low": low,
                         "close": close, "volume": volume}, index=idx)


def make_range_frame(days: int = 300, ranges: list[float] | None = None,
                     trend: float = 0.0, seed: int = 11,
                     start_price: float = 100.0) -> pd.DataFrame:
    """Daily frame with explicit absolute high-low ranges (per bar) so the
    ATR percentile is controllable. `ranges` defaults to uniform 2..4."""
    rng = np.random.default_rng(seed)
    if ranges is None:
        ranges = list(rng.uniform(2.0, 4.0, days))
    close = start_price * np.cumprod(1 + np.full(days, trend))
    half = np.array(ranges) / 2.0
    high = close + half
    low = np.maximum(close - half, 0.01)
    open_ = np.clip(np.roll(close, 1), low, high)
    open_[0] = start_price
    volume = np.full(days, 2_000_000.0)
    idx = pd.date_range(end="2026-07-10", periods=days, freq="B", tz="UTC")
    return pd.DataFrame({"open": open_, "high": high, "low": low,
                         "close": close, "volume": volume}, index=idx)


def make_ctx(now: datetime, regime: Regime = Regime.TREND_UP,
             history: dict | None = None, strategy_cfg: dict | None = None,
             open_positions: list[Position] | None = None,
             equity: float = 100_000.0,
             earnings: dict | None = None,
             sector_map: dict | None = None) -> ValidationContext:
    return ValidationContext(
        now=now,
        regime=RegimeState("SPY", regime, now, {"adx": 30.0}),
        benchmark_symbol="SPY",
        history=history or {},
        strategy_config=strategy_cfg or dict(STRATEGY_CFG),
        open_positions=open_positions or [],
        equity=equity,
        earnings_calendar=earnings or {},
        sector_map=sector_map or {},
    )
```

- [x] **Step 2: Sanity-run existing validation tests** — `python -m pytest tests/validation/ -q` — expect green (conftest adds fixtures only).

---

### Task 5: `tests/validation/test_stage0_data_sanity.py`

Signals: pass = fresh sane data; fail-at-stage = stale last bar; edges = empty history, zero-volume bar.

- [x] **Step 1: Write tests:**

```python
from backend.validation.stage0_data_sanity import DataSanityStage

from tests.validation.conftest import make_ctx, make_hourly_frame

CFG = {"max_bar_age_multiplier": 2.0, "max_gap_bars": 3, "min_volume": 1}


def _ctx(now, frame):
    return make_ctx(now, history={("NVDA", "1h"): frame})


def test_fresh_sane_data_passes(signal, now):
    result = DataSanityStage(CFG).validate(signal, _ctx(now, make_hourly_frame()))
    assert result.passed
    assert result.measured["bar_age_seconds"] <= result.measured["max_age_seconds"]


def test_stale_data_fails(signal, now):
    stale = make_hourly_frame(end="2026-07-09 14:00")   # >2x interval old
    result = DataSanityStage(CFG).validate(signal, _ctx(now, stale))
    assert not result.passed
    assert result.reason == "stale data"


def test_no_data_fails(signal, now):
    result = DataSanityStage(CFG).validate(signal, make_ctx(now))
    assert not result.passed
    assert result.reason == "no data for symbol"


def test_zero_volume_bar_fails(signal, now):
    frame = make_hourly_frame()
    frame.iloc[-2, frame.columns.get_loc("volume")] = 0.0
    result = DataSanityStage(CFG).validate(signal, _ctx(now, frame))
    assert not result.passed
    assert "zero-volume" in result.reason
```

- [x] **Step 2: Run** — `python -m pytest tests/validation/test_stage0_data_sanity.py -q` — expect 4 passed (implementation exists; a failure means the test data needs fixing, not the stage).

---

### Task 6: `tests/validation/test_stage1_regime_gate.py`

Move the five regime tests out of `test_stages.py` unchanged (imports updated to `backend.validation.stage1_regime_gate`, `.validate(`, ctx built via local `make_ctx`). Signals: pass = TREND_UP allowed; fail-at-stage = RANGE not allowed; edges = HIGH_VOL blocks despite allowed_regimes, HIGH_VOL opt-in passes, FLAT always passes.

- [x] **Step 1: Write the file** (same 5 test bodies as current `test_stages.py:29-62`, using `make_ctx(now, regime=..., history={("NVDA", "1d"): make_daily_frame()})` from validation conftest + tests.conftest).
- [x] **Step 2: Run** — expect 5 passed.
- [x] **Step 3: Delete the regime-gate tests from `test_stages.py`.** Run `python -m pytest tests/validation/ -q` — green.

---

### Task 7: `tests/validation/test_stage2_mtf_alignment.py`

Signals: pass = LONG, daily above EMA200 + rising hourly EMA50; fail-at-stage = LONG in daily downtrend; edge = insufficient hourly history.

- [x] **Step 1: Write tests:**

```python
from backend.validation.stage2_mtf_alignment import MtfAlignmentStage

from tests.conftest import make_daily_frame
from tests.validation.conftest import make_ctx, make_hourly_frame

CFG = {"long_requires_above_daily_ema": 200,
       "long_requires_rising_hourly_ema": 50, "ema_slope_lookback": 5}


def _ctx(now, daily, hourly):
    return make_ctx(now, history={("NVDA", "1d"): daily, ("NVDA", "1h"): hourly})


def test_long_with_aligned_timeframes_passes(signal, now):
    ctx = _ctx(now, make_daily_frame(trend=0.0025), make_hourly_frame())
    result = MtfAlignmentStage(CFG).validate(signal, ctx)
    assert result.passed
    assert result.measured["price"] > result.measured["daily_ema"]


def test_long_against_daily_downtrend_fails(signal, now):
    ctx = _ctx(now, make_daily_frame(trend=-0.0025), make_hourly_frame())
    result = MtfAlignmentStage(CFG).validate(signal, ctx)
    assert not result.passed
    assert "against higher-timeframe trend" in result.reason


def test_insufficient_history_fails(signal, now):
    ctx = _ctx(now, make_daily_frame(days=50), make_hourly_frame(bars=20))
    result = MtfAlignmentStage(CFG).validate(signal, ctx)
    assert not result.passed
    assert "insufficient history" in result.reason
```

- [x] **Step 2: Run** — expect 3 passed. If the pass test fails on `hourly_ema_slope`, increase `up_ret` in `make_hourly_frame` call (net-uptrend slope must be > 0).

---

### Task 8: `tests/validation/test_stage3_volume_confirmation.py`

Signals: pass = RVOL spike + OBV rising; fail-at-stage = low RVOL; edges = OBV disagreement, strategy skip override.

- [x] **Step 1: Write tests:**

```python
from backend.validation.stage3_volume_confirmation import VolumeConfirmationStage

from tests.validation.conftest import STRATEGY_CFG, make_ctx, make_hourly_frame

CFG = {"relative_volume_min": 1.2, "rvol_lookback_bars": 20,
       "require_obv_agreement": True}


def _ctx(now, frame, strategy_cfg=None):
    return make_ctx(now, history={("NVDA", "1h"): frame}, strategy_cfg=strategy_cfg)


def test_volume_spike_with_obv_agreement_passes(signal, now):
    result = VolumeConfirmationStage(CFG).validate(signal, _ctx(now, make_hourly_frame()))
    assert result.passed
    assert result.measured["rvol"] >= 1.2
    assert result.measured["obv_agrees"]


def test_low_relative_volume_fails(signal, now):
    quiet = make_hourly_frame(last_vol_mult=0.1)
    result = VolumeConfirmationStage(CFG).validate(signal, _ctx(now, quiet))
    assert not result.passed
    assert "relative volume below minimum" in result.reason


def test_obv_disagreement_fails(signal, now):
    # heavier volume on down-bars: OBV falls while price nets upward
    bearish_flow = make_hourly_frame(up_vol=1_000_000.0, down_vol=2_000_000.0,
                                     last_vol_mult=6.0)
    result = VolumeConfirmationStage(CFG).validate(signal, _ctx(now, bearish_flow))
    assert not result.passed
    assert "OBV disagrees" in result.reason


def test_strategy_skip_override(signal, now):
    cfg = dict(STRATEGY_CFG,
               validation_overrides={"volume_confirmation": {"skip": True}})
    result = VolumeConfirmationStage(CFG).validate(
        signal, _ctx(now, make_hourly_frame(), strategy_cfg=cfg))
    assert result.passed
    assert result.measured.get("skipped") is True
```

- [x] **Step 2: Run** — expect 4 passed. Note: in `test_obv_disagreement_fails` the last bar is an up-bar with 6x volume — check the RVOL still ≥ 1.2 so the failure is specifically OBV; if OBV slope ends positive, raise `down_vol` further.

---

### Task 9: `tests/validation/test_stage4_volatility_band.py`

Signals: pass = ATR mid-percentile; fail-at-stage = volatility spike (percentile > 90); edges = volatility collapse (< 20), insufficient history.

- [x] **Step 1: Write tests:**

```python
from backend.validation.stage4_volatility_band import VolatilityBandStage

from tests.validation.conftest import make_ctx, make_range_frame

CFG = {"atr_period": 14, "percentile_min": 20, "percentile_max": 90,
       "percentile_lookback_days": 252}


def _ctx(now, daily):
    return make_ctx(now, history={("NVDA", "1d"): daily})


def test_mid_band_volatility_passes(signal, now):
    daily = make_range_frame()   # ranges uniform 2..4 -> last ATR near median
    result = VolatilityBandStage(CFG).validate(signal, _ctx(now, daily))
    assert result.passed
    assert 20 <= result.measured["atr_percentile"] <= 90


def test_volatility_spike_fails(signal, now):
    ranges = [2.0] * 280 + [8.0] * 20    # recent ranges 4x historic
    daily = make_range_frame(ranges=ranges)
    result = VolatilityBandStage(CFG).validate(signal, _ctx(now, daily))
    assert not result.passed
    assert result.reason == "volatility too high"


def test_volatility_collapse_fails(signal, now):
    ranges = [4.0] * 270 + [0.5] * 30    # recent ranges collapsed
    daily = make_range_frame(ranges=ranges)
    result = VolatilityBandStage(CFG).validate(signal, _ctx(now, daily))
    assert not result.passed
    assert result.reason == "volatility too low"


def test_insufficient_history_fails(signal, now):
    result = VolatilityBandStage(CFG).validate(signal, _ctx(now, make_range_frame(days=15)))
    assert not result.passed
    assert "insufficient history" in result.reason
```

- [x] **Step 2: Run** — expect 4 passed. If the mid-band test lands outside [20, 90], change `make_range_frame` default seed until the last ATR sits mid-distribution (documented tuning knob; the frame stays deterministic).

---

### Task 10: `tests/validation/test_stage5_confluence_score.py`

Signals: pass = strong multi-factor long ≥ threshold; fail-at-stage = weak/choppy setup < threshold; edges = FLAT exit skips, insufficient history scores 0.

- [x] **Step 1: Write tests:**

```python
from backend.core.config import load_yaml_config
from backend.core.events import Signal
from backend.validation.stage5_confluence_score import ConfluenceScoreStage

from tests.conftest import make_daily_frame
from tests.validation.conftest import make_ctx, make_hourly_frame, make_range_frame

CFG = load_yaml_config("validation").get("confluence_score")


def _ctx(now, daily, hourly, bench):
    return make_ctx(now, history={
        ("NVDA", "1d"): daily, ("NVDA", "1h"): hourly, ("SPY", "1d"): bench,
    })


def test_strong_long_setup_scores_above_threshold(signal, now):
    ctx = _ctx(now,
               daily=make_range_frame(trend=0.002),
               hourly=make_hourly_frame(),
               bench=make_range_frame(trend=0.002, seed=3))
    result = ConfluenceScoreStage(CFG).validate(signal, ctx)
    assert result.passed
    assert result.measured["score"] >= result.measured["threshold"]
    assert set(result.measured["components"]) == set(CFG["weights"])


def test_weak_setup_scores_below_threshold(signal, now):
    ctx = _ctx(now,
               daily=make_range_frame(trend=-0.002),          # downtrend vs LONG
               hourly=make_hourly_frame(up_ret=0.0005, down_ret=-0.002,
                                        up_vol=1_000_000.0, down_vol=2_000_000.0,
                                        last_vol_mult=0.3),   # weak momentum+volume
               bench=make_range_frame(trend=-0.002, seed=3))
    result = ConfluenceScoreStage(CFG).validate(signal, ctx)
    assert not result.passed
    assert result.measured["score"] < result.measured["threshold"]


def test_exit_signal_skips(now):
    exit_signal = Signal("trend_pullback", "NVDA", "FLAT", 1.0, now, {})
    result = ConfluenceScoreStage(CFG).validate(exit_signal, make_ctx(now))
    assert result.passed
    assert result.measured.get("skipped") is True


def test_insufficient_history_scores_zero(signal, now):
    ctx = _ctx(now, daily=make_daily_frame(days=50),
               hourly=make_hourly_frame(bars=10), bench=make_daily_frame(days=1))
    result = ConfluenceScoreStage(CFG).validate(signal, ctx)
    assert not result.passed
    assert result.measured["score"] < result.measured["threshold"]
```

- [x] **Step 2: Run** — expect 4 passed. If the strong-setup score lands just under threshold, tune `make_hourly_frame` (`up_ret`/`down_ret` for RSI 40–70 with MACD hist > 0) — components are printed in `measured["components"]`, so the shortfall is visible per component.

---

### Task 11: `tests/validation/test_stage6_event_filter.py`

Move the four event tests from `test_stages.py`; add close-blackout and FLAT edges. Signals: pass = midday; fail-at-stage = open blackout, earnings ≤ 2 days, macro date; edges = close blackout, FLAT skips.

- [x] **Step 1: Write the file** — existing 4 tests re-homed (imports → `backend.validation.stage6_event_filter`, `.validate(`), plus:

```python
def test_session_close_blackout(signal):
    # 15:55 ET = 19:55 UTC (July, EDT); close blackout = last 10 min
    now = datetime(2026, 7, 10, 19, 55, tzinfo=timezone.utc)
    signal.bar_time = now
    stage = EventFilterStage(EVENT_CFG, SESSION)
    result = stage.validate(signal, _ctx_at(now))
    assert not result.passed
    assert "close blackout" in result.reason


def test_exit_signal_skips_event_filter(now):
    exit_signal = Signal("trend_pullback", "NVDA", "FLAT", 1.0, now, {})
    stage = EventFilterStage(EVENT_CFG, SESSION)
    assert stage.validate(exit_signal, _ctx_at(now)).passed
```

- [x] **Step 2: Run** — expect 6 passed.
- [x] **Step 3: Delete `tests/validation/test_stages.py`** (now fully dissolved). Run `python -m pytest tests/validation/ -q` — green.

---

### Task 12: `tests/validation/test_stage7_portfolio_correlation.py`

Signals: pass = clean book; fail-at-stage = heat cap; edges = correlation cap, sector cap, FLAT skips.

- [x] **Step 1: Write tests:**

```python
from backend.core.events import Position, Signal
from backend.validation.stage7_portfolio_correlation import PortfolioCorrelationStage

from tests.conftest import make_daily_frame
from tests.validation.conftest import make_ctx

CFG = {"max_heat_pct": 5.0, "max_correlation": 0.7,
       "correlation_lookback_days": 60, "max_sector_exposure_pct": 30.0}


def test_clean_book_passes(signal, now):
    ctx = make_ctx(now, history={("NVDA", "1d"): make_daily_frame()})
    result = PortfolioCorrelationStage(CFG).validate(signal, ctx)
    assert result.passed
    assert result.measured["portfolio_heat_pct"] == 0.0


def test_heat_cap_fails(signal, now):
    # open risk: (100 - 90) * 500 = 5000 = 5.0% of 100k -> at cap
    positions = [Position("AMD", 500, 100.0, "trend_pullback", stop_loss=90.0)]
    ctx = make_ctx(now, history={("NVDA", "1d"): make_daily_frame()},
                   open_positions=positions)
    result = PortfolioCorrelationStage(CFG).validate(signal, ctx)
    assert not result.passed
    assert "heat cap" in result.reason


def test_correlation_cap_fails(signal, now):
    frame = make_daily_frame()          # identical frames -> corr 1.0
    positions = [Position("AMD", 10, 100.0, "trend_pullback", stop_loss=99.0)]
    ctx = make_ctx(now, history={("NVDA", "1d"): frame, ("AMD", "1d"): frame},
                   open_positions=positions)
    result = PortfolioCorrelationStage(CFG).validate(signal, ctx)
    assert not result.passed
    assert result.measured["correlated_with"] == "AMD"
    assert result.measured["correlation"] > 0.7


def test_sector_cap_fails(signal, now):
    # AMD exposure: 350 * 100 = 35k = 35% of equity, same sector as NVDA
    positions = [Position("AMD", 350, 100.0, "trend_pullback", stop_loss=99.9)]
    ctx = make_ctx(now,
                   history={("NVDA", "1d"): make_daily_frame(seed=5),
                            ("AMD", "1d"): make_daily_frame(seed=9)},
                   open_positions=positions,
                   sector_map={"NVDA": "semis", "AMD": "semis"})
    result = PortfolioCorrelationStage(CFG).validate(signal, ctx)
    assert not result.passed
    assert "sector exposure cap" in result.reason


def test_exit_signal_skips(now):
    exit_signal = Signal("trend_pullback", "NVDA", "FLAT", 1.0, now, {})
    result = PortfolioCorrelationStage(CFG).validate(exit_signal, make_ctx(now))
    assert result.passed
```

- [x] **Step 2: Run** — expect 5 passed. Note `test_sector_cap_fails` uses different seeds so correlation stays below 0.7 and the failure is specifically the sector cap; note the stop-losses keep heat under 5% in the correlation/sector tests.

---

### Task 13: Pipeline end-to-end tests — `tests/validation/test_pipeline.py`

Covers: full pass through all 8 stages against the REAL `configs/validation.yaml` (returns `ValidatedSignal` with score + 8 stage_results in yaml order), rejection short-circuit (funnel logs the failing stage with thresholds; returns None), journal receives every record.

- [x] **Step 1: Write tests:**

```python
from backend.core.config import load_yaml_config
from backend.core.events import ValidatedSignal
from backend.portfolio.journal import TradeJournal
from backend.validation.funnel_logger import FunnelLogger
from backend.validation.pipeline import SignalValidationPipeline
from backend.validation.context import ValidationContext  # noqa: F401 (doc anchor)

from backend.core.events import Regime
from tests.validation.conftest import make_ctx, make_hourly_frame, make_range_frame

YAML_STAGE_ORDER = load_yaml_config("validation").get("pipeline.stages")


def _passing_ctx(now):
    return make_ctx(now, history={
        ("NVDA", "1d"): make_range_frame(trend=0.002),
        ("NVDA", "1h"): make_hourly_frame(),
        ("SPY", "1d"): make_range_frame(trend=0.002, seed=3),
    })


def _pipeline():
    journal = TradeJournal()
    return SignalValidationPipeline(funnel=FunnelLogger(journal)), journal


def test_valid_signal_passes_all_8_stages(signal, now):
    pipeline, journal = _pipeline()
    validated = pipeline.validate(signal, _passing_ctx(now))
    assert isinstance(validated, ValidatedSignal)
    assert 0 < validated.score <= 100
    assert [r.stage for r in validated.stage_results] == YAML_STAGE_ORDER
    assert all(r.passed for r in validated.stage_results)
    assert len(pipeline.funnel.records) == 8
    assert len(journal.entries) == 8
    assert all(e["kind"] == "validation_stage" for e in journal.entries)


def test_rejected_signal_short_circuits_and_logs(signal, now):
    pipeline, journal = _pipeline()
    ctx = _passing_ctx(now)
    ctx.regime.regime = Regime.RANGE          # dies at stage 1
    assert pipeline.validate(signal, ctx) is None
    assert [r["stage"] for r in pipeline.funnel.records] == ["data_sanity", "regime_gate"]
    last = pipeline.funnel.records[-1]
    assert last["passed"] is False
    assert last["thresholds"] == load_yaml_config("validation").get("regime_gate")
    assert journal.entries[-1]["payload"]["stage"] == "regime_gate"


def test_every_funnel_record_has_measured_and_thresholds(signal, now):
    pipeline, _ = _pipeline()
    pipeline.validate(signal, _passing_ctx(now))
    for record in pipeline.funnel.records:
        assert "measured" in record and record["measured"] is not None
        assert "thresholds" in record and isinstance(record["thresholds"], dict)
```

- [x] **Step 2: Run** — `python -m pytest tests/validation/test_pipeline.py -q`. The end-to-end pass test exercises real yaml thresholds; if a stage rejects, its funnel record names the stage + measured values — tune the conftest builders (same knobs as Tasks 9/10) until all 8 pass. Data changes only; never touch `configs/validation.yaml` to make tests pass.

---

### Task 14: Final verification against the definition of done

- [x] **Step 1:** `python -m pytest tests/ -q` — everything green.
- [x] **Step 2:** `python -m pytest tests/validation/ -q` — count ≥ 3 tests per stage (0–7).
- [x] **Step 3:** Confirm the 8 stage files exist: `ls backend/validation/stage*.py` → exactly 8 files, `stage0_data_sanity.py` … `stage7_portfolio_correlation.py`.
- [x] **Step 4:** Grep `backend/validation/` for `backtest|live_trading|is_backtest` → only docstring mentions, no branches.
- [x] **Step 5:** Confirm `backend/validation/stages/` is gone and no import of `backend.validation.stages` remains (grep).

## Self-Review Notes

- Spec coverage: 8 stage files (T1), validate() interface (T1), pipeline order + ValidatedSignal score/stage_results (existing + T13), scoring.py weighted 0–100 from yaml (T2), funnel → journal with measured + thresholds (T3), no mode branches (T14 grep), ≥3 signals per stage (T5–T12), pipeline runs identically in backtest/live (unchanged — DI only, verified by T14 grep).
- Type consistency: `validate(self, signal, context)` used consistently; `FunnelLogger.record(signal, result, thresholds)` matches pipeline call and tests; `weighted_confluence_score(components, weights)` matches stage5 call.
- Calibration risk (T9/T10/T13 pass-cases) is explicitly flagged with per-test tuning knobs; frames remain deterministic.
