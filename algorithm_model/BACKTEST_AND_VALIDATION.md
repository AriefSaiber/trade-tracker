# Backtest and Validation Requirements

## Purpose

This file defines the rules the AI agent must follow when implementing the backtest. The goal is to prevent common errors such as look-ahead bias, survivorship bias, unrealistic execution, and overfitting.

## Data requirements

Minimum data:

```text
daily open, high, low, close, volume
symbol
date
adjusted_close
```

Recommended data:

```text
adjusted open, high, low, close
split information
dividend information
delisted stocks
sector classification
bid/ask or spread estimates
```

## Data validation

The data loader must check:

- Missing required columns.
- Duplicate symbol-date rows.
- Non-positive prices.
- Non-positive volume.
- Dates out of order.
- Large suspicious price jumps.
- Missing benchmark data.

Invalid rows should be reported. Do not silently discard large portions of data.

## Adjusted vs raw prices

Indicators and returns should be calculated on adjusted prices when possible.

Execution simulation should use price fields that are internally consistent with the indicator prices. If only adjusted close is available but raw OHLC is unadjusted, the agent must either:

1. Adjust OHLC using the adjustment ratio, or
2. Report that accurate execution simulation is not possible with the provided data.

## Look-ahead prevention

For signal day `t`:

- All indicators must use data available at or before close of day `t`.
- Entry orders may be placed for day `t + 1`.
- The strategy must not use day `t + 1` high, low, close, or volume to decide whether to create the signal.
- Ranking by momentum must use only values known on day `t`.

## Entry execution

Default buy-stop logic:

```text
signal generated after close on day t
entry_trigger = high[t] + tick_size
on day t + 1:
    if high[t + 1] >= entry_trigger:
        fill_price = max(open[t + 1], entry_trigger)
    else:
        order canceled
```

Then add slippage and spread cost.

## Exit execution

For each open long position on day `d` after entry:

1. Check if stop is touched.
2. Check if target is touched.
3. If both stop and target are touched on the same bar, use conservative handling: stop first.
4. Apply time stop if holding period exceeds configured maximum.
5. Apply optional trend exit if enabled.

Default stop fill:

```text
if open[d] <= stop_price:
    exit_price = open[d]
elif low[d] <= stop_price:
    exit_price = stop_price
```

Default target fill:

```text
if open[d] >= target_price:
    exit_price = open[d]
elif high[d] >= target_price:
    exit_price = target_price
```

Costs must be subtracted from all exits.

## Slippage and spread

Default cost model:

```text
entry_cost = commission + slippage + spread_cost
exit_cost = commission + slippage + spread_cost
```

Where:

```text
slippage = price * shares * slippage_bps / 10000
spread_cost = price * shares * spread_bps / 10000
```

Configurable defaults:

```yaml
commission_per_share: 0.005
minimum_commission: 1.00
slippage_bps: 5
spread_bps: 5
```

## Survivorship bias

If the dataset includes only current index constituents, the agent must report survivorship-bias risk.

If delisted stocks are available, include them.

When stocks disappear from the dataset, do not assume they exited profitably. The agent should apply a configurable delisting handling rule, such as:

```text
if open position and symbol disappears:
    exit at last available close with extra delisting penalty
```

Default delisting penalty:

```text
0 percent if no delisting information is available, but flag limitation
```

## Walk-forward validation

Implement rolling walk-forward testing.

Default split:

```text
train_period_years: 5
validation_period_years: 1
test_period_years: 1
step_years: 1
```

Process:

```text
for each walk-forward window:
    train on train period
    select candidate parameters using validation period
    freeze selected parameters
    evaluate on test period
combine all test periods into final out-of-sample report
```

The final report must clearly separate:

- In-sample results.
- Validation results.
- Out-of-sample test results.

## Parameter optimization rules

The optimizer may use grid search first.

Do not test too many parameter combinations without reporting multiple-testing risk.

For every parameter set, record:

```text
parameter_set_id
parameters
metrics
accepted_or_rejected
rejection_reason
```

## Rejection rules

Reject a parameter set if any condition is true:

```text
total_trades < min_trades
expectancy_R <= 0
profit_factor < min_profit_factor
wilson_lower_win_rate <= break_even_win_rate
max_drawdown < -max_drawdown_limit
average_trade_net_return <= 2 * estimated_round_trip_cost
out_of_sample_expectancy_R <= 0
```

Defaults:

```yaml
min_trades: 200
min_profit_factor: 1.25
max_drawdown_limit: 0.20
```

## Overfitting controls

The agent must include at least these controls:

1. Walk-forward validation.
2. Separate final test period.
3. Parameter stability report.
4. Rejected parameter report.
5. Minimum trade count.
6. Wilson lower-bound win rate.
7. Bootstrap lower expectancy.

## Parameter stability report

For each accepted parameter set, show nearby parameter results.

A robust strategy should not collapse when one parameter is changed slightly.

Example:

```text
target_R = 1.25 works
target_R = 1.20 and 1.30 should not completely fail
```

If small parameter changes destroy performance, flag the strategy as likely overfit.

## Reporting requirements

The generated Markdown report must include:

```text
1. Executive summary
2. Strategy description
3. Data description
4. Backtest assumptions
5. Cost assumptions
6. Parameter grid
7. Optimization objective
8. Selected parameters
9. In-sample metrics
10. Validation metrics
11. Out-of-sample metrics
12. Trade distribution
13. Drawdown analysis
14. Monthly or yearly return table
15. Rejected parameter summary
16. Known limitations
17. Research conclusion
```

## Live trading restriction

The default project must not place live trades.

If a future version adds live trading, it must require a separate approval step, paper trading validation, and broker-specific risk limits.
