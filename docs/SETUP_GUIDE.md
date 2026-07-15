# AlgoTrader AI — Setup Guide (from scratch)

Two ways to run the platform. Start with **Path A** — it needs nothing but
Python and proves the whole system works before you configure anything.

---

## Path A — Zero-config local run (simulated data, 5 minutes)

### 1. Install

```bash
git clone <your-repo> trade-tracker
cd trade-tracker
pip install -r backend/requirements.txt
```

Python 3.12+ required. No database, no Redis, no API keys.

### 2. Start the platform

```bash
python scripts/run_local.py
```

This starts the FastAPI backend on `http://127.0.0.1:8000` with the
paper-trading worker embedded in the same process:

- market data: deterministic **simulated** feed (`configs/market.yaml → provider: simulated`)
- persistence: SQLite at `data/algotrader.db` (created automatically)
- state: in-memory, shared between worker and API

Within ~1–2 minutes you will see `paper_fill` log lines; trades appear at
`http://127.0.0.1:8000/api/portfolio`.

### 3. Start the dashboard (optional, separate terminal)

```bash
cd frontend
npm install
npm run dev        # http://localhost:3000
```

### 4. Verify

```bash
curl http://127.0.0.1:8000/api/health                     # worker_alive: true
curl http://127.0.0.1:8000/api/portfolio                  # equity, positions
curl http://127.0.0.1:8000/api/validation/funnel/summary  # the signal funnel
```

### 5. Stop

`Ctrl+C` in the run_local terminal. The journal is flushed to SQLite before
exit — your trade history survives restarts.

---

## Path B — Docker Compose (Postgres/TimescaleDB + Redis + real market data)

### 1. Prerequisites

- Docker Desktop
- An **Alpaca** account (free) with **paper** API keys:
  https://app.alpaca.markets → Paper Trading → API Keys

### 2. Configure

```bash
cp docker/.env.example .env
```

Edit `.env` — the minimum you must set:

| Variable | What |
|---|---|
| `POSTGRES_PASSWORD` | any strong password (required by compose) |
| `ALPACA_PAPER_KEY_ID` / `ALPACA_PAPER_SECRET` | your Alpaca **paper** keys |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | optional — alerts to Telegram |
| `JWT_SECRET` | any long random string |

Leave `LIVE_TRADING=false`. It is also pinned to `false` at the compose level,
so `.env` cannot silently arm live trading.

### 3. Choose the data provider

`configs/market.yaml`:

```yaml
provider: alpaca        # switch from 'simulated' once keys are set
```

With `alpaca`, the worker polls real market bars; outside market hours it will
correctly sit idle (the trading-hours gate blocks entries — that is by design).
Keep `simulated` if you want activity around the clock.

### 4. Start

```bash
docker compose -f docker/docker-compose.yml up -d
```

| Service | URL |
|---|---|
| Dashboard | http://127.0.0.1:3000 |
| API | http://127.0.0.1:8000/api/health |
| Postgres | 127.0.0.1:5432 (localhost only) |
| Redis | 127.0.0.1:6379 (localhost only) |

The worker container publishes state to Redis; the backend container serves it
to the dashboard. Nothing is exposed beyond localhost.

### 5. Stop

```bash
docker compose -f docker/docker-compose.yml down      # state survives (volumes)
```

---

## Configuration reference (all thresholds live in `configs/`)

| File | Governs |
|---|---|
| `market.yaml` | data provider, symbol universe, session hours, regime thresholds, simulator tuning |
| `risk.yaml` | daily-loss circuit breaker, max drawdown, position sizing, ATR stops, take-profit R, cooldowns, portfolio heat |
| `validation.yaml` | the 8 validation stages and confluence-score weights/threshold |
| `broker.yaml` | Alpaca URLs, paper-fill slippage/latency, reconciliation policy |
| `watchdog.yaml` | heartbeat timeout, staleness multipliers, kill-switch file path |
| `worker.yaml` | poll cadence, backfill sizes, persistence flush interval |

Strategy parameters live next to each strategy:
`backend/strategies/<name>/config.yaml` (symbols, interval, `allowed_regimes`,
risk per trade, indicator parameters). Set `enabled: false` to bench one.

## Running the safety drills (required before any promotion)

```bash
python scripts/watchdog_drill.py
```

Kills the data feed, trips the kill switch, and misses a heartbeat — verifies
the system fails flat on each. Exit code 0 = all passed.

## Running the tests

```bash
pytest tests/ -q        # 235 tests, all green expected
```
