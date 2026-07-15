"""Stage 1 — regime gate: allowed regime passes, wrong regime fails,
HIGH_VOL blocking / opt-in and exit signals are the edge cases."""
from backend.core.config import load_yaml_config
from backend.core.events import Regime, Signal
from backend.validation.stage1_regime_gate import RegimeGateStage

from tests.validation.conftest import STRATEGY_CFG, make_ctx

CFG = load_yaml_config("validation").get("regime_gate")


def test_allowed_regime_passes(signal, now):
    result = RegimeGateStage(CFG).validate(signal, make_ctx(now, regime=Regime.TREND_UP))
    assert result.passed


def test_wrong_regime_fails(signal, now):
    result = RegimeGateStage(CFG).validate(signal, make_ctx(now, regime=Regime.RANGE))
    assert not result.passed
    assert "RANGE" in result.reason
    assert result.measured["current_regime"] == "RANGE"      # measured values logged
    assert result.measured["allowed_regimes"] == ["TREND_UP"]


def test_high_vol_blocks_even_if_allowed(signal, now):
    cfg = dict(STRATEGY_CFG, allowed_regimes=["TREND_UP", "HIGH_VOL"])
    ctx = make_ctx(now, regime=Regime.HIGH_VOL, strategy_config=cfg)
    assert not RegimeGateStage(CFG).validate(signal, ctx).passed


def test_high_vol_opt_in_allows(signal, now):
    cfg = dict(STRATEGY_CFG, allowed_regimes=["HIGH_VOL"], opt_in_high_vol=True)
    ctx = make_ctx(now, regime=Regime.HIGH_VOL, strategy_config=cfg)
    assert RegimeGateStage(CFG).validate(signal, ctx).passed


def test_exit_signal_always_passes(now):
    exit_signal = Signal("trend_pullback", "NVDA", "FLAT", 1.0, now, {})
    result = RegimeGateStage(CFG).validate(exit_signal, make_ctx(now, regime=Regime.HIGH_VOL))
    assert result.passed
