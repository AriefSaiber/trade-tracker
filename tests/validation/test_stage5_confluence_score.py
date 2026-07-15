"""Stage 5 — confluence score: strong multi-factor long clears the yaml
threshold; a counter-trend low-volume setup dies here; exit signals and
short history are the edge cases."""
from backend.core.config import load_yaml_config
from backend.core.events import Signal
from backend.validation.stage5_confluence_score import ConfluenceScoreStage

from tests.conftest import make_daily_frame
from tests.validation.conftest import make_ctx, make_hourly_frame

CFG = load_yaml_config("validation").get("confluence_score")


def _ctx(now, daily, hourly, bench):
    return make_ctx(now, history={
        ("NVDA", "1d"): daily, ("NVDA", "1h"): hourly, ("SPY", "1d"): bench,
    })


def test_strong_long_setup_scores_above_threshold(signal, now):
    ctx = _ctx(now,
               daily=make_daily_frame(trend=0.0025),
               hourly=make_hourly_frame(trend=0.002, last_volume_mult=3.0),
               bench=make_daily_frame(trend=0.0025, seed=3))
    result = ConfluenceScoreStage(CFG).validate(signal, ctx)
    assert result.passed
    assert result.measured["score"] >= result.measured["threshold"]   # threshold logged
    assert set(result.measured["components"]) == set(CFG["weights"])


def test_weak_setup_scores_below_threshold(signal, now):
    ctx = _ctx(now,
               daily=make_daily_frame(trend=-0.0025),                 # downtrend vs LONG
               hourly=make_hourly_frame(trend=-0.002, last_volume_mult=0.3),
               bench=make_daily_frame(trend=-0.0025, seed=3))
    result = ConfluenceScoreStage(CFG).validate(signal, ctx)
    assert not result.passed
    assert result.measured["score"] < result.measured["threshold"]


def test_exit_signal_skips(now):
    exit_signal = Signal("trend_pullback", "NVDA", "FLAT", 1.0, now, {})
    result = ConfluenceScoreStage(CFG).validate(exit_signal, make_ctx(now))
    assert result.passed and result.measured.get("skipped") is True


def test_insufficient_history_scores_low_edge(signal, now):
    ctx = _ctx(now, daily=make_daily_frame(days=50),
               hourly=make_hourly_frame(hours=10),
               bench=make_daily_frame(days=1))
    result = ConfluenceScoreStage(CFG).validate(signal, ctx)
    assert not result.passed
    assert result.measured["score"] < result.measured["threshold"]
