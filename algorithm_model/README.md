# Stock Trading Algorithm Agent Specification

This folder contains Markdown instructions for an AI coding agent to generate, backtest, and validate a systematic stock trading algorithm.

The algorithm is designed for research and education. It is not financial advice, not a promise of profit, and not authorization to trade live capital. The agent must implement the system as a research/backtesting framework first.

## Core objective

Build a robust stock trading research system that finds the highest statistically reliable win rate only after proving positive expectancy, realistic costs, acceptable drawdown, and out-of-sample robustness.

The agent must not optimize raw win rate alone.

Correct objective:

```text
Maximize Wilson lower-bound win rate
subject to:
    expectancy_R > 0
    profit_factor >= 1.25
    trades >= 200
    Wilson lower-bound win rate > break-even win rate
    max_drawdown <= configured risk limit
    out-of-sample performance remains positive
```

## Files in this package

Read the files in this order:

1. `IMPLEMENTATION_PROMPT.md` - direct prompt to give an AI coding agent.
2. `AGENT_TASK.md` - role, mission, deliverables, and implementation standards.
3. `STRATEGY_SPEC.md` - exact trading rules and formulas.
4. `CALCULATIONS_AND_METRICS.md` - required win-rate, expectancy, risk, and performance calculations.
5. `BACKTEST_AND_VALIDATION.md` - backtest rules, data requirements, bias controls, and validation process.
6. `PSEUDOCODE.md` - algorithm pseudocode, function skeleton, and configuration template.

## Expected output from the coding agent

The AI coding agent should generate a Python research project with:

```text
stock_strategy_project/
    README.md
    pyproject.toml or requirements.txt
    configs/
        default_strategy.yaml
    data/
        raw/
        processed/
    src/
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
        trade_log.csv
        equity_curve.csv
        summary_metrics.csv
        report.md
```

## Minimum safety and research standards

The agent must:

- Use adjusted OHLCV data where appropriate.
- Avoid look-ahead bias.
- Avoid survivorship bias where data allows.
- Model transaction costs, slippage, and spread.
- Produce a full trade log.
- Validate out of sample.
- Report rejected parameter sets, not only the winning one.
- Include warnings that the result is historical research, not guaranteed future performance.

## Strategy summary

The default strategy is a long-only momentum pullback system:

1. Trade liquid stocks only.
2. Trade only when the broad market is above its 200-day moving average.
3. Select stocks in the top momentum percentile using 12-month momentum excluding the most recent month.
4. Require an uptrend using moving average filters.
5. Enter after a short-term pullback when price confirms strength by breaking above the prior high.
6. Use ATR-based stops and R-multiple targets.
7. Size positions by account risk, not by fixed share count.
8. Select parameters using robust out-of-sample validation.
