# Strategy Specification: Robust Momentum Pullback Algorithm

## Strategy purpose

This is a systematic long-only stock swing-trading strategy. It attempts to trade strong stocks during favorable market regimes after a short-term pullback.

The default goal is a reliable win rate with positive expectancy, not maximum raw return.

## Timeframe

Use daily bars.

Expected holding period:

```text
5 trading days to 3 months
```

## Universe filter

For each stock and date, a stock is tradable only if all conditions are true:

```text
close > 5
average_dollar_volume_20 > 20000000
ATR_20 / close < 0.08
```

Where:

```text
average_dollar_volume_20 = SMA(close * volume, 20)
```

## Market regime filter

Long entries are allowed only when the benchmark is in an uptrend:

```text
benchmark_close > SMA(benchmark_close, 200)
```

Default benchmark:

```text
SPY
```

## Momentum calculation

Use 12-month momentum excluding the most recent month:

```text
momentum_12_1 = close[t - 21] / close[t - 252] - 1
```

This intentionally skips the most recent 21 trading days.

On each date, rank all tradable stocks by `momentum_12_1`.

A stock passes the momentum filter if:

```text
momentum_percentile >= 0.80
```

Meaning the stock is in the top 20 percent of eligible stocks by momentum.

## Trend filter

A stock must pass both trend conditions:

```text
close > SMA(close, 100)
SMA(close, 50) > SMA(close, 200)
```

## Pullback trigger

A stock becomes a candidate when:

```text
close < SMA(close, 10)
```

This means the stock is in a short-term pullback while maintaining longer-term strength.

## Entry rule

When all filters are true on signal day `t`, place a next-day buy-stop order:

```text
entry_trigger = high[t] + tick_size
```

Default:

```text
tick_size = 0.01
```

On trading day `t + 1`, enter long only if:

```text
high[t + 1] >= entry_trigger
```

Default fill price:

```text
entry_price = max(open[t + 1], entry_trigger)
```

Then add estimated trading costs.

If the next day does not trigger the entry, cancel the order unless the configuration allows multi-day pending orders.

## Initial stop-loss

Use an ATR-based stop:

```text
initial_stop = entry_price - atr_stop_multiple * ATR_20[t]
```

Default:

```text
atr_stop_multiple = 2.0
```

Risk per share:

```text
risk_per_share = entry_price - initial_stop
```

Reject trade if `risk_per_share <= 0`.

## Profit target

Set a target using R multiple:

```text
target_price = entry_price + target_R * risk_per_share
```

Default:

```text
target_R = 1.25
```

## Break-even stop adjustment

After price reaches at least `break_even_trigger_R`, move stop to entry price.

Default:

```text
break_even_trigger_R = 1.0
```

Condition:

```text
highest_high_since_entry >= entry_price + break_even_trigger_R * risk_per_share
```

Then:

```text
current_stop = max(current_stop, entry_price)
```

## Exit rules

A trade exits when any of these events occurs:

1. Stop-loss hit.
2. Profit target hit.
3. Time stop hit.
4. Optional trend exit hit.

Default maximum holding period:

```text
max_holding_days = 60
```

Time-stop exit:

```text
exit at next close or next open, depending on config
```

Default:

```text
exit_on_time_stop = next_open
```

Optional trend exit:

```text
close < SMA(close, 20)
```

Default:

```text
use_trend_exit = false
```

## Same-bar stop/target rule

Daily OHLC bars do not reveal the exact intraday order of events.

If both stop and target are touched on the same daily bar, use conservative handling:

For a long trade:

```text
assume stop is hit first
```

This avoids overstating performance.

## Position sizing

Risk a fixed percentage of account equity per trade:

```text
risk_budget = account_equity * risk_per_trade
shares = floor(risk_budget / risk_per_share)
```

Default:

```text
risk_per_trade = 0.005
```

This means each trade risks 0.5 percent of current account equity before slippage and gap risk.

Reject trade if:

```text
shares <= 0
```

## Portfolio constraints

Default constraints:

```text
max_open_positions = 10
max_total_gross_exposure = 1.00
max_single_position_equity_fraction = 0.15
max_sector_exposure = 0.20
```

If sector data is unavailable, skip sector exposure but report that the constraint could not be enforced.

## Drawdown risk controls

Optional portfolio-level controls:

```text
if current_drawdown >= 0.10:
    reduce new trade risk by 50 percent

if current_drawdown >= 0.15:
    reduce new trade risk by 75 percent

if current_drawdown >= 0.20:
    stop opening new trades until equity makes a new 20-day high
```

These controls should be configurable.

## Default parameter grid

The optimizer may test:

```yaml
momentum_lookback_days: [126, 189, 252]
momentum_skip_days: [21]
momentum_percentile_min: [0.70, 0.80, 0.90]
market_sma_days: [100, 200]
trend_sma_fast_days: [50]
trend_sma_mid_days: [100]
trend_sma_slow_days: [200]
pullback_sma_days: [5, 10, 20]
atr_days: [14, 20]
atr_stop_multiple: [1.5, 2.0, 2.5]
target_R: [1.0, 1.25, 1.5, 2.0]
break_even_trigger_R: [1.0]
risk_per_trade: [0.0025, 0.005, 0.01]
max_holding_days: [20, 40, 60]
```

## Default selected starting configuration

Use this for the first non-optimized backtest:

```yaml
momentum_lookback_days: 252
momentum_skip_days: 21
momentum_percentile_min: 0.80
market_sma_days: 200
trend_sma_fast_days: 50
trend_sma_mid_days: 100
trend_sma_slow_days: 200
pullback_sma_days: 10
atr_days: 20
atr_stop_multiple: 2.0
target_R: 1.25
break_even_trigger_R: 1.0
risk_per_trade: 0.005
max_holding_days: 60
```
