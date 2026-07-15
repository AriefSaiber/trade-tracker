# AlgoTrader AI

Local-first algorithmic stock trading platform (MVP v1.1, Phase 1 scaffold).
Python 3.12 · FastAPI · PostgreSQL/TimescaleDB · Redis · Next.js. Runs entirely in Docker on one machine.

> Not investment advice. Backtests overstate live results. The system stays in **paper mode** until the Promotion Gates (MVP §12) pass. `LIVE_TRADING=false` is the default everywhere.

## Quick start — zero config (simulated data)

```bash
pip install -r backend/requirements.txt
python scripts/run_local.py                    # API + embedded worker :8000
cd frontend && npm install && npm run dev      # dashboard :3000 (optional)
```

Paper trading starts immediately on a deterministic simulated feed — no API
keys, no database setup (SQLite at `data/algotrader.db`). Watch it trade:
`curl http://127.0.0.1:8000/api/portfolio`.

## Quick start — Docker (Postgres/TimescaleDB + Redis + real data)

```bash
cp docker/.env.example .env        # set POSTGRES_PASSWORD (+ Alpaca PAPER keys)
# configs/market.yaml: provider: alpaca   (once keys are set)
docker compose -f docker/docker-compose.yml up -d
```

- Dashboard: http://127.0.0.1:3000 · API: http://127.0.0.1:8000/api/health
- Nothing is exposed beyond localhost.
- Full walkthrough: [docs/SETUP_GUIDE.md](docs/SETUP_GUIDE.md) · operations: [docs/USER_MANUAL.md](docs/USER_MANUAL.md)

## Development

```bash
python -m backend.worker --cycles 100          # worker alone, bounded run
python scripts/watchdog_drill.py               # fail-flat drills (Gate B)
pytest tests/ -q                               # 235 tests
```

## Architecture (one code path)

```
data → regime detector → strategies (plugins, Signal only)
     → Signal Validation Pipeline (stages 0–7, every result logged)
     → Risk Engine (survival: limits, stops mandatory, circuit breakers)
     → Execution Manager (idempotent orders, reconciliation)
     → Paper broker / Alpaca adapter → Portfolio + Trade Journal
supervised by Watchdog (heartbeats, staleness, kill switch)
```

The backtester (`backend/backtest/engine.py`) drives the *same* pipeline, risk engine, and order state machine — no `if backtesting:` branches exist anywhere.

## Layout

| Path | What |
|---|---|
| `backend/core/` | events (Signal, Order…), typed config, event bus |
| `backend/data/` | DataProvider interface, Alpaca provider, indicators, quality checks |
| `backend/regime/` | regime detector (TREND_UP/DOWN, RANGE, HIGH_VOL, TRANSITION) |
| `backend/strategies/` | plugins: trend_pullback, rsi2_mean_reversion (+ config.yaml each) |
| `backend/validation/` | pipeline stages 0–7, confluence scoring, funnel logger |
| `backend/risk/` | risk engine, fixed-fractional sizing, ATR stops |
| `backend/execution/` | order state machine, paper broker, Alpaca adapter, reconciliation |
| `backend/backtest/` | event-driven engine, cost model, metrics, walk-forward, Monte Carlo |
| `backend/watchdog/` | heartbeats, kill switch (`touch data/KILL`), fail-flat halts |
| `configs/` | market / broker / risk / validation / regime YAML — all thresholds live here |
| `frontend/` | Next.js dashboard: overview, funnel, strategies, regime, kill switch |
| `tests/` | pytest suite incl. strategy-isolation and no-look-ahead guards |

## Safety rules baked in

- Every order passes `RiskEngine.evaluate()`; entries without stops are rejected
- Stable `client_order_id` idempotency keys — retries can't duplicate positions
- Strategies cannot import execution/risk/portfolio (test-enforced)
- Fail flat: stale data, missing price, reconciliation mismatch → no new entries + alert
- Kill switch: dashboard button, `touch data/KILL`, or Telegram — all disarm live mode

## Status

Phase 1 is functional end to end (see [docs/STABILIZATION_REPORT.md](docs/STABILIZATION_REPORT.md)):
the worker drives data → regime → strategies → validation → risk → paper broker →
journal/persistence → dashboard, supervised by heartbeats, staleness checks, and
the kill switch. Verified live: complete paper trades with P&L, kill-switch
round trip, audit trail surviving restarts. 235 tests green; watchdog drills 3/3.

## Next steps (Phase 2)

1. Real-data paper trading window (Alpaca) toward Gate A/B evidence
2. Alembic migrations on first schema change; bar persistence to TimescaleDB
3. Earnings-calendar source for the stage-6 event filter
4. Opening-range-breakout strategy (third of the recommended suite)
5. Meta-labeling gate (stage 8) + drift monitor auto-pause
