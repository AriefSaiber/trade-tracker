#!/bin/bash
# run_overnight.sh
#
# Unattended, sequential build of the platform, one /goal phase per `claude -p`.
#
# WHAT CHANGED vs the original draft:
#  * `/model` and `/effort` are Claude Code *slash commands* and only work
#    inside an interactive session. On their own lines in a shell script they
#    are parsed as "run the program /model", which fails immediately (and with
#    `set -e`, aborted the whole script on the first line). Model and effort are
#    now passed as real CLI flags: --model / --effort.
#  * Model selection is now surgical. The five phases that are both
#    safety-critical AND structurally complex (validation pipeline, risk engine,
#    execution, backtester, end-to-end integration) run on `fable` at xhigh --
#    Fable 5 is Anthropic's most capable model and the one MVP_Plan.md §4.1
#    names as the build-time coding assistant, best-suited to long-horizon
#    agentic work. Simpler, well-bounded phases stay on `opus`; the low-stakes
#    notifications/portfolio phase stays on `sonnet`.
#  * --dangerously-skip-permissions is set so the run is truly unattended
#    (writing files + running pytest would otherwise block on permission
#    prompts overnight). Remove it if you want to babysit approvals instead.
#
# CAVEATS:
#  * Fable 5 requires 30-day data retention at the org level; a zero-data-
#    retention org returns HTTP 400 on every Fable request. Verify before running.
#  * Fable turns can run many minutes each -- expected for an overnight build.

set -e  # stop on any failure

# The Claude Code installer puts `claude` in ~/.local/bin, which is on the
# Windows/PowerShell PATH but not always on Git Bash's PATH. Make sure it's
# resolvable so `bash run_overnight.sh` works from any shell.
if ! command -v claude >/dev/null 2>&1; then
  export PATH="/c/Users/Arief/.local/bin:$PATH"
fi
CLAUDE_BIN="${CLAUDE_BIN:-/c/Users/Arief/.local/bin/claude.exe}"

if [ ! -x "$CLAUDE_BIN" ]; then
  echo "Claude CLI not found: $CLAUDE_BIN"
  exit 127
fi

run_phase() {
  local model="$1"
  local effort="$2"
  local prompt="$3"

  while true; do
    echo "Starting phase with model $model ($effort effort)..."
    
    # Capture stderr and stdout to a temporary file so we can parse it if it fails
    local log_file=$(mktemp)
    
    if "$CLAUDE_BIN" \
      -p "$prompt" \
      --model "$model" \
      --effort "$effort" \
      --dangerously-skip-permissions > "$log_file" 2>&1
    then
      echo "Phase completed successfully."
      rm -f "$log_file"
      break
    else
      echo "Claude failed. Parsing logs for rate limit reset times..."
      cat "$log_file" # Print the error to the console so you can see it overnight
      
      local sleep_seconds=1800 # Default fallback: 30 minutes
      
      # Scenario 1: Look for "retry-after" or explicit seconds indicator
      if grep -qi "retry-after\|wait.*seconds" "$log_file"; then
        # Extracts the first digits found near the retry instruction
        local secs=$(grep -oI "[0-9]\+" "$log_file" | head -n 1)
        if [ ! -z "$secs" ]; then
          sleep_seconds=$((secs + 10)) # Add a 10-second buffer
          echo "Detected explicit retry-after header/message."
        fi
        
      # Scenario 2: Look for "X hours Y minutes" subscription reset text
      elif grep -qi "resets in:\|hours.*minutes" "$log_file"; then
        # Extract hours and minutes using basic regex
        local hours=$(sed -n 's/.* \([0-9]\+\) hour.*/\1/p' "$log_file" | head -n 1)
        local mins=$(sed -n 's/.* \([0-9]\+\) minute.*/\1/p' "$log_file" | head -n 1)
        
        [ -z "$hours" ] && hours=0
        [ -z "$mins" ] && mins=0
        
        if [ "$hours" -gt 0 ] || [ "$mins" -gt 0 ]; then
          sleep_seconds=$(( (hours * 3600) + (mins * 60) + 30 )) # Add 30-second buffer
          echo "Detected long-window subscription limit."
        fi
      fi

      echo "Rate limit dynamic sleep active. Sleeping for ${sleep_seconds} seconds..."
      sleep "$sleep_seconds"
      rm -f "$log_file"
    fi
  done
}

# run_phase opus xhigh "/goal backend/execution/order_state_machine.py implements the full PENDING->SUBMITTED->PARTIAL->FILLED/CANCELLED/REJECTED/EXPIRED state machine, backend/execution/paper_broker.py implements BrokerAdapter and simulates fills using bar OHLC with configurable slippage and latency from configs/broker.yaml, backend/execution/alpaca_adapter.py implements BrokerAdapter for Alpaca REST+WebSocket with idempotency keys stored in Redis, backend/execution/reconciler.py compares local portfolio state to broker truth and emits alerts on mismatch, LIVE_TRADING defaults to false everywhere including docker-compose.yml and all config schemas, and pytest tests/execution/ passes with all Alpaca API calls mocked"

# run_phase opus xhigh "/goal backend/backtest/engine.py is a fully implemented event-driven backtester that replays Bar events through the identical Strategy->ValidationPipeline->RiskEngine->PaperBroker code path used in live mode with zero strategy-specific if-backtesting branches, backend/backtest/cost_model.py applies commission and ATR-scaled slippage on every fill, backend/backtest/metrics.py computes net profit, expectancy, profit factor, win rate, avg win/loss ratio, Sharpe, Sortino, max drawdown, MAR, trade count, and longest losing streak, backend/backtest/walk_forward.py runs rolling train/trade windows and reports Walk-Forward Efficiency, backend/backtest/monte_carlo.py runs 10000 bootstrap resamples and returns the 95th-percentile drawdown distribution, and pytest tests/backtest/ passes with a deterministic test using a fixed Bar sequence and seeded random state producing identical results across 3 runs"

# run_phase opus high "/goal backend/strategies/trend_pullback/ and backend/strategies/mean_reversion_rsi2/ each contain a strategy plugin implementing StrategyBase, a config.yaml with symbol/interval/allowed_regimes/risk_per_trade/stop_loss/take_profit and no hardcoded values, and a tests/ subdirectory — both strategies produce a Signal only via generate_signal() and import nothing from backend/execution or backend/risk, a lint check confirms no cross-layer imports, and pytest tests/strategies/ passes with backtester integration tests that run each strategy over 6 months of mocked OHLCV data and assert expectancy > 0 and trade count >= 10"

# run_phase opus high "/goal backend/watchdog/monitor.py emits and checks heartbeats for all running components via Redis with a 30-second timeout, backend/watchdog/kill_switch.py responds to a KILL file at data/KILL, a dashboard button event, and a Telegram command — each triggering order cancellation and disarming LIVE_TRADING, backend/watchdog/staleness.py detects quotes older than 2x the strategy interval and blocks new entries, all watchdog events are logged as structured JSON, and pytest tests/watchdog/ passes including a test that simulates a missed heartbeat and verifies new entries are blocked"

# run_phase sonnet high "/goal backend/notifications/telegram.py implements the NotificationDispatcher interface and sends alerts for trade executed, stop hit, circuit breaker tripped, watchdog timeout, drift monitor pause, and daily summary — with secrets scrubbed before sending, backend/portfolio/portfolio.py tracks cash, buying power, open/closed positions, realized/unrealized PnL, daily PnL, and equity curve, backend/portfolio/journal.py persists every Signal, ValidatedSignal, rejection, order event, and fill to Postgres with full stage_results JSON, the Alembic migration for portfolio and journal tables exists, and pytest tests/portfolio/ and tests/notifications/ pass with Telegram API mocked"

run_phase opus xhigh "/goal an end-to-end integration test at tests/integration/test_paper_trading_loop.py exists and passes: it starts the full pipeline (data provider -> regime detector -> trend_pullback strategy -> validation pipeline all 8 stages -> risk engine -> paper broker -> portfolio journal), replays 30 days of mocked OHLCV bars for AAPL at 1h interval, asserts that at least one ValidatedSignal was produced with score >= 70, at least one order was filled in the paper broker, every fill appears in the trade journal, every rejected signal has a logged stage and reason, and no import from backend/execution or backend/risk exists in any file under backend/strategies/"

echo "All phases complete"
