"""Stage 6 — event & time filters: midday passes; session-open blackout,
earnings proximity, and macro dates fail; session-close blackout and exit
signals are the edge cases."""
from datetime import datetime, timezone

from backend.core.config import load_yaml_config
from backend.core.events import Signal
from backend.validation.stage6_event_filter import EventFilterStage

from tests.validation.conftest import make_ctx

# yaml thresholds, plus a macro date (yaml ships with an empty calendar)
CFG = dict(load_yaml_config("validation").get("event_filter"),
           macro_blackout_dates=["2026-07-29"])
SESSION = load_yaml_config("market").get("session")


def test_midday_passes(signal, now):
    # `now` fixture is 15:00 UTC = 11:00 ET — mid-session
    result = EventFilterStage(CFG, SESSION).validate(signal, make_ctx(now))
    assert result.passed


def test_session_open_blackout_fails(signal):
    now = datetime(2026, 7, 10, 13, 35, tzinfo=timezone.utc)   # 09:35 ET (EDT)
    signal.bar_time = now
    result = EventFilterStage(CFG, SESSION).validate(signal, make_ctx(now))
    assert not result.passed
    assert "open blackout" in result.reason


def test_earnings_blackout_fails(signal, now):
    ctx = make_ctx(now, earnings={"NVDA": ["2026-07-11"]})     # 1 day out
    result = EventFilterStage(CFG, SESSION).validate(signal, ctx)
    assert not result.passed
    assert "earnings" in result.reason
    assert result.measured["earnings_date"] == "2026-07-11"


def test_macro_blackout_fails(signal):
    now = datetime(2026, 7, 29, 15, 0, tzinfo=timezone.utc)
    signal.bar_time = now
    result = EventFilterStage(CFG, SESSION).validate(signal, make_ctx(now))
    assert not result.passed
    assert "macro" in result.reason


def test_session_close_blackout_edge(signal):
    # 15:55 ET = 19:55 UTC (July, EDT); close blackout covers the last 10 min
    now = datetime(2026, 7, 10, 19, 55, tzinfo=timezone.utc)
    signal.bar_time = now
    result = EventFilterStage(CFG, SESSION).validate(signal, make_ctx(now))
    assert not result.passed
    assert "close blackout" in result.reason


def test_exit_signal_skips(now):
    exit_signal = Signal("trend_pullback", "NVDA", "FLAT", 1.0, now, {})
    result = EventFilterStage(CFG, SESSION).validate(exit_signal, make_ctx(now))
    assert result.passed and result.measured.get("skipped") is True
