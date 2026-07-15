# Stabilization Report — 2026-07-13

Full remediation pass following the QA audit of 2026-07-12. Scope: make the
platform paper-trade end to end with minimal manual intervention.

## 1. Executive summary

**Status: the platform paper-trades end to end, verified live.** A fresh
clone now runs with one command (`python scripts/run_local.py`) and zero
external services: simulated market data → regime detection → strategies →
8-stage validation → risk engine → paper broker → portfolio → SQLite journal →
dashboard API. All of the following were verified against the *running*
platform over HTTP, not just in tests: complete round-trip trades with P&L,
stop-loss and 2R take-profit exits, the validation funnel rejecting
regime/volatility-mismatched signals, the dashboard kill switch halting the
worker cross-process and re-arming, and the audit trail surviving process
shutdown.

Test suite: **235 passed, 0 failed** (was 199; 36 added). Watchdog drills
(Gate B prerequisite): **3/3 pass** via `python scripts/watchdog_drill.py`.
Frontend: `npm run build` clean.

**Readiness level:** ready for unattended *paper* trading on simulated data;
ready for real-data paper trading the moment Alpaca paper keys are added
(config switch, no code). Live trading remains structurally disarmed by
design — that is Phase 2, gated behind MVP §12's promotion criteria, and
nothing in this pass shortcuts it.

## 2. Changes made

### Critical fixes (the QA report's top findings)
1. **Worker was a stub — now the real runtime** (`backend/worker.py`). The
   loop that previously only heartbeated now drives the full decision path
   per bar, with: point-in-time history slicing (identical to the
   backtester), stop-loss/take-profit enforcement through the risk engine,
   FLAT-signal exits, daily P&L rollover + circuit breaker, consecutive-loss
   cooldowns, periodic broker reconciliation, journal persistence, dashboard
   state publishing, Telegram alerts, and graceful shutdown with final flush.
2. **Watchdog duplication resolved.** The untested `watchdog/watchdog.py`
   (which was the one actually wired in) is deleted; the tested trio —
   `HeartbeatMonitor`, `StalenessMonitor`, `KillSwitch` — is now what the
   runtime uses. Their flags feed `AccountState`, so the risk engine
   genuinely rejects entries on heartbeat loss / stale data / kill switch.
3. **Persistence exists.** SQLAlchemy 2 async models (`journal`, `orders`,
   `fills`, `closed_trades`, `equity_snapshots`) mirroring the initdb SQL;
   `PersistenceService` flushes every cycle and on shutdown; Postgres in
   Docker, SQLite locally. Fail-soft: a dead DB raises an alert and buffers —
   it never stops paper trading.
4. **Worker → dashboard bridge exists.** New `StateStore` (in-memory when the
   worker is embedded in the API process, Redis when they're separate
   containers). All API endpoints now serve real worker state.
   `WORKER_EMBEDDED=true` gives the single-process local mode.

### New features
- **Simulated data provider** (`backend/data/simulated_provider.py`) —
  deterministic synthetic OHLCV per symbol; the default provider, so paper
  trading works with zero API keys. Alpaca remains a config switch away.
- **Kill-switch round trip via the KILL file**: dashboard POST → file →
  worker's `KillSwitch.watch` trips within a second → orders cancelled,
  entries blocked; deleting the file (or `POST /api/rearm`) re-arms.
- **`scripts/run_local.py`** — one-command platform.
- **`scripts/watchdog_drill.py` (+ `.sh`)** — the previously-missing Gate B
  drills: data-feed kill, kill-switch trip, missed heartbeat; all fail-flat
  paths verified, exit-code gated.
- **`/api/validation/funnel/summary`** — the endpoint the frontend was
  already calling (it 404'd before; the dashboard silently fell back to demo
  data). Also new: `/api/alerts`.
- **Emergency flatten path**: staleness hard-limit / kill-switch flatten
  closes positions through an explicit risk-engine decision + the
  ExecutionManager — no raw broker calls, the "every order passes the Risk
  Engine" rule holds even in emergencies.

### Bug fixes & hygiene
- Stage-0 data-sanity failures now fire a real alert (MVP §8 requirement),
  not just a funnel row.
- `configs/regime.yaml` (dead duplicate of `market.yaml`'s regime section)
  removed; the config test now asserts the single source of truth.
- Backtester exit handling: FLAT signals now bypass the *quality* gauntlet
  but still pass `RiskEngine.evaluate()` (previously an exit could be
  blocked by the regime gate, trapping a position).
- Backtester stop-check ordering fixed (stops resolve before the strategy
  sees its position state for the bar).
- `.env.example` documents `WORKER_EMBEDDED` and `DATABASE_URL`;
  compose pins `WORKER_EMBEDDED=false` for both backend and worker.
- `aiosqlite` added to requirements.

### Tests added (36)
`tests/portfolio/` (accounting + persistence round-trip + DB-outage
degradation), `tests/notifications/` (Telegram mocked, secrets never logged,
failures swallowed), `tests/data/test_simulated_provider.py` (determinism,
OHLC sanity, windowing), `tests/app/test_api.py` (endpoints, kill-switch
file semantics), and `tests/integration/test_worker_runtime.py` — the
**actual worker** run for 80 cycles: asserts a complete entry→exit trade
with nonzero P&L, every fill journaled AND persisted, cash/P&L consistency,
dashboard state shape, and kill-switch entry blocking.

## 3. Trading strategy review

### Original weaknesses found
1. **rsi2_mean_reversion ignored its own time stop.** `time_stop_bars: 5` sat
   in config, unimplemented. A mean-reversion trade that neither bounced nor
   stopped out would linger indefinitely — exactly the trade type that decays
   into a large loss. It also emitted exit signals with no way to know it was
   in a position, and could pyramid entries every bar RSI stayed < 10.
2. **trend_pullback could pyramid** the same pullback on consecutive bars
   (bounded only by the global position caps).
3. **Strategies had no position awareness at all** — by design they can't
   import the portfolio, but nothing injected read-only position state, so
   exit logic was structurally impossible.

### Improvements made
- `StrategyContext` now carries runtime-injected, read-only `position_qty`
  and `bars_held` (populated identically by the live worker and the
  backtester — one code path preserved; the isolation rule still holds and
  its AST test still passes).
- rsi2: time stop implemented (`time_stop_bars`), exits only when actually
  positioned, no re-entry while positioned.
- trend_pullback: no adding to an open position; exits remain the ATR stop /
  2R target's job.
- Exit signals (FLAT) skip the quality gauntlet but still pass the risk
  engine — exits are survival actions and can no longer be trapped by a
  regime flip.

### Evidence and honest limits
Verified mechanically on the deterministic sim feed (live run):
6 closed trades — wins +347.76, +434.60, +358.60 vs losses −183.64, −215.10,
−212.39 — i.e. the configured ~2:1 reward:risk shape is realized, stops and
targets both trigger, and the funnel rejected volatility- and
regime-mismatched entries along the way. **This demonstrates the machinery
behaves as specified. It is NOT evidence of market edge** — the sim feed is
a synthetic uptrend. No parameter was tuned, deliberately: tuning against
synthetic data would be overfitting to nothing. Real conclusions require the
MVP §11 protocol (walk-forward, ≥100 OOS trades, Monte Carlo) on real
historical data, and the tooling for that (`backend/backtest/`) is built and
tested.

### Remaining risks
Both strategies are long-only; TREND_DOWN produces no trades. Earnings
calendar isn't wired (stage 6 has the hook, no data source), so the
earnings blackout is inert on real data. The opening-range-breakout strategy
from the spec's recommended suite is not yet implemented.

## 4. Required user configuration

**Nothing, for simulated paper trading.** For more:

| Goal | What you need |
|---|---|
| Real market data (paper) | Alpaca paper keys in `.env` + `market.yaml → provider: alpaca` |
| Docker deployment | Docker Desktop + `POSTGRES_PASSWORD` in `.env` |
| Telegram alerts | `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` in `.env` |
| Dashboard | Node 20+, `cd frontend && npm install` |

Full walkthrough: [SETUP_GUIDE.md](SETUP_GUIDE.md). Operations:
[USER_MANUAL.md](USER_MANUAL.md).

## 5. Known limitations

1. **Backtest fills vs paper fills use different cost models.** The
   backtester fills via `CostModel` (ATR-scaled slippage); the paper broker
   uses flat `slippage_bps`. Both conservative and config-driven, but not
   literally one fill path. Left as-is deliberately: unifying them means
   refactoring the deterministic engine for marginal benefit; revisit before
   Gate A comparisons.
2. **No Alembic scaffolding yet.** Schema is created idempotently
   (`create_all` locally; `database/migrations/001_initial_schema.sql` via
   initdb in Docker). First real schema *change* should introduce Alembic.
3. **Paper portfolio state is not restored on restart.** The journal/trades
   persist (audit trail), but open paper positions reset with the process —
   acceptable for paper; the live path would rely on broker reconciliation.
4. **Alpaca live-bar streaming** uses polling (robust) rather than the
   websocket subscribe path for intraday aggregation; websocket code exists
   but isn't the default. Telegram *inbound* commands (kill via chat) not
   implemented; outbound alerts work.
5. **Daily bars in Alpaca mode include the forming (partial) bar** for the
   current day — current-data-only (no look-ahead), but indicator values on
   the daily frame move intraday. The sim feed is close-stamped and immune.
6. **JWT auth on the dashboard is not enforced yet** — mitigated by
   localhost-only binding; add before exposing beyond 127.0.0.1.
7. **Frontend widgets poll REST**; the `/ws/live` websocket now streams
   snapshots but the UI doesn't consume it yet.

## 6. Verification record (what was actually run)

| Check | Result |
|---|---|
| `pytest tests/` | 235 passed, 0 failed |
| `python scripts/watchdog_drill.py` | 3/3 drills PASS |
| Live platform (`run_local.py`, 8123) | worker alive, sim clock advancing |
| Complete paper trades over HTTP | 6 closed trades, entry→exit, P&L correct |
| Portfolio endpoint | equity 100,806.28 (+806.28), positions marked, 41-point equity curve |
| Funnel endpoint | 8 stages, rejections at regime_gate (10) and volatility_band (5) |
| Kill switch via API | file created → worker halted <2s → re-arm → resumed |
| Audit trail after process kill | 207 journal rows, 14 fills, 6 trades, 68 equity snapshots in SQLite |
| `npm run build` (frontend) | clean, 7 routes |
