"""Pipeline end-to-end against the real configs/validation.yaml: a full pass
returns a ValidatedSignal with score + 8 stage results in yaml order; a
rejection short-circuits, and the funnel journals every stage with measured
values and thresholds either way."""
from backend.core.config import load_yaml_config
from backend.core.events import Regime, ValidatedSignal
from backend.portfolio.journal import TradeJournal
from backend.validation.funnel_logger import FunnelLogger
from backend.validation.pipeline import SignalValidationPipeline

from tests.conftest import make_daily_frame
from tests.validation.conftest import make_ctx, make_hourly_frame

YAML_STAGE_ORDER = load_yaml_config("validation").get("pipeline.stages")


def _passing_ctx(now):
    return make_ctx(now, history={
        ("NVDA", "1d"): make_daily_frame(trend=0.0025),
        ("NVDA", "1h"): make_hourly_frame(trend=0.002, last_volume_mult=3.0),
        ("SPY", "1d"): make_daily_frame(trend=0.0025, seed=3),
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
    assert len(pipeline.funnel.records) == 8
    for record in pipeline.funnel.records:
        assert "measured" in record and record["measured"] is not None
        assert isinstance(record["thresholds"], dict) and record["thresholds"]
