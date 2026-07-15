"""Stage 0 — data sanity: fresh data passes; stale data and zero-volume bars
fail; missing history is the edge case."""
from backend.core.config import load_yaml_config
from backend.validation.stage0_data_sanity import DataSanityStage

from tests.validation.conftest import make_ctx, make_hourly_frame

CFG = load_yaml_config("validation").get("data_sanity")


def _ctx(now, frame):
    return make_ctx(now, history={("NVDA", "1h"): frame})


def test_fresh_data_passes(signal, now):
    result = DataSanityStage(CFG).validate(signal, _ctx(now, make_hourly_frame()))
    assert result.passed
    assert result.measured["bar_age_seconds"] <= result.measured["max_age_seconds"]


def test_stale_data_fails(signal, now):
    stale = make_hourly_frame(end="2026-07-10 09:00")   # 6h old > 2x the 1h interval
    result = DataSanityStage(CFG).validate(signal, _ctx(now, stale))
    assert not result.passed
    assert result.reason == "stale data"
    assert result.measured["bar_age_seconds"] > result.measured["max_age_seconds"]


def test_zero_volume_bar_fails(signal, now):
    frame = make_hourly_frame()
    frame.iloc[-3, frame.columns.get_loc("volume")] = 0.0
    result = DataSanityStage(CFG).validate(signal, _ctx(now, frame))
    assert not result.passed
    assert "zero-volume" in result.reason


def test_no_data_edge_case_fails(signal, now):
    result = DataSanityStage(CFG).validate(signal, make_ctx(now))
    assert not result.passed
    assert result.reason == "no data for symbol"


def test_min_volume_override_for_crypto(signal, now):
    """Crypto volume is denominated in coins: an hourly bar trading 0.4 BTC
    would fail the equity min_volume=1 — the strategy config may override."""
    frame = make_hourly_frame()
    frame.iloc[-3, frame.columns.get_loc("volume")] = 0.4
    assert not DataSanityStage(CFG).validate(signal, _ctx(now, frame)).passed

    cfg = {"strategy_id": "btc_trend_momentum", "interval": "1h",
           "validation_overrides": {"data_sanity": {"min_volume": 0.000001}}}
    ctx = make_ctx(now, history={("NVDA", "1h"): frame}, strategy_config=cfg)
    assert DataSanityStage(CFG).validate(signal, ctx).passed
