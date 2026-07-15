# Pseudocode and Implementation Skeleton

## High-level pipeline

```text
load config
load prices
load benchmark
validate data
adjust prices if needed
calculate indicators
create daily tradable universe
create signals
simulate orders and portfolio
calculate trade metrics
calculate portfolio metrics
run optimization if requested
run walk-forward validation if requested
generate report
save outputs
```

## Indicator pseudocode

```python
def sma(series, window):
    return series.rolling(window).mean()


def true_range(high, low, close):
    previous_close = close.shift(1)
    return max_of_columns([
        high - low,
        abs(high - previous_close),
        abs(low - previous_close),
    ])


def atr(high, low, close, window):
    tr = true_range(high, low, close)
    return tr.rolling(window).mean()


def average_dollar_volume(close, volume, window):
    return (close * volume).rolling(window).mean()


def momentum_12_1(close, lookback_days=252, skip_days=21):
    return close.shift(skip_days) / close.shift(lookback_days) - 1
```

## Signal pseudocode

```python
def build_features(prices, benchmark, config):
    prices["sma_10"] = groupby_symbol_sma(prices["close"], 10)
    prices["sma_50"] = groupby_symbol_sma(prices["close"], 50)
    prices["sma_100"] = groupby_symbol_sma(prices["close"], 100)
    prices["sma_200"] = groupby_symbol_sma(prices["close"], 200)
    prices["atr_20"] = groupby_symbol_atr(prices, 20)
    prices["adv_20"] = groupby_symbol_adv(prices, 20)
    prices["momentum_12_1"] = groupby_symbol_momentum(prices, 252, 21)

    prices["momentum_percentile"] = prices.groupby("date")["momentum_12_1"].rank(pct=True)

    benchmark["benchmark_sma_200"] = sma(benchmark["close"], 200)
    benchmark["market_regime"] = benchmark["close"] > benchmark["benchmark_sma_200"]

    prices = merge_market_regime(prices, benchmark)
    return prices


def generate_signals(features, config):
    features["universe_ok"] = (
        (features["close"] > config.min_price) &
        (features["adv_20"] > config.min_average_dollar_volume) &
        (features["atr_20"] / features["close"] < config.max_atr_fraction)
    )

    features["momentum_ok"] = features["momentum_percentile"] >= config.momentum_percentile_min

    features["trend_ok"] = (
        (features["close"] > features["sma_100"]) &
        (features["sma_50"] > features["sma_200"])
    )

    features["pullback_ok"] = features["close"] < features["sma_10"]

    features["signal"] = (
        features["universe_ok"] &
        features["market_regime"] &
        features["momentum_ok"] &
        features["trend_ok"] &
        features["pullback_ok"]
    )

    features["entry_trigger"] = features["high"] + config.tick_size
    return features
```

## Event-driven backtest pseudocode

```python
def run_backtest(features, config):
    account = Account(equity=config.initial_equity, cash=config.initial_equity)
    open_positions = {}
    trade_log = []
    equity_curve = []

    for date in sorted(features.date.unique()):
        today = features[features.date == date]

        # 1. Update exits for existing positions using today's OHLC.
        for symbol, position in list(open_positions.items()):
            bar = today[today.symbol == symbol]
            if bar is empty:
                continue

            exit_event = evaluate_exit(position, bar, config)
            if exit_event.should_exit:
                trade = close_position(position, exit_event, config)
                account.cash += trade.exit_value - trade.exit_cost
                trade_log.append(trade)
                del open_positions[symbol]

        # 2. Mark to market current positions.
        account.update_equity(open_positions, today)

        # 3. Create entries from signals generated on previous day.
        pending_signals = get_signals_from_previous_day(features, date)

        for signal in rank_pending_signals(pending_signals):
            if not portfolio_allows_new_position(account, open_positions, signal, config):
                continue

            today_bar = today[today.symbol == signal.symbol]
            if today_bar is empty:
                continue

            if today_bar.high >= signal.entry_trigger:
                entry_price = max(today_bar.open, signal.entry_trigger)
                stop_price = entry_price - config.atr_stop_multiple * signal.atr
                risk_per_share = entry_price - stop_price
                shares = floor((account.equity * config.risk_per_trade) / risk_per_share)

                if shares <= 0:
                    continue

                entry_cost = calculate_cost(entry_price, shares, config)
                required_cash = shares * entry_price + entry_cost

                if required_cash > account.cash:
                    continue

                target_price = entry_price + config.target_R * risk_per_share

                position = Position(
                    symbol=signal.symbol,
                    entry_date=date,
                    entry_price=entry_price,
                    shares=shares,
                    initial_stop=stop_price,
                    current_stop=stop_price,
                    target_price=target_price,
                    risk_per_share=risk_per_share,
                    risk_dollars=shares * risk_per_share,
                    entry_cost=entry_cost,
                )

                account.cash -= required_cash
                open_positions[signal.symbol] = position

        # 4. Save daily equity.
        account.update_equity(open_positions, today)
        equity_curve.append({"date": date, "equity": account.equity})

    # 5. Close open positions at final available close.
    close_remaining_positions()

    metrics = calculate_all_metrics(trade_log, equity_curve, config)
    return BacktestResult(trade_log=trade_log, equity_curve=equity_curve, metrics=metrics)
```

## Exit evaluation pseudocode

```python
def evaluate_exit(position, bar, config):
    stop = position.current_stop
    target = position.target_price

    # Move stop to break-even if condition has been met before evaluating exits.
    position.highest_high = max(position.highest_high, bar.high)
    if position.highest_high >= position.entry_price + config.break_even_trigger_R * position.risk_per_share:
        position.current_stop = max(position.current_stop, position.entry_price)
        stop = position.current_stop

    stop_touched = bar.low <= stop
    target_touched = bar.high >= target

    # Conservative daily-bar ambiguity handling.
    if stop_touched and target_touched:
        return ExitEvent(True, price=stop_or_gap_price(bar.open, stop, side="long_stop"), reason="same_bar_stop_first")

    if stop_touched:
        if bar.open <= stop:
            return ExitEvent(True, price=bar.open, reason="gap_stop")
        return ExitEvent(True, price=stop, reason="stop")

    if target_touched:
        if bar.open >= target:
            return ExitEvent(True, price=bar.open, reason="gap_target")
        return ExitEvent(True, price=target, reason="target")

    if position.holding_days >= config.max_holding_days:
        return ExitEvent(True, price=bar.close, reason="time_stop")

    if config.use_trend_exit and bar.close < bar.sma_20:
        return ExitEvent(True, price=bar.close, reason="trend_exit")

    return ExitEvent(False)
```

## Metrics pseudocode

```python
def wilson_lower_bound(wins, n, z=1.96):
    if n == 0:
        return 0.0
    p = wins / n
    denominator = 1 + z**2 / n
    center = p + z**2 / (2 * n)
    margin = z * sqrt((p * (1 - p) / n) + (z**2 / (4 * n**2)))
    return (center - margin) / denominator


def calculate_trade_metrics(trades):
    n = len(trades)
    wins = [t for t in trades if t.net_pnl > 0]
    losses = [t for t in trades if t.net_pnl < 0]

    win_rate = len(wins) / n if n else 0
    avg_win = mean([t.net_pnl for t in wins]) if wins else 0
    avg_loss = abs(mean([t.net_pnl for t in losses])) if losses else 0
    expectancy = mean([t.net_pnl for t in trades]) if trades else 0

    R_values = [t.net_pnl / t.risk_dollars for t in trades if t.risk_dollars > 0]
    expectancy_R = mean(R_values) if R_values else 0

    avg_win_R = mean([r for r in R_values if r > 0])
    avg_loss_R = abs(mean([r for r in R_values if r < 0]))

    break_even_win_rate = avg_loss / (avg_win + avg_loss) if avg_win > 0 and avg_loss > 0 else 1.0

    gross_profit = sum([t.net_pnl for t in wins])
    gross_loss = abs(sum([t.net_pnl for t in losses]))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    return {
        "trades": n,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": win_rate,
        "wilson_lower_win_rate": wilson_lower_bound(len(wins), n),
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "avg_win_R": avg_win_R,
        "avg_loss_R": avg_loss_R,
        "break_even_win_rate": break_even_win_rate,
        "expectancy": expectancy,
        "expectancy_R": expectancy_R,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "profit_factor": profit_factor,
    }
```

## Optimizer pseudocode

```python
def parameter_search(data, benchmark, parameter_grid, config):
    results = []

    for params in expand_grid(parameter_grid):
        test_config = config.with_updates(params)
        result = run_backtest(data, benchmark, test_config)
        metrics = result.metrics

        accepted, reason = acceptance_rule(metrics, test_config)
        score = objective_score(metrics, test_config) if accepted else -inf

        results.append({
            "params": params,
            "metrics": metrics,
            "accepted": accepted,
            "rejection_reason": reason,
            "score": score,
        })

    return sort_by_score(results)


def acceptance_rule(metrics, config):
    if metrics.trades < config.min_trades:
        return False, "too_few_trades"
    if metrics.expectancy_R <= 0:
        return False, "non_positive_expectancy_R"
    if metrics.profit_factor < config.min_profit_factor:
        return False, "profit_factor_too_low"
    if metrics.wilson_lower_win_rate <= metrics.break_even_win_rate:
        return False, "wilson_below_breakeven"
    if metrics.max_drawdown < -config.max_drawdown_limit:
        return False, "drawdown_too_high"
    return True, "accepted"


def objective_score(metrics, config):
    return (
        metrics.wilson_lower_win_rate
        + 0.25 * metrics.expectancy_R
        + 0.05 * min(metrics.profit_factor, 3.0)
        - 0.50 * abs(metrics.max_drawdown)
    )
```

## Walk-forward pseudocode

```python
def run_walk_forward(data, benchmark, config, parameter_grid):
    windows = make_walk_forward_windows(
        start_date=data.date.min(),
        end_date=data.date.max(),
        train_years=config.train_period_years,
        validation_years=config.validation_period_years,
        test_years=config.test_period_years,
        step_years=config.step_years,
    )

    all_test_results = []
    window_summaries = []

    for window in windows:
        train_data = filter_dates(data, window.train_start, window.train_end)
        validation_data = filter_dates(data, window.validation_start, window.validation_end)
        test_data = filter_dates(data, window.test_start, window.test_end)

        # Use train and validation to select parameters.
        candidate_results = parameter_search(train_data, benchmark, parameter_grid, config)
        top_candidates = select_top_candidates(candidate_results, config)

        validation_results = []
        for candidate in top_candidates:
            validation_result = run_backtest(validation_data, benchmark, config.with_updates(candidate.params))
            validation_results.append(validation_result)

        selected_params = choose_best_validation_params(validation_results, config)

        # Freeze parameters before test.
        test_result = run_backtest(test_data, benchmark, config.with_updates(selected_params))
        all_test_results.append(test_result)

        window_summaries.append({
            "window": window,
            "selected_params": selected_params,
            "test_metrics": test_result.metrics,
        })

    combined_result = combine_walk_forward_results(all_test_results)
    return WalkForwardResult(combined_result, window_summaries)
```

## Default YAML configuration

```yaml
project:
  name: robust_momentum_pullback
  mode: research

data:
  date_column: date
  symbol_column: symbol
  benchmark_symbol: SPY
  require_adjusted_prices: true

account:
  initial_equity: 100000
  risk_per_trade: 0.005
  max_open_positions: 10
  max_total_gross_exposure: 1.00
  max_single_position_equity_fraction: 0.15
  max_sector_exposure: 0.20

costs:
  commission_per_share: 0.005
  minimum_commission: 1.00
  slippage_bps: 5
  spread_bps: 5

strategy:
  min_price: 5
  min_average_dollar_volume: 20000000
  max_atr_fraction: 0.08
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
  max_holding_days: 60
  tick_size: 0.01
  use_trend_exit: false

validation:
  min_trades: 200
  min_profit_factor: 1.25
  max_drawdown_limit: 0.20
  bootstrap_iterations: 5000
  require_bootstrap_lower_expectancy_positive: true
  train_period_years: 5
  validation_period_years: 1
  test_period_years: 1
  step_years: 1

optimization:
  objective: robust_winrate
  parameter_grid:
    momentum_lookback_days: [126, 189, 252]
    momentum_skip_days: [21]
    momentum_percentile_min: [0.70, 0.80, 0.90]
    market_sma_days: [100, 200]
    pullback_sma_days: [5, 10, 20]
    atr_days: [14, 20]
    atr_stop_multiple: [1.5, 2.0, 2.5]
    target_R: [1.0, 1.25, 1.5, 2.0]
    risk_per_trade: [0.0025, 0.005, 0.01]
    max_holding_days: [20, 40, 60]
```
