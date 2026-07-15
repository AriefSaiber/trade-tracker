"""Stage 3 — volume confirmation: RVOL spike with OBV agreement passes; weak
volume and OBV disagreement fail; strategy skip override and short history
are the edge cases."""
from backend.core.config import load_yaml_config
from backend.validation.stage3_volume_confirmation import VolumeConfirmationStage

from tests.validation.conftest import STRATEGY_CFG, make_ctx, make_hourly_frame

CFG = load_yaml_config("validation").get("volume_confirmation")


def _ctx(now, frame, strategy_config=None):
    return make_ctx(now, history={("NVDA", "1h"): frame}, strategy_config=strategy_config)


def test_volume_spike_with_obv_agreement_passes(signal, now):
    frame = make_hourly_frame(trend=0.002, last_volume_mult=3.0)
    result = VolumeConfirmationStage(CFG).validate(signal, _ctx(now, frame))
    assert result.passed
    assert result.measured["rvol"] >= result.measured["rvol_min"]   # threshold logged


def test_weak_volume_fails(signal, now):
    frame = make_hourly_frame(trend=0.002, last_volume_mult=0.5)
    result = VolumeConfirmationStage(CFG).validate(signal, _ctx(now, frame))
    assert not result.passed
    assert "relative volume below minimum" in result.reason


def test_obv_disagreement_fails(signal, now):
    # falling closes but spiking volume: RVOL clears, OBV contradicts a LONG
    frame = make_hourly_frame(trend=-0.003, last_volume_mult=3.0)
    result = VolumeConfirmationStage(CFG).validate(signal, _ctx(now, frame))
    assert not result.passed
    assert "OBV" in result.reason


def test_strategy_skip_override_edge(signal, now):
    cfg = dict(STRATEGY_CFG, validation_overrides={"volume_confirmation": {"skip": True}})
    frame = make_hourly_frame(trend=0.002, last_volume_mult=0.1)    # would fail
    result = VolumeConfirmationStage(CFG).validate(signal, _ctx(now, frame, cfg))
    assert result.passed
    assert result.measured.get("skipped") is True


def test_short_history_edge_fails(signal, now):
    result = VolumeConfirmationStage(CFG).validate(
        signal, _ctx(now, make_hourly_frame(hours=10)))
    assert not result.passed
    assert "insufficient history" in result.reason
