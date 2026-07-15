"""Stage 2 — MTF alignment: aligned long passes, counter-trend long fails,
insufficient history is the edge case; short direction mirrors."""
from backend.core.config import load_yaml_config
from backend.validation.stage2_mtf_alignment import MtfAlignmentStage

from tests.conftest import make_daily_frame
from tests.validation.conftest import make_ctx, make_hourly_frame

CFG = load_yaml_config("validation").get("mtf_alignment")


def _ctx(now, daily, hourly):
    return make_ctx(now, history={("NVDA", "1d"): daily, ("NVDA", "1h"): hourly})


def test_long_with_uptrend_passes(signal, now):
    ctx = _ctx(now, make_daily_frame(trend=0.0025), make_hourly_frame(trend=0.001))
    result = MtfAlignmentStage(CFG).validate(signal, ctx)
    assert result.passed
    assert result.measured["price"] > result.measured["daily_ema"]


def test_long_against_downtrend_fails(signal, now):
    ctx = _ctx(now, make_daily_frame(trend=-0.0025), make_hourly_frame(trend=-0.001))
    result = MtfAlignmentStage(CFG).validate(signal, ctx)
    assert not result.passed
    assert "against higher-timeframe trend" in result.reason


def test_insufficient_history_edge_fails(signal, now):
    ctx = _ctx(now, make_daily_frame(days=50), make_hourly_frame(hours=30))
    result = MtfAlignmentStage(CFG).validate(signal, ctx)
    assert not result.passed
    assert "insufficient history" in result.reason


def test_short_with_downtrend_passes(signal, now):
    signal.direction = "SHORT"
    ctx = _ctx(now, make_daily_frame(trend=-0.0025), make_hourly_frame(trend=-0.001))
    assert MtfAlignmentStage(CFG).validate(signal, ctx).passed
