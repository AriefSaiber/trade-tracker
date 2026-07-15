# Calculations and Metrics

This file defines the required calculations for the strategy and optimizer.

## Net PnL per trade

For a long trade:

```text
net_pnl = shares * (exit_price - entry_price) - total_costs
```

Where:

```text
total_costs = entry_commission + exit_commission + entry_slippage + exit_slippage + entry_spread_cost + exit_spread_cost + fees
```

Default cost model:

```text
commission = max(minimum_commission, commission_per_share * shares)
slippage = price * shares * slippage_bps / 10000
spread_cost = price * shares * spread_bps / 10000
```

## Initial risk

```text
risk_per_share = entry_price - initial_stop
risk_dollars = shares * risk_per_share
```

Reject trade if:

```text
risk_dollars <= 0
```

## R multiple

```text
R = net_pnl / risk_dollars
```

## Win rate

```text
win_rate = number_of_winning_trades / total_trades
```

A winning trade is:

```text
net_pnl > 0
```

A losing trade is:

```text
net_pnl < 0
```

Break-even trades can be reported separately.

## Wilson lower-bound win rate

Use the Wilson score lower bound to estimate a conservative win rate.

```text
p_hat = wins / n
z = 1.96

wilson_lower = (
    p_hat + z^2 / (2n)
    - z * sqrt((p_hat * (1 - p_hat) / n) + z^2 / (4n^2))
) / (1 + z^2 / n)
```

If `n == 0`, return 0.

The optimizer should prefer this over raw win rate.

## Average win

```text
avg_win = mean(net_pnl for trades where net_pnl > 0)
```

## Average loss

```text
avg_loss = abs(mean(net_pnl for trades where net_pnl < 0))
```

## Average win in R

```text
avg_win_R = mean(R for trades where R > 0)
```

## Average loss in R

```text
avg_loss_R = abs(mean(R for trades where R < 0))
```

## Payoff ratio

```text
payoff_ratio = avg_win / avg_loss
```

Or in R:

```text
payoff_ratio_R = avg_win_R / avg_loss_R
```

## Break-even win rate

If using net average win and net average loss:

```text
break_even_win_rate = avg_loss / (avg_win + avg_loss)
```

If using R values:

```text
break_even_win_rate_R = avg_loss_R / (avg_win_R + avg_loss_R)
```

A strategy with a win rate below break-even is not acceptable.

## Expectancy

```text
expectancy = (win_rate * avg_win) - ((1 - win_rate) * avg_loss)
```

Alternatively, use the direct trade average:

```text
expectancy = mean(net_pnl)
```

The direct mean of `net_pnl` is preferred because it naturally handles break-even trades.

## Expectancy in R

```text
expectancy_R = mean(R)
```

This is one of the most important metrics.

The system is unacceptable if:

```text
expectancy_R <= 0
```

## Gross profit

```text
gross_profit = sum(net_pnl for trades where net_pnl > 0)
```

## Gross loss

```text
gross_loss = abs(sum(net_pnl for trades where net_pnl < 0))
```

## Profit factor

```text
profit_factor = gross_profit / gross_loss
```

If gross loss is 0 and gross profit is positive, report profit factor as infinity but flag the sample as suspicious if trade count is small.

Default acceptance threshold:

```text
profit_factor >= 1.25
```

## Equity curve

Portfolio equity at each date:

```text
equity = cash + market_value_of_open_positions
```

Daily portfolio return:

```text
daily_return[t] = equity[t] / equity[t - 1] - 1
```

## Drawdown

```text
running_peak[t] = max(equity[0:t])
drawdown[t] = equity[t] / running_peak[t] - 1
max_drawdown = min(drawdown)
```

Report max drawdown as a negative percentage or absolute positive drawdown, but be consistent.

## CAGR

If the test period is long enough:

```text
years = number_of_calendar_days / 365.25
CAGR = (ending_equity / starting_equity) ** (1 / years) - 1
```

## Sharpe ratio

Using daily returns:

```text
excess_daily_return = daily_return - risk_free_rate_daily
Sharpe = sqrt(252) * mean(excess_daily_return) / std(excess_daily_return)
```

If no risk-free data is available, set risk-free rate to 0 and disclose it.

## Bootstrap lower expectancy

Use bootstrap resampling on trade R multiples.

Procedure:

```text
for i in 1..bootstrap_iterations:
    sample n trades with replacement
    sample_expectancy_R[i] = mean(sample.R)

bootstrap_lower_expectancy_R = percentile(sample_expectancy_R, 5)
```

Default:

```text
bootstrap_iterations = 5000
```

Accept only if:

```text
bootstrap_lower_expectancy_R > 0
```

This rule can be strict. Allow it to be configurable.

## Optimization objective

The optimizer must reject any parameter set that fails the constraints.

Default constraints:

```text
trades >= 200
expectancy_R > 0
profit_factor >= 1.25
wilson_lower_win_rate > break_even_win_rate
max_drawdown >= -0.20
out_of_sample_expectancy_R > 0
```

Then score accepted parameter sets:

```text
score = (
    wilson_lower_win_rate
    + 0.25 * expectancy_R
    + 0.05 * min(profit_factor, 3.0)
    - 0.50 * abs(max_drawdown)
)
```

The exact weights should be configurable.

## Tie-breaking

If two parameter sets have similar scores, prefer:

1. More trades.
2. Lower drawdown.
3. Higher out-of-sample expectancy_R.
4. Simpler parameters closer to defaults.
5. Lower turnover.

## Required metrics output schema

For each backtest or parameter set, output:

```text
parameter_set_id
start_date
end_date
total_trades
winning_trades
losing_trades
breakeven_trades
win_rate
wilson_lower_win_rate
avg_win
avg_loss
avg_win_R
avg_loss_R
payoff_ratio
break_even_win_rate
expectancy
expectancy_R
bootstrap_lower_expectancy_R
gross_profit
gross_loss
profit_factor
starting_equity
ending_equity
CAGR
sharpe_ratio
max_drawdown
turnover
average_holding_days
accepted
rejection_reason
```
