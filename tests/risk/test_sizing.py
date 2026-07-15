from backend.risk.sizing import fixed_fractional_size, volatility_stop


def test_fixed_fractional_math():
    # equity 100k, risk 1% => $1000; entry 100, stop 95 => $5/share => 200 shares
    sized = fixed_fractional_size(
        equity=100_000, risk_per_trade_pct=1.0, entry=100.0, stop_price=95.0,
        max_position_pct=50.0, is_long=True, take_profit_r_multiple=2.0,
    )
    assert sized.shares == 200
    assert sized.take_profit == 110.0     # 2R above entry
    assert sized.risk_amount == 1000.0


def test_max_position_cap_applies():
    sized = fixed_fractional_size(
        equity=100_000, risk_per_trade_pct=1.0, entry=100.0, stop_price=99.5,
        max_position_pct=10.0, is_long=True,
    )
    # uncapped would be 2000 shares ($200k) — capped at 10% of equity = 100 shares
    assert sized.shares == 100


def test_invalid_stop_gives_zero_shares():
    sized = fixed_fractional_size(
        equity=100_000, risk_per_trade_pct=1.0, entry=100.0, stop_price=101.0,
        max_position_pct=10.0, is_long=True,
    )
    assert sized.shares == 0


def test_fractional_sizing_for_crypto():
    # BTC at 120k, stop 4k below: risk $1000 => 0.25 BTC — whole-share flooring
    # would return 0 and the system would never trade crypto
    sized = fixed_fractional_size(
        equity=100_000, risk_per_trade_pct=1.0, entry=120_000.0,
        stop_price=116_000.0, max_position_pct=50.0, is_long=True,
        take_profit_r_multiple=2.0, fractional=True,
    )
    assert abs(sized.shares - 0.25) < 1e-9
    assert sized.take_profit == 128_000.0
    assert abs(sized.risk_amount - 1000.0) < 1e-6


def test_whole_share_flooring_zeroes_out_at_high_prices():
    # same trade without fractional support: floor(0.25) == 0 shares
    sized = fixed_fractional_size(
        equity=100_000, risk_per_trade_pct=1.0, entry=120_000.0,
        stop_price=116_000.0, max_position_pct=50.0, is_long=True,
    )
    assert sized.shares == 0


def test_fractional_short_sizing():
    sized = fixed_fractional_size(
        equity=100_000, risk_per_trade_pct=1.0, entry=120_000.0,
        stop_price=124_000.0, max_position_pct=50.0, is_long=False,
        take_profit_r_multiple=2.0, fractional=True,
    )
    assert abs(sized.shares - 0.25) < 1e-9
    assert sized.take_profit == 112_000.0    # 2R below entry


def test_fractional_respects_max_position_cap():
    # tight stop would size 2.5 BTC ($300k) — capped at 10% equity / entry
    sized = fixed_fractional_size(
        equity=100_000, risk_per_trade_pct=1.0, entry=120_000.0,
        stop_price=119_600.0, max_position_pct=10.0, is_long=True,
        fractional=True,
    )
    assert sized.shares <= 10_000.0 / 120_000.0 + 1e-12


def test_volatility_stop_shrinks_size_when_atr_expands():
    stop_narrow = volatility_stop(100.0, atr_value=1.0, atr_multiplier=2.0, is_long=True)
    stop_wide = volatility_stop(100.0, atr_value=3.0, atr_multiplier=2.0, is_long=True)
    narrow = fixed_fractional_size(100_000, 1.0, 100.0, stop_narrow, 100.0)
    wide = fixed_fractional_size(100_000, 1.0, 100.0, stop_wide, 100.0)
    assert wide.shares < narrow.shares
