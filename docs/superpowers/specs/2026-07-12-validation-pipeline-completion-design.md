# Validation Pipeline Completion — Design

Date: 2026-07-12
Status: executed autonomously (goal-driven session; acceptance criteria supplied by user)

## Goal (acceptance criteria)

1. `backend/validation/` contains 8 stage files `stage0_data_sanity.py` … `stage7_portfolio_correlation.py`,
   each implementing `validate(signal, context) -> StageResult`.
2. `backend/validation/pipeline.py` runs all stages in order and returns a `ValidatedSignal`
   with `score` and `stage_results`.
3. `backend/validation/scoring.py` computes a weighted confluence score 0–100 with weights
   loaded from `configs/validation.yaml`.
4. `backend/validation/funnel_logger.py` logs every pass/fail with measured values and
   thresholds to the trade journal.
5. The pipeline runs identically in backtest and live mode — no `if backtesting:` branches.
6. `pytest tests/validation/` passes with ≥ 3 test signals per stage
   (pass / fail-at-that-stage / edge case).

## Current state

The repo contains a half-finished migration: the 8 new-style `stageN_*.py` files exist and are
complete (criterion 1 ✓), but `pipeline.py` still imports a *duplicate* older implementation from
`backend/validation/stages/` using an `evaluate()` interface; `scoring.py` is a copy of the old
stage 5 rather than a scoring module; `FunnelLogger` records in memory + structlog but never
touches `TradeJournal`; `tests/validation/` covers only regime_gate and event_filter (against the
old modules) plus an interface test for the new modules.

## Decisions

- **Canonical stages:** the top-level `stageN_*.py` files with `validate(signal, context)` and
  `backend/validation/base.ValidationStage`. The `stages/` subpackage is deleted (duplicated
  logic, one edit away from divergence).
- **scoring.py** becomes the single owner of the score arithmetic:
  `compute_confluence_score(components, weights=None) -> float` — clamps components to [0, 1],
  normalizes by total weight so the result is always on a 0–100 scale, defaults weights from
  `configs/validation.yaml` via `load_scoring_config()`. `stage5_confluence_score.py` keeps the
  six component scorers and delegates the weighted sum to scoring.py.
- **pipeline.py** maps config stage names → new stage classes, calls `validate()`, logs every
  StageResult to the funnel, short-circuits on first failure returning `None`, otherwise returns
  `ValidatedSignal(score, stage_results, regime, validated_at)`. No mode flags anywhere in its
  API (guarded by a test on the `validate` signature).
- **funnel_logger.py** takes an optional `TradeJournal`; every stage result is appended to
  `self.records` (backtest engine reads this), emitted via structlog, and — when a journal is
  wired — recorded as kind `"validation_stage"`. Thresholds ride along inside `measured`
  (each stage already includes its limits: `max_age_seconds`, `rvol_min`, `band`, `threshold`,
  `max_heat_pct`, …).
- **worker.py** wires `FunnelLogger(journal=journal)` into the pipeline so live/paper funnel
  entries land in the trade journal. The backtest engine keeps its existing
  `pipeline.funnel.records` contract.

## Tests (tests/validation/)

One file per stage (`test_stage0_…` … `test_stage7_…`), each with at least pass / fail / edge
signals; `test_scoring.py` for the score math and yaml-loaded weights; `test_funnel_logger.py`
for journal wiring; `test_pipeline.py` for ordering, ValidatedSignal shape, short-circuiting and
the no-mode-branch guard; `test_interface.py` (existing) for the DoD interface check.
Market data is built deterministically (seeded rng / constructed spreads) in
`tests/validation/conftest.py`; the old `test_stages.py` cases are migrated into the per-stage
files and the file is removed.

## Out of scope

Stage 8 (`ml_gate`, Phase 2), DB persistence of journal entries, dashboard funnel endpoint.
