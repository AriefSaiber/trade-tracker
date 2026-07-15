"""Funnel logs every pass/fail with measured values AND thresholds to the journal."""
from backend.core.events import StageResult
from backend.portfolio.journal import TradeJournal
from backend.validation.funnel_logger import FunnelLogger


def test_record_includes_measured_and_thresholds(signal):
    funnel = FunnelLogger()
    result = StageResult("volatility_band", False,
                         {"atr_percentile": 95.2, "band": [20.0, 90.0]},
                         "volatility too high")
    funnel.record(signal, result, thresholds={"percentile_min": 20, "percentile_max": 90})
    entry = funnel.records[-1]
    assert entry["stage"] == "volatility_band"
    assert entry["passed"] is False
    assert entry["measured"]["atr_percentile"] == 95.2
    assert entry["thresholds"] == {"percentile_min": 20, "percentile_max": 90}


def test_every_record_lands_in_trade_journal(signal):
    journal = TradeJournal()
    funnel = FunnelLogger(journal)
    ok = StageResult("data_sanity", True, {"bar_age_seconds": 60.0}, "ok")
    bad = StageResult("regime_gate", False, {"current_regime": "RANGE"}, "wrong regime")
    funnel.record(signal, ok, thresholds={"max_bar_age_multiplier": 2.0})
    funnel.record(signal, bad, thresholds={"high_vol_blocks_all": True})
    kinds = [e["kind"] for e in journal.entries]
    assert kinds == ["validation_stage", "validation_stage"]
    payloads = [e["payload"] for e in journal.entries]
    assert payloads[0]["passed"] is True and payloads[1]["passed"] is False
    assert payloads[1]["thresholds"] == {"high_vol_blocks_all": True}


def test_default_journal_created_when_none_given(signal):
    funnel = FunnelLogger()
    funnel.record(signal, StageResult("data_sanity", True, {}, "ok"))
    assert len(funnel.journal.entries) == 1
