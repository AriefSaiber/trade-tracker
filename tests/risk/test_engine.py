from datetime import datetime, timezone

from backend.core.events import Position, Signal, StageResult, ValidatedSignal
from backend.risk.engine import AccountState, RiskEngine, make_client_order_id


def make_account(**overrides) -> AccountState:
    base = dict(
        equity=100_000.0,
        equity_peak=100_000.0,
        daily_pnl=0.0,
        open_positions=[],
        open_positions_by_strategy={},
        consecutive_losses_by_strategy={},
        cooldown_until_by_strategy={},
        now=datetime(2026, 7, 10, 15, 0, tzinfo=timezone.utc),
    )
    base.update(overrides)
    return AccountState(**base)


def make_validated(signal) -> ValidatedSignal:
    return ValidatedSignal(
        signal=signal, score=80.0, stage_results=[
            StageResult("confluence_score", True, {"score": 80.0}, "ok")
        ],
        regime="TREND_UP",
        validated_at=datetime(2026, 7, 10, 15, 0, tzinfo=timezone.utc),
    )


def test_approved_order_has_stop_and_idempotency_key(signal):
    engine = RiskEngine()
    decision = engine.evaluate(make_validated(signal), make_account(),
                               entry_price=100.0, atr_value=2.0)
    assert decision.approved
    order = decision.order
    assert order is not None
    assert order.stop_loss == 96.0                # 100 - 2*ATR(2.0)
    assert order.client_order_id                  # stable idempotency key
    # deterministic: same signal → same key
    assert order.client_order_id == make_client_order_id(
        signal.strategy_id, signal.symbol, signal.bar_time
    )


def test_kill_switch_blocks_everything(signal):
    engine = RiskEngine()
    decision = engine.evaluate(make_validated(signal),
                               make_account(kill_switch_active=True),
                               entry_price=100.0, atr_value=2.0)
    assert not decision.approved
    assert "kill switch" in decision.reason


def test_daily_loss_circuit_breaker(signal):
    engine = RiskEngine()
    decision = engine.evaluate(make_validated(signal),
                               make_account(daily_pnl=-5000.0),   # > 3% of 100k
                               entry_price=100.0, atr_value=2.0)
    assert not decision.approved


def test_max_drawdown_halts(signal):
    engine = RiskEngine()
    decision = engine.evaluate(make_validated(signal),
                               make_account(equity=80_000.0, equity_peak=100_000.0),
                               entry_price=100.0, atr_value=2.0)
    assert not decision.approved
    assert "drawdown" in decision.reason


def test_invalid_atr_fails_flat(signal):
    engine = RiskEngine()
    decision = engine.evaluate(make_validated(signal), make_account(),
                               entry_price=100.0, atr_value=0.0)
    assert not decision.approved
    assert "fail flat" in decision.reason


def test_reconciliation_mismatch_blocks(signal):
    engine = RiskEngine()
    decision = engine.evaluate(make_validated(signal),
                               make_account(reconciliation_ok=False),
                               entry_price=100.0, atr_value=2.0)
    assert not decision.approved


def test_circuit_breaker_halts_all_trading(signal, now):
    engine = RiskEngine()
    # entries are blocked
    decision = engine.evaluate(make_validated(signal),
                               make_account(halted_today=True),
                               entry_price=100.0, atr_value=2.0)
    assert not decision.approved
    assert "circuit breaker" in decision.reason
    # even exits are blocked — the halt stops ALL trading for the day
    exit_signal = Signal(signal.strategy_id, signal.symbol, "FLAT",
                         1.0, now, {})
    pos = Position("NVDA", 100, 100.0, signal.strategy_id, stop_loss=96.0)
    decision = engine.evaluate(make_validated(exit_signal),
                               make_account(halted_today=True,
                                            open_positions=[pos]),
                               entry_price=100.0, atr_value=2.0)
    assert not decision.approved


def test_outside_trading_hours_rejected(signal):
    engine = RiskEngine()
    # Thursday 22:00 ET (Friday 02:00 UTC) — a weekday, but after the close
    decision = engine.evaluate(
        make_validated(signal),
        make_account(now=datetime(2026, 7, 10, 2, 0, tzinfo=timezone.utc)),
        entry_price=100.0, atr_value=2.0,
    )
    assert not decision.approved
    assert "trading hours" in decision.reason


def test_weekend_rejected(signal):
    engine = RiskEngine()
    # Saturday 11:00 ET (2026-07-11 15:00 UTC)
    decision = engine.evaluate(
        make_validated(signal),
        make_account(now=datetime(2026, 7, 11, 15, 0, tzinfo=timezone.utc)),
        entry_price=100.0, atr_value=2.0,
    )
    assert not decision.approved
    assert "trading hours" in decision.reason


def test_take_profit_r_multiple_metadata_override(signal):
    """Strategies may carry a per-trade target_R in signal metadata (e.g.
    gpt_pro's 1.25R) — the engine honors it over the global config."""
    engine = RiskEngine()
    signal.metadata["take_profit_r_multiple"] = 1.25
    decision = engine.evaluate(make_validated(signal), make_account(),
                               entry_price=100.0, atr_value=2.0)
    assert decision.approved
    # stop = 100 - 2*2.0 = 96 → risk/share 4 → TP = 100 + 1.25*4 = 105 (not 2R=108)
    assert decision.order.take_profit == 105.0


def test_take_profit_defaults_to_global_config(signal):
    engine = RiskEngine()
    decision = engine.evaluate(make_validated(signal), make_account(),
                               entry_price=100.0, atr_value=2.0)
    assert decision.approved
    assert decision.order.take_profit == 108.0    # risk.yaml 2R default


def make_crypto_signal(now: datetime, direction: str = "LONG") -> Signal:
    return Signal(
        strategy_id="btc_trend_momentum", symbol="BTC/USD",
        direction=direction, confidence=0.9, bar_time=now, metadata={},
    )


def test_crypto_entry_allowed_on_weekend_24_7(now):
    """Crypto is exempt from the equity session gate (risk.yaml
    trading_hours.crypto_exempt) — Saturday entries must pass."""
    engine = RiskEngine()
    saturday = datetime(2026, 7, 11, 15, 0, tzinfo=timezone.utc)
    decision = engine.evaluate(
        make_validated(make_crypto_signal(saturday)),
        make_account(now=saturday),
        entry_price=120_000.0, atr_value=2_000.0,
    )
    assert decision.approved, decision.reason


def test_crypto_order_is_fractional_with_gtc(now):
    engine = RiskEngine()
    decision = engine.evaluate(
        make_validated(make_crypto_signal(now)),
        make_account(),
        entry_price=120_000.0, atr_value=2_000.0,
    )
    assert decision.approved, decision.reason
    order = decision.order
    # risk 0.75% of 100k = $750 over a $4000 stop => 0.1875 BTC,
    # capped by max_position_pct 10% => 10k/120k ~ 0.0833
    assert 0 < order.qty < 1
    assert order.time_in_force == "gtc"           # Alpaca crypto rejects "day"
    assert order.stop_loss == 116_000.0           # 2x ATR below entry


def test_crypto_short_order_supported(now):
    engine = RiskEngine()
    decision = engine.evaluate(
        make_validated(make_crypto_signal(now, direction="SHORT")),
        make_account(),
        entry_price=120_000.0, atr_value=2_000.0,
    )
    assert decision.approved, decision.reason
    assert decision.order.side.value == "SELL"
    assert decision.order.stop_loss == 124_000.0  # 2x ATR above entry


def test_crypto_dust_order_rejected_below_min_notional(now):
    engine = RiskEngine()
    decision = engine.evaluate(
        make_validated(make_crypto_signal(now)),
        make_account(equity=1_000.0, equity_peak=1_000.0),  # tiny account
        entry_price=120_000.0, atr_value=60_000.0,          # huge stop distance
    )
    # risk $7.50 over a $120k stop => ~6e-5 BTC => ~$7.5 notional < $10 min
    assert not decision.approved
    assert "notional" in decision.reason


def test_stale_symbol_blocks_only_that_symbol(signal, now):
    """Staleness is per-symbol: weekend-stale equities must not freeze
    24/7 crypto (and a stale crypto feed must not block equities)."""
    engine = RiskEngine()
    stale = make_account(stale_symbols=["BTC/USD"])
    rejected = engine.evaluate(make_validated(make_crypto_signal(now)),
                               stale, entry_price=120_000.0, atr_value=2_000.0)
    assert not rejected.approved
    assert "stale" in rejected.reason
    # the equity signal sails through the same account state
    approved = engine.evaluate(make_validated(signal), stale,
                               entry_price=100.0, atr_value=2.0)
    assert approved.approved, approved.reason


def test_heat_cap_rejects_pile_on(signal):
    engine = RiskEngine()
    # existing positions already carrying ~4.9% risk on 100k equity
    positions = [
        Position("AAPL", 500, 100.0, "s1", stop_loss=95.0),   # $2500 risk
        Position("MSFT", 480, 100.0, "s2", stop_loss=95.0),   # $2400 risk
    ]
    decision = engine.evaluate(
        make_validated(signal),
        make_account(open_positions=positions),
        entry_price=100.0, atr_value=2.0,
    )
    assert not decision.approved
    assert "heat" in decision.reason
