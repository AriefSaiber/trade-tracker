"""Stage 7 — portfolio gate: clean book passes; heat cap fails; correlation
cap, sector exposure cap, and exit signals are the edge cases."""
from backend.core.config import load_yaml_config
from backend.core.events import Position, Signal
from backend.validation.stage7_portfolio_correlation import PortfolioCorrelationStage

from tests.conftest import make_daily_frame
from tests.validation.conftest import make_ctx

CFG = load_yaml_config("validation").get("portfolio_correlation")


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
    assert result.measured["portfolio_heat_pct"] >= result.measured["max_heat_pct"]


def test_correlation_cap_fails_edge(signal, now):
    frame = make_daily_frame()              # identical frames -> corr 1.0
    positions = [Position("AMD", 10, 100.0, "trend_pullback", stop_loss=99.0)]
    ctx = make_ctx(now, history={("NVDA", "1d"): frame, ("AMD", "1d"): frame},
                   open_positions=positions)
    result = PortfolioCorrelationStage(CFG).validate(signal, ctx)
    assert not result.passed
    assert result.measured["correlated_with"] == "AMD"
    assert result.measured["correlation"] > CFG["max_correlation"]


def test_sector_cap_fails_edge(signal, now):
    # AMD exposure: 350 * 100 = 35k = 35% of equity, same sector as NVDA;
    # different seeds keep correlation under the cap so the sector cap is
    # what fails, and the tiny stop distance keeps heat well under 5%.
    positions = [Position("AMD", 350, 100.0, "trend_pullback", stop_loss=99.9)]
    ctx = make_ctx(now,
                   history={("NVDA", "1d"): make_daily_frame(seed=5),
                            ("AMD", "1d"): make_daily_frame(seed=9)},
                   open_positions=positions,
                   sectors={"NVDA": "semis", "AMD": "semis"})
    result = PortfolioCorrelationStage(CFG).validate(signal, ctx)
    assert not result.passed
    assert "sector exposure cap" in result.reason
    assert result.measured["sector_exposure_pct"] >= CFG["max_sector_exposure_pct"]


def test_exit_signal_skips(now):
    exit_signal = Signal("trend_pullback", "NVDA", "FLAT", 1.0, now, {})
    result = PortfolioCorrelationStage(CFG).validate(exit_signal, make_ctx(now))
    assert result.passed and result.measured.get("skipped") is True
