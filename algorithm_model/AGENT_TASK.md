# Agent Task Specification

## Role

You are an AI quantitative research engineer. Your job is to produce a reproducible stock trading research framework, not a live trading bot.

## Goal

Implement and validate a long-only equity trading algorithm called the Robust Momentum Pullback Algorithm.

The system should attempt to find a high win-rate configuration, but the chosen configuration must also pass profitability and robustness constraints.

## Main research principle

A high win rate is not enough.

A strategy is only acceptable when:

```text
expectancy > 0
expectancy_R > 0
profit_factor >= minimum_profit_factor
Wilson lower-bound win rate > break-even win rate
max_drawdown <= maximum_allowed_drawdown
out-of-sample performance remains positive
```

## Agent deliverables

The agent must create:

1. A Python backtesting engine.
2. A configurable strategy implementation.
3. A parameter optimizer.
4. A walk-forward validation module.
5. A metrics module.
6. A Markdown report generator.
7. Unit tests.
8. A sample configuration file.

## Non-goals

The agent must not build live trading execution unless explicitly requested later.

The agent must not make guarantees about profitability.

The agent must not hide bad results. Failed tests and rejected parameter sets are part of the research output.

## Data assumptions

The default implementation uses daily bars.

Required data columns:

```text
date, symbol, open, high, low, close, volume, adjusted_close
```

Use adjusted prices for return and indicator calculations when appropriate. Use raw open, high, low, and close for execution if the dataset provides properly adjusted OHLC fields. If only adjusted close is available, document the limitation.

## Backtest assumptions

Default account settings:

```text
initial_equity: 100000
risk_per_trade: 0.005
max_positions: 10
max_sector_exposure: 0.20
max_total_gross_exposure: 1.00
commission_per_share: 0.005
minimum_commission: 1.00
slippage_bps: 5
spread_bps: 5
```

Default strategy is long-only. Short-selling support is optional and should be disabled by default.

## Engineering expectations

The implementation should be modular:

- `data_loader.py` handles data validation and loading.
- `indicators.py` computes technical features.
- `signals.py` creates candidate entries.
- `execution.py` simulates fills, stops, targets, and transaction costs.
- `portfolio.py` manages capital, positions, and equity curve.
- `metrics.py` computes statistics.
- `optimizer.py` searches parameter sets.
- `walk_forward.py` performs train/validation/test splits.
- `report.py` writes a Markdown report.
- `main.py` provides a CLI.

## Testing expectations

Add unit tests for:

- SMA calculation.
- ATR calculation.
- Momentum calculation.
- Wilson lower-bound calculation.
- Break-even win-rate calculation.
- Expectancy calculation.
- Same-bar stop/target handling.
- No-look-ahead behavior.
- Position sizing.
- Cost calculation.

## Required final report

The report must include:

```text
Strategy name
Data period
Universe description
Cost assumptions
Parameter grid
Selected parameters
Number of trades
Win rate
Wilson lower-bound win rate
Break-even win rate
Average win
Average loss
Payoff ratio
Expectancy
Expectancy_R
Profit factor
Max drawdown
Sharpe ratio
Out-of-sample metrics
Rejected parameter summary
Risk warnings
```
