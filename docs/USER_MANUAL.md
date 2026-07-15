# AlgoTrader AI — User Manual

## The paper-trading workflow at a glance

```
you start the platform
   └─> worker backfills history, classifies the market regime
        └─> strategies watch each new bar
             └─> raw signals run the 8-stage validation funnel
                  └─> survivors go to the Risk Engine (sizing + mandatory stop)
                       └─> approved orders fill in the paper broker
                            └─> portfolio, journal, dashboard update
you watch the funnel + portfolio, and stop everything with the kill switch
```

Everything the system decides — including every **rejected** signal and the
stage + reason it died at — is journaled and visible.

---

## Dashboard (http://127.0.0.1:3000)

| Page | What you see |
|---|---|
| **Overview** | equity + daily P&L, open positions with unrealized P&L, current regime, latest signals with validation scores, KILL SWITCH button |
| **Funnel** | per-stage pass/fail counts — where your signals die. A healthy funnel rejects most signals; that is the design working |
| **Strategies** | each strategy's state (active / cooldown / paused), allowed regimes, trades, win rate, expectancy |
| **Regime** | current classification (TREND_UP / TREND_DOWN / RANGE / HIGH_VOL / TRANSITION) with the ADX / EMA / volatility numbers behind it |

The same data is available as JSON if you prefer curl:

| Endpoint | Returns |
|---|---|
| `GET /api/health` | worker liveness, kill-switch / halt flags, sim clock |
| `GET /api/portfolio` | equity, cash, daily P&L, positions, equity curve |
| `GET /api/signals` | recent signals: score if validated, stage + reason if rejected |
| `GET /api/validation/funnel/summary` | pass/fail per stage |
| `GET /api/orders` | order history with stops/targets and fill prices |
| `GET /api/strategies` | per-strategy status and stats |
| `GET /api/alerts` | recent alerts (staleness, kill switch, circuit breaker…) |

## The kill switch

Three equivalent triggers, any of them cancels open orders, halts new entries,
and disarms live mode until you manually re-arm:

1. **Dashboard button** — type `KILL` to confirm (`POST /api/kill-switch`)
2. **File** — `touch data/KILL` (works even if the API is down)
3. Telegram command (Phase 2 — the hook exists, inbound polling doesn't yet)

The trip is persisted as `data/KILL`, so a process restart stays disarmed.
**Re-arm**: delete the file or `POST /api/rearm`. The worker notices within one
cycle and resumes. Re-arming never re-enables live trading.

## Risk settings you'll actually touch (`configs/risk.yaml`)

| Setting | Default | Meaning |
|---|---|---|
| `position.risk_per_trade_pct` | 0.75 | % of equity risked per trade (distance to stop × size) |
| `stops.atr_multiplier` | 2.0 | stop distance = 2 × ATR(14) — sizes shrink when volatility expands |
| `stops.take_profit_r_multiple` | 2.0 | take profit at 2× the risked amount |
| `account.max_daily_loss_pct` | 3.0 | daily circuit breaker — halts ALL trading for the day |
| `account.max_drawdown_pct` | 15.0 | halt + manual re-arm required |
| `cooldown.consecutive_losses` | 4 | a strategy pausing itself after a losing streak |
| `portfolio.max_heat_pct` | 5.0 | cap on total open risk across all positions |

Edit the YAML and restart the worker — no code changes, ever.

## Trade history & P&L

- **Live view**: `/api/portfolio` (open positions, unrealized P&L, equity curve)
- **Persistent record**: every fill, closed trade, equity snapshot, and journal
  event is written to the database —
  SQLite `data/algotrader.db` locally, Postgres in Docker.

```bash
# closed trades with P&L (local SQLite)
python -c "import sqlite3; [print(r) for r in sqlite3.connect('data/algotrader.db').execute(
  'select symbol,strategy_id,entry_price,exit_price,pnl from closed_trades')]"
```

Journal `kind`s worth knowing: `signal_validated`, `signal_rejected` (with
stage + reason), `fill`, `validation_stage` (every stage result),
`daily_summary`, `alert`, `circuit_breaker`, `strategy_cooldown`.

## Logs

Structured JSON (structlog) to stdout — in Docker: `docker compose logs -f worker`.
Key events: `paper_fill`, `signal_rejected` (stage, measured values, reason),
`risk_rejected`, `regime_classified`, `data_stale_entries_blocked`,
`kill_switch_triggered`, `reconciliation_mismatch`, `daily_circuit_breaker`.

## Example workflows

**"Why didn't it trade today?"**
1. `/api/health` — any of `kill_switch_active`, `trading_halted`,
   `entries_blocked_stale` true? That's your answer (by design: fail flat).
2. `/api/validation/funnel/summary` — signals firing but dying at a stage?
   `regime_gate` failures mean the market isn't in the strategy's regime.
   That is the system refusing regime-mismatched trades, not a bug.
3. No raw signals at all? The strategy's entry conditions simply didn't occur.
   Check `/api/regime` and the strategy page.

**"I want it more/less aggressive"**
- More trades: lower `confluence_score.threshold` in `validation.yaml`
  (understand you are admitting lower-quality setups — the 68-scoring signals
  it now takes are the ones the design considers marginal).
- Bigger positions: `position.risk_per_trade_pct` — move in 0.25 steps.
- Different symbols: each strategy's `config.yaml → symbols`.

**"Switch from simulated to real market data"**
1. Put Alpaca paper keys in `.env`
2. `configs/market.yaml → provider: alpaca`
3. Restart. Entries only occur during NYSE hours (09:30–16:00 ET) — outside
   them the session gate rejects, which you'll see in the funnel.

**Adding a strategy**: copy a folder under `backend/strategies/`, implement
`StrategyBase` (emit `Signal` only — importing execution/risk/portfolio fails
the isolation test), give it a `config.yaml`, add tests. The worker discovers
it by the config's `enabled: true`.

## Paper → live (do not skip)

Live trading is **structurally disarmed**: `LIVE_TRADING=false` everywhere, the
worker refuses to start live, and the arming procedure (MVP §15) is
deliberately unimplemented in Phase 1. Before even thinking about it, the
Promotion Gates (MVP §12) require: profit factor ≥ 1.3 out-of-sample, ≥ 100
trades, walk-forward efficiency ≥ 0.5, 60+ days of paper, and
`python scripts/watchdog_drill.py` passing. Paper results on simulated data
prove the *machinery*, not an edge — only real-data paper trading counts
toward the gates.
