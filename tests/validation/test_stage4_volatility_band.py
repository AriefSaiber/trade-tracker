"""Stage 4 — volatility band: mid-band ATR passes; a spike fails high and a
collapse fails low; short history and exit signals are the edge cases."""
from backend.core.config import load_yaml_config
from backend.core.events import Signal
from backend.validation.stage4_volatility_band import VolatilityBandStage

from tests.validation.conftest import make_ctx, make_spread_frame

CFG = load_yaml_config("validation").get("volatility_band")


def _ctx(now, daily):
    return make_ctx(now, history={("NVDA", "1d"): daily})


def test_mid_band_volatility_passes(signal, now):
    # long calm stretch, a spike, then decay back to the middle of the range
    spreads = [1.0] * 200 + [2.0] * 50 + [1.0] * 50
    result = VolatilityBandStage(CFG).validate(signal, _ctx(now, make_spread_frame(spreads)))
    assert result.passed
    lo, hi = result.measured["band"]                       # threshold logged
    assert lo <= result.measured["atr_percentile"] <= hi


def test_volatility_spike_fails_high(signal, now):
    spreads = [1.0] * 270 + [4.0] * 30
    result = VolatilityBandStage(CFG).validate(signal, _ctx(now, make_spread_frame(spreads)))
    assert not result.passed
    assert result.reason == "volatility too high"


def test_volatility_collapse_fails_low(signal, now):
    spreads = [4.0] * 200 + [0.2] * 100
    result = VolatilityBandStage(CFG).validate(signal, _ctx(now, make_spread_frame(spreads)))
    assert not result.passed
    assert result.reason == "volatility too low"


def test_short_history_edge_fails(signal, now):
    result = VolatilityBandStage(CFG).validate(signal, _ctx(now, make_spread_frame([1.0] * 10)))
    assert not result.passed
    assert "insufficient history" in result.reason


def test_exit_signal_skips(now):
    exit_signal = Signal("trend_pullback", "NVDA", "FLAT", 1.0, now, {})
    result = VolatilityBandStage(CFG).validate(exit_signal, make_ctx(now))
    assert result.passed and result.measured.get("skipped") is True
