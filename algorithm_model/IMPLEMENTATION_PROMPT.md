# Direct Prompt for an AI Coding Agent

You are a quantitative trading research and Python engineering agent.

Your task is to build a complete research-grade backtesting framework for a stock trading algorithm based on the Markdown specification files in this folder.

## Mission

Generate a Python project that implements the Robust Momentum Pullback Algorithm for equities.

The system must search for the highest statistically reliable win rate, but only among strategies that have positive expectancy, realistic costs, acceptable drawdown, and out-of-sample validation.

Do not optimize raw win rate alone.

## Required reading order

Before writing code, read:

1. `AGENT_TASK.md`
2. `STRATEGY_SPEC.md`
3. `CALCULATIONS_AND_METRICS.md`
4. `BACKTEST_AND_VALIDATION.md`
5. `PSEUDOCODE.md`

## Build requirements

Create a Python project that can:

1. Load daily OHLCV data for multiple stocks and benchmark data such as SPY.
2. Calculate indicators:
   - SMA 10, 50, 100, 200
   - ATR 20
   - Average dollar volume 20
   - 12-1 momentum
   - Momentum percentile rank by date
3. Generate trading signals using the strategy rules.
4. Simulate orders and portfolio holdings without look-ahead bias.
5. Apply realistic costs:
   - Commission
   - Slippage
   - Half-spread estimate
   - Optional borrow cost for shorts, though default strategy is long-only
6. Produce trade-level metrics:
   - Net PnL
   - R multiple
   - Win rate
   - Wilson lower-bound win rate
   - Average win
   - Average loss
   - Payoff ratio
   - Break-even win rate
   - Expectancy
   - Expectancy in R
   - Profit factor
7. Produce portfolio-level metrics:
   - Equity curve
   - Max drawdown
   - CAGR if enough calendar data exists
   - Sharpe ratio, using daily returns
   - Exposure
   - Turnover
8. Run walk-forward validation.
9. Optimize parameters using the objective and constraints in `CALCULATIONS_AND_METRICS.md`.
10. Output a research report in Markdown.

## Required project structure

Generate this structure:

```text
stock_strategy_project/
    README.md
    requirements.txt
    configs/
        default_strategy.yaml
    src/
        __init__.py
        data_loader.py
        indicators.py
        signals.py
        execution.py
        portfolio.py
        metrics.py
        optimizer.py
        walk_forward.py
        report.py
        main.py
    tests/
        test_indicators.py
        test_metrics.py
        test_no_lookahead.py
        test_execution.py
    outputs/
        .gitkeep
```

## Implementation standards

Use Python 3.11 or later.

Use clear, testable functions. Prefer deterministic, vectorized calculations where possible, but use an event-driven loop for order execution and portfolio simulation to avoid look-ahead bias.

Recommended packages:

```text
pandas
numpy
pyyaml
pydantic
pytest
matplotlib
```

Do not require a paid data provider. The system should accept CSV input. If optional data-provider hooks are added, keep them separate from the core engine.

## Input data format

The framework should accept either one combined CSV or one CSV per symbol.

Required columns:

```text
date
symbol
open
high
low
close
volume
adjusted_close
```

Optional columns:

```text
split_factor
dividend
sector
borrow_rate
bid
ask
```

Benchmark data should use the same format, with symbol `SPY` or a configured benchmark symbol.

## Critical rules

- All signals must be calculated using information available at or before the signal date.
- Entries occur on the next trading day after the signal.
- Stops and targets must be simulated using future bars only after entry.
- The engine must handle cases where both stop and target occur on the same daily bar. Use a conservative assumption by default: for long trades, assume the stop is hit first.
- No parameter set may be accepted with fewer than 200 trades unless the config explicitly overrides this threshold.
- No selected strategy may have non-positive expectancy_R.
- No selected strategy may have Wilson lower-bound win rate below break-even win rate.
- All output metrics must be net of estimated costs.

## Main command examples

The final project should support commands like:

```bash
python -m src.main backtest --config configs/default_strategy.yaml --prices data/prices.csv --benchmark data/benchmark.csv
python -m src.main optimize --config configs/default_strategy.yaml --prices data/prices.csv --benchmark data/benchmark.csv
python -m src.main walk-forward --config configs/default_strategy.yaml --prices data/prices.csv --benchmark data/benchmark.csv
```

## Final deliverables

Return:

1. Complete source code.
2. Example configuration file.
3. Unit tests.
4. Explanation of how to run the system.
5. A sample report template.
6. Clear warnings about limitations, overfitting, and historical-performance uncertainty.

## Do not do these things

Do not connect to a live brokerage account.
Do not market the strategy as guaranteed profitable.
Do not optimize only for in-sample raw win rate.
Do not ignore trading costs.
Do not use future data in signal generation.
Do not silently drop losing delisted symbols if delisted data is available.
