# AlgoTrader AI
## Local-First Algorithmic Stock Trading Platform
**Version: MVP v1.1 (Revised)**
**Deployment target: a single local machine running Docker Compose. No cloud services.**
 
> **Read this first.** This document describes software architecture and quantitative engineering practices. It is not investment advice, and no signal filter, validation pipeline, or AI model guarantees profitability. Backtests systematically overstate live results. The design below improves your *odds* by cutting low-quality trades, overfitting, and operational failures — the three ways most retail algos actually lose money. Keep the system in Paper Trading mode until it passes the Promotion Gates defined in Section 12.
 
---
 
# What Changed from v1.0
 
1. **Scope locked to local-only.** Cloud deployment and distributed workers removed from the roadmap. The only outbound network traffic is to market data APIs, broker APIs, notification services (Telegram), and optionally the Anthropic API.
2. **Role of Claude Fable 5 defined** (Section 4): build-time coding assistant plus an optional asynchronous "AI Analyst" service. Claude is deliberately kept **out of the real-time trade execution path**.
3. **New: Signal Validation Pipeline** (Section 8) — the "advanced algorithm check." Every raw strategy signal must pass a multi-stage, fully-logged gauntlet before it can reach the Risk Engine.
4. **New: Market Regime Detection module** (Section 7). Strategies only run in regimes they are suited for. In practice this is the single biggest win-rate lever available to a retail system.
5. **New: Robustness & Anti-Overfitting framework** (Section 11): walk-forward optimization, purged cross-validation, Monte Carlo drawdown analysis, parameter plateau checks.
6. **New: Promotion Gates and Live-vs-Backtest Drift Monitor** (Section 12): objective criteria for moving backtest → paper → live, and automatic strategy pausing when live behavior diverges from backtest expectations.
7. **New: Unattended Operation Safeguards** (Section 13): watchdog, heartbeats, kill switch, auto-flatten policies, broker state reconciliation. Required because the system trades real money with nobody watching.
8. **Reframed the objective** from "maximize win rate" to "maximize positive expectancy with controlled drawdown" (Section 3). Win rate is a component, not the target — chasing it blindly is how accounts blow up.
---
 
# 1. Project Goal
 
Build a local-first automated stock trading platform that:
 
- Monitors live stock prices
- Downloads and stores historical market data
- Runs technical analysis and detects the current market regime
- Runs custom trading algorithms as isolated plugins
- **Validates every signal through an advanced multi-stage quality gate before trading it**
- Backtests strategies with realistic costs and anti-overfitting protections
- Paper trades with the exact same code path as live trading
- Trades live through supported brokers only after passing objective promotion criteria
- Manages risk automatically and independently of strategy code
- Operates completely unattended, with watchdogs and a kill switch
- Runs entirely inside Docker on one machine, with internet egress to broker and data APIs
The platform is for personal use. Priorities, in order: **safety, reliability, reproducibility, then performance, then features.**
 
---
 
# 2. Design Philosophy
 
- **Local First** — all state lives on your machine; external calls are limited to data/broker/notification APIs
- **Docker Ready** — `docker compose up` starts everything
- **Fail Flat** — when anything is uncertain (stale data, broker disconnect, watchdog timeout), the system stops opening positions and, if configured, closes existing ones. Doing nothing is always a valid and safe state.
- **Deterministic Core** — the decision path (data → signal → validation → risk → order) is deterministic code. Same inputs, same outputs, in backtest and live. This is what makes backtests meaningful.
- **Modular & Event Driven** — strategies, validators, brokers, and data providers are pluggable behind interfaces
- **Highly Observable** — every decision, including every *rejected* signal, is logged with the reason
- **Config Driven** — no magic numbers in code
---
 
# 3. Realistic Expectations (Win Rate, Honestly)
 
This section exists because "better win rate" is the stated goal, and it deserves a straight answer.
 
**Expectancy is the real target:**
 
```
Expectancy per trade = (WinRate × AvgWin) − ((1 − WinRate) × AvgLoss)
```
 
A 40% win-rate strategy with 3:1 reward:risk is profitable. A 90% win-rate strategy that occasionally loses 30× its average win (martingale, grid systems, selling into news) will eventually destroy the account. The validation pipeline in this document raises win rate the only sustainable way: **by refusing to take low-quality trades**, which means the system will trade less often than a naive version. Fewer, better trades is the design intent, not a bug.
 
Other facts the architecture takes seriously:
 
- Backtest results degrade live due to slippage, latency, data differences, and overfitting. Walk-forward efficiency of 50–70% (live/out-of-sample performance vs in-sample) is considered *good*.
- Edges decay. Every strategy gets scheduled revalidation (quarterly by default) and an automatic drift monitor.
- Most parameter-optimized strategies are curve-fit noise. Section 11 exists specifically to catch this before money is at risk.
---
 
# 4. Role of Claude Fable 5
 
Claude is used in two ways, and deliberately **not** used in a third.
 
## 4.1 Build-time: coding assistant
 
Use Claude Code (Anthropic's agentic coding tool, see https://docs.claude.com/en/docs/claude-code/overview) to scaffold modules, write strategies against the plugin interface, generate tests, and review pull requests into your own repo. This is where Fable 5 adds the most value with zero runtime risk.
 
## 4.2 Runtime (optional): the `ai_analyst` service
 
An asynchronous, non-critical container that calls the Anthropic Messages API (https://docs.claude.com/en/api/overview) to:
 
- Produce a **nightly trade journal review**: summarize the day's trades, flag anomalies (slippage spikes, unusual rejection rates in the validation funnel, fills far from signal price)
- Write **strategy post-mortems** after a strategy is paused by the drift monitor
- **Explain rejections in plain language** ("this signal failed the regime gate because ADX was 12 — the market is ranging and this is a trend strategy")
- **Draft candidate strategy code** — which is always treated as untrusted input: human review, then the full backtest/walk-forward/paper pipeline like any other strategy
Rules for this service:
 
- `AI_ANALYST_ENABLED=false` by default. When disabled, the platform is fully local except for data/broker traffic.
- It has **read-only** access to the journal and logs. It cannot place, modify, or cancel orders. Not "shouldn't" — the execution API is not reachable from this container.
- Secrets are scrubbed before anything is sent to the API. Broker credentials never leave the machine.
## 4.3 Explicitly NOT: Claude in the execution path
 
Claude does not generate or approve trades in real time, because:
 
- **Non-determinism** breaks backtest/live parity — you cannot backtest an LLM's future opinions, so you'd be trading an unvalidated system
- **Latency** (seconds) and **per-call cost** are incompatible with tick/bar-driven execution
- **Availability**: an unattended trading system must not have a hard runtime dependency on an external AI API
The deterministic pipeline decides trades. Claude helps you build and improve the pipeline.
 
---
 
# 5. Core Architecture
 
```
                        Internet
                            │
        ┌───────────────────┼──────────────────────┐
        ▼                   ▼                      ▼
  Market Data APIs     Broker APIs          Anthropic API (optional)
  Polygon / Alpaca     Alpaca / IBKR        Telegram API
  Finnhub / TwelveData      ▲                      ▲
        │                   │                      │
        ▼                   │                      │
 ┌──────────────┐           │               ┌──────┴───────┐
 │ Market Data  │           │               │  AI Analyst   │ (read-only,
 │   Service    │           │               │  + Notifier   │  async)
 └──────┬───────┘           │               └──────▲───────┘
        ▼                   │                      │
 ┌──────────────┐           │                      │
 │ Historical DB│◄──────────┼──────────────────────┤
 │ (Postgres/   │           │                      │
 │  Timescale)  │           │                      │
 └──────┬───────┘           │                      │
        ▼                   │                      │
 ┌──────────────┐           │                      │
 │    Regime    │           │                      │
 │   Detector   │           │                      │
 └──────┬───────┘           │                      │
        ▼                   │                      │
 ┌──────────────┐   raw     │                      │
 │   Strategy   │  signals  │                      │
 │    Engine    ├─────┐     │                      │
 └──────────────┘     ▼     │                      │
              ┌───────────────────┐                │
              │ SIGNAL VALIDATION │                │
              │     PIPELINE      │──rejections────┤ (logged to
              └────────┬──────────┘                │  journal)
                       ▼ validated + scored        │
              ┌───────────────────┐                │
              │    Risk Engine    │──rejections────┤
              └────────┬──────────┘                │
                       ▼ approved orders           │
              ┌───────────────────┐                │
              │ Execution Manager │────────────────┘
              └───┬───────────┬───┘
                  ▼           ▼
            Paper Broker   Live Broker Adapter ──► Broker API
                  │           │
                  ▼           ▼
              Portfolio + Trade Journal DB
 
   ┌─────────────────────────────────────────────┐
   │  WATCHDOG: heartbeats, staleness checks,    │
   │  kill switch, auto-flatten, circuit breakers│
   │  — supervises every component above         │
   └─────────────────────────────────────────────┘
```
 
Key structural rules:
 
- Strategies emit *intents* (signals), never orders
- Only the Execution Manager talks to brokers; only the Risk Engine feeds the Execution Manager
- The Signal Validation Pipeline and Risk Engine run identically in backtest, paper, and live
---
 
# 6. Technology Stack
 
## Backend — Python 3.12
 
Python dominates quant tooling; keep it.
 
- **FastAPI** — REST + WebSocket API for the dashboard
- **SQLAlchemy 2.x** (async) — ORM
- **Pandas / Polars / NumPy** — data wrangling (Polars for large historical scans)
- **Indicators**: `pandas-ta` or `ta` as the default (pure Python, painless in Docker). TA-Lib is optional — it needs the C library baked into the image; only add it if you want its exact implementations.
- **vectorbt** — *research only*: fast vectorized parameter sweeps and plateau heatmaps
- **Custom event-driven engine** — the *canonical* backtester. One engine, one code path for backtest/paper/live. Do not maintain strategy logic in two frameworks; vectorbt findings must be confirmed in the event-driven engine before promotion.
- **APScheduler**, **asyncio**, **websockets**, **Pydantic v2** (all config and messages are typed models)
- **scikit-learn / LightGBM** — Phase 2 meta-model
## Frontend
 
- **Next.js + React**, **shadcn/ui**, **Zustand**, **TradingView Lightweight Charts**, WebSockets for live updates
- Served on `127.0.0.1` by default — the dashboard is not exposed to your network unless you explicitly change it
## Database — PostgreSQL 16
 
- With the **TimescaleDB** extension (optional but recommended): hypertables + compression for OHLCV make multi-year 1m data cheap to store and fast to query
- ACID, great indexing, one database for candles, trades, journal, and config history
## Cache / Bus — Redis
 
- Latest quotes, pub/sub for signal and fill events, dedupe keys for order idempotency, dashboard live feed
## Broker APIs (pluggable `BrokerAdapter` interface)
 
- **Alpaca** first — clean REST/WebSocket API and a proper paper-trading environment that mirrors live
- **Interactive Brokers** second — broad international market access, more integration effort (TWS/Gateway container)
- Future: Moomoo, Tiger Brokers, Binance/Bybit (crypto)
## Market Data (pluggable `DataProvider` interface)
 
- Polygon, Finnhub, TwelveData, AlphaVantage; Alpaca's own data for convenience
- Yahoo Finance for *research-grade historical only* — never for execution decisions
- Switching providers must not require touching strategy code
---
 
# 7. Market Regime Detection (NEW)
 
**Why it's first-class:** most strategies are not "bad," they are *good in one regime and terrible in another*. Trend systems bleed in chop; mean-reversion gets steamrolled in strong trends. Gating strategies by regime typically improves win rate more than tuning any indicator parameter.
 
The Regime Detector runs on schedule (e.g., every bar close on 1h + daily) and publishes a market state consumed by the Strategy Engine and Validation Pipeline.
 
**Classification inputs (computed on the index/benchmark, e.g., SPY, and per-symbol):**
 
- **Trend strength**: ADX(14) on daily; slope of EMA(50); price vs EMA(200)
- **Trend direction**: EMA(50) vs EMA(200); higher-highs/higher-lows structure
- **Volatility state**: realized volatility (20-day) percentile vs 1-year history; ATR(14) percentile
- Optional: Hurst exponent over 100 bars (>0.55 trending, <0.45 mean-reverting)
**Output labels (config-driven thresholds):**
 
| Regime | Heuristic (defaults) |
|---|---|
| `TREND_UP` | ADX > 25, EMA50 > EMA200, EMA50 slope > 0 |
| `TREND_DOWN` | ADX > 25, EMA50 < EMA200, EMA50 slope < 0 |
| `RANGE` | ADX < 20 |
| `HIGH_VOL` | Realized vol percentile > 90 (overrides others; most strategies pause) |
| `TRANSITION` | Anything else — reduced size or no new entries |
 
Every strategy declares `allowed_regimes` in its config. Signals outside allowed regimes are rejected at Validation Stage 1 — and the rejection is logged so you can later measure whether the gate actually helped.
 
---
 
# 8. Signal Validation Pipeline — the Advanced Algorithm Check (NEW)
 
This is the core answer to "check signals harder before trading them." A raw signal from any strategy must pass **every gate** below, in order, and earn a minimum **confluence score**. Each stage logs `pass/fail + measured values` to the journal, producing a *validation funnel* you can analyze and A/B test by replaying history with individual stages disabled.
 
The pipeline is deterministic and runs **identically in backtest** — otherwise your backtest would be testing a different system than the one trading.
 
### Stage 0 — Data sanity
- Last bar age < 2× the strategy interval (no stale data)
- No gaps in the recent lookback window; volume > 0; price within exchange bands
- Fail → signal dropped AND a data-quality alert fires
### Stage 1 — Regime gate
- Current regime (Section 7) ∈ strategy's `allowed_regimes`
- `HIGH_VOL` regime blocks all new entries unless a strategy explicitly opts in
### Stage 2 — Multi-timeframe alignment
- Longs: price above daily EMA(200) and 1h EMA(50) rising (defaults; configurable per strategy)
- Shorts: mirrored
- Rationale: entering on a 5m signal *with* the higher-timeframe tide, not against it, is one of the most reliable win-rate improvements in intraday/swing systems
### Stage 3 — Volume confirmation
- Relative volume ≥ 1.2× the 20-bar average for breakout-type signals
- OBV slope agrees with signal direction
- Skip-able per strategy (mean-reversion entries legitimately fire on volume dry-ups)
### Stage 4 — Volatility band
- ATR(14) percentile within `[20, 90]` of its 1-year distribution
- Too quiet → no follow-through (churn + costs); too wild → stops get blown through and slippage explodes
### Stage 5 — Confluence scoring (0–100, threshold ≥ 70 by default)
Weighted sum of *independent* confirmations, weights in config:
 
| Component | Default weight |
|---|---|
| Higher-timeframe trend agreement | 25 |
| Momentum agreement (RSI/MACD direction, not overbought against entry) | 20 |
| Volume confirmation strength | 15 |
| Distance from nearest support/resistance ≥ 1× ATR in trade direction | 15 |
| Market breadth proxy (e.g., SPY above VWAP for longs) | 15 |
| Volatility band position (mid-band scores highest) | 10 |
 
A signal scoring 68 dies here. That is the point: marginal setups are where the losses live.
 
### Stage 6 — Event & time filters
- No entries in the first 15 minutes or last 10 minutes of the session (configurable)
- No entries within N days (default 2) of the symbol's earnings date
- Optional macro-event blackout calendar (FOMC, CPI) loaded from config
### Stage 7 — Portfolio & correlation gate
- **Portfolio heat**: sum of open risk (distance to stops × size) ≤ 5% of equity
- Reject if 60-day return correlation with any existing position > 0.7 and combined exposure would exceed the correlated-exposure cap
- Sector exposure cap (default 30% of equity per sector)
- Max concurrent positions per strategy and globally
### Stage 8 — Meta-model gate (Phase 2)
- A LightGBM classifier trained on *features captured at past signal time* with triple-barrier labels (profit target / stop / time exit) — the "meta-labeling" approach from López de Prado
- The base strategy proposes; the meta-model estimates P(win); trade only if P(win) ≥ 0.58 (tunable)
- Trained with purged, embargoed cross-validation only (Section 11); retrained on schedule, never intraday
- This stage directly optimizes precision — i.e., win rate — on top of an already-validated signal stream
**Output:** a `ValidatedSignal {signal, score, stage_results[], regime, timestamp}` handed to the Risk Engine. The Risk Engine can still veto (limits, drawdown state, kill switch) — validation is about *quality*, risk is about *survival*, and they stay separate modules.
 
---
 
# 9. Strategy Engine & Baseline Strategy Suite
 
Strategies are isolated plugins with a fixed interface:
 
```
initialize(config, context)
on_bar(bar)            # and optionally on_tick(tick)
generate_signal() -> Signal | None
teardown()
```
 
Each strategy ships with `config.yaml` (symbol universe, interval, parameters, `allowed_regimes`, per-stage validation overrides, risk-per-trade). No hardcoded values. Strategies **cannot** import broker or execution modules — enforced by an import-linter rule in CI.
 
**Recommended starting suite** (opinionated — each has a documented, regime-dependent edge and they diversify each other):
 
1. **Trend pullback** (`TREND_UP` only): price > EMA(200) daily, buy pullbacks to EMA(20) on 1h with momentum resuming. Moderate win rate, strong R:R.
2. **Short-term mean reversion in uptrends** (`TREND_UP`/`RANGE`): RSI(2) < 10 with price above EMA(200), exit on RSI(2) > 65 or time stop. Historically high win rate, small wins — sensitive to costs, so the volume/volatility gates matter.
3. **Opening range breakout with relative volume** (`TREND_*`): 15-min opening range break + RVOL ≥ 1.5. Lower win rate, fat right tail; the confluence score filters the chop.
4. *(Phase 2)* **Pairs / statistical arbitrage** on cointegrated pairs — market-neutral, diversifies the book in `RANGE`.
Classic single-indicator strategies (MA cross, MACD, Bollinger) remain in the repo as teaching examples and building blocks, with the honest note that **unfiltered, they have no persistent edge** — the regime gate + pipeline is what makes derivatives of them tradeable.
 
## Indicators library
 
SMA, EMA, RSI, MACD, Bollinger Bands, ATR, VWAP, Supertrend, ADX, Ichimoku, OBV, Relative Volume, realized volatility, rolling percentile ranks, (optional) Hurst exponent, volume profile.
 
---
 
# 10. Risk Engine, Position Sizing & Execution
 
## Risk Engine (mandatory, strategy-independent)
 
Hard checks on every validated signal and continuously on the portfolio:
 
- Max daily loss (halts **all** trading for the day when hit — circuit breaker)
- Max drawdown from equity peak (halts and requires manual re-arm)
- Max position size, max open trades (global and per strategy), max leverage (default 1.0 — cash only)
- Risk per trade cap; portfolio heat cap (Section 8, Stage 7 values re-verified here)
- Consecutive-loss cooldown: a strategy that loses N times in a row (default 4) pauses for a configurable period
- Trading-hours enforcement; live-mode requires the arming procedure in Section 15
- Stop-loss, take-profit, trailing-stop attached to **every** order — an entry without a stop is rejected
## Position sizing
 
- **Fixed fractional risk** (default 0.5–1.0% of equity per trade):
  `shares = floor((equity × risk_per_trade) / (entry − stop_price))`
- Stops are **volatility-based** by default (e.g., 2 × ATR(14)), so size automatically shrinks when volatility expands
- *(Phase 2, optional)* fractional Kelly (¼ Kelly), only after ≥ 200 out-of-sample trades exist to estimate edge, and always capped by the fixed-fractional limit
## Execution Manager
 
- Order state machine: `PENDING → SUBMITTED → PARTIAL → FILLED / CANCELLED / REJECTED / EXPIRED`
- **Idempotency keys** on every order (Redis) — a retry can never create a duplicate position
- Retry with exponential backoff on transient broker errors; verify fills against broker truth
- **Reconciliation on startup and every N minutes**: broker positions/orders are authoritative; any mismatch with the local portfolio triggers an alert and blocks new entries until resolved
- Paper broker simulates fills using the same order state machine, with configurable slippage and latency so paper ≈ live
## Portfolio & Trade Journal
 
Tracks cash, buying power, open/closed positions, realized/unrealized PnL, daily PnL, equity curve, and a **full journal**: every signal (validated or rejected, with stage results), every order event, every fill, every risk veto. The journal is the dataset that powers Section 11, Stage 8's meta-model, and the AI Analyst.
 
---
 
# 11. Backtesting & Anti-Overfitting Framework
 
One event-driven engine, one code path (backtest = paper = live). Backtests are deterministic (seeded, pinned data snapshots).
 
## Realistic cost model (non-negotiable)
 
- Commission per share/order per broker config
- Slippage = ½ spread + volatility-scaled impact component
- Simulated order latency (default 300 ms) between signal and fill
- Fills respect bar OHLC logic (no fills at prices the bar never traded; conservative same-bar stop/target resolution)
## Metrics
 
Net profit, **expectancy**, profit factor, win rate, avg win/avg loss, Sharpe, Sortino, max drawdown, MAR (CAGR/MaxDD), exposure %, trade count, longest losing streak, equity curve, per-stage validation funnel stats.
 
## Anti-overfitting protocol (a strategy is not "done" until all of these pass)
 
1. **Data split**: 60/20/20 train/validate/test by time. The test segment is touched **once**, at the end.
2. **Walk-forward optimization**: e.g., rolling 2-year train → 6-month trade, stepping forward. Report **Walk-Forward Efficiency** = OOS performance ÷ IS performance. Require ≥ 0.5.
3. **Parameter plateau analysis**: sweep parameters (vectorbt), heatmap the results, and pick the **center of a stable plateau**, never the peak. Reject any strategy whose profit collapses with ±20% parameter perturbation.
4. **Sample size**: ≥ 100 out-of-sample trades before any conclusion. Below that, results are noise.
5. **Monte Carlo**: 10,000 bootstrap resamples of the OOS trade sequence (plus block bootstrap of returns) → distribution of max drawdown and terminal equity. The **95th-percentile drawdown** must be inside your risk tolerance; probability of hitting the account-level max-drawdown halt must be ≈ 0 at chosen sizing.
6. **Multiple-testing honesty**: log every variant you tried. The more you searched, the higher the bar (deflated Sharpe ratio as a sanity check). Ten failed variants make the eleventh "winner" suspect by default.
7. **Data hygiene**: survivorship-bias-aware symbol universe (store delistings), split/dividend-adjusted candles with raw preserved, strict point-in-time discipline (no look-ahead: signals at bar *t* may only use data ≤ *t*).
8. **Meta-model discipline (Stage 8)**: purged & embargoed k-fold CV only; features and labels engineered exclusively from journal data available at signal time.
---
 
# 12. Promotion Gates & Drift Monitor (NEW)
 
Objective, pre-committed criteria — decided before you see results, so you can't rationalize.
 
**Gate A — Backtest → Paper.** OOS: profit factor ≥ 1.3, expectancy > 0 after costs, max DD ≤ 15%, ≥ 100 trades, WFE ≥ 0.5, plateau check passed, Monte Carlo 95th-pct DD within tolerance.
 
**Gate B — Paper → Live (small).** ≥ 60 trading days of paper AND ≥ 30 trades; paper expectancy and drawdown inside the Monte Carlo 80% band from Gate A; zero unexplained order/reconciliation errors; all watchdog drills passed (Section 13).
 
**Gate C — Small live → Full size.** 30+ days at 25% of intended size with live slippage within 1.5× the modeled slippage and results still inside the expected band.
 
**Live-vs-Backtest Drift Monitor (always on).** Rolling 20-trade live expectancy and slippage are compared against the backtest/Monte Carlo distribution. Below the 5th percentile → the strategy is **auto-paused**, an alert fires, and the AI Analyst (if enabled) drafts a post-mortem. Every strategy is also re-validated quarterly against fresh data regardless of performance. Edges decay; the system assumes it.
 
---
 
# 13. Unattended Operation Safeguards (NEW)
 
Because "operates completely unattended" + real money is the highest-risk requirement in this document:
 
- **Watchdog service**: every component emits heartbeats to Redis; a missed heartbeat (default 30 s) → trading halts, alert fires
- **Data staleness policy**: quotes older than 2× interval → no new entries; older than a hard limit with open positions → configurable **auto-flatten** (close everything at market) or hold-with-alert
- **Broker disconnect policy**: reconnect with backoff; while disconnected, the system assumes nothing about fills and reconciles fully on reconnect before trading resumes
- **Kill switch**: a dashboard button *and* a file-based trigger (`touch data/KILL`) *and* a Telegram command — any of them cancels open orders, optionally flattens, and disarms live mode until manually re-armed
- **Daily loss circuit breaker** halts all strategies until the next session
- **Host-level**: `restart: unless-stopped` on all containers, Docker healthchecks, NTP time sync on the host (timestamp skew silently corrupts bar alignment), disk-space monitor, nightly Postgres dump to a local backup volume
- **Watchdog drills** are part of Gate B: deliberately kill the data feed and the broker connection in paper mode and verify the system fails flat
---
 
# 14. Scheduler, Notifications, Logging
 
- **Scheduler** (APScheduler): per-second/minute/hour/day jobs — data refresh, regime updates, strategy bars, reconciliation, nightly reports, quarterly revalidation reminders
- **Notifications** (Telegram first; Discord/desktop/email later): trade executed, stop hit, circuit breaker tripped, watchdog/staleness alerts, drift-monitor pauses, daily summary, kill-switch confirmations
- **Logging**: structured JSON logs for every event — signal generated, **signal rejected (stage + values)**, order lifecycle transitions, risk vetoes, API failures, strategy start/stop. Logs rotate locally; nothing ships off-machine.
---
 
# 15. Security
 
- Secrets only in `.env` (git-ignored); never hardcode API keys, broker credentials, JWT secrets
- **Separate credentials for paper and live**; live keys created with **trading-only permissions — no withdrawal/transfer scope** where the broker supports it (Alpaca does)
- Live trading is **disarmed by default**. Arming requires all three: `LIVE_TRADING=true` in env, a signed confirmation file, and a dashboard confirmation with typed acknowledgment. Any kill-switch event disarms it again.
- Dashboard binds to `127.0.0.1`, JWT-protected regardless
- Secrets scrubbed from logs and from anything sent to the Anthropic or Telegram APIs
---
 
# 16. Docker
 
Services (Docker Compose): `frontend`, `backend` (API), `worker` (scheduler + strategy runtime), `postgres` (TimescaleDB image), `redis`, and optional `ai_analyst`. For the MVP, `backend` and `worker` may be one container; split later.
 
Requirements:
 
- `docker compose up -d` starts the whole platform
- Named volumes for Postgres, Redis, logs, and backups — state survives container rebuilds
- Healthchecks on every service; `restart: unless-stopped`
- Egress allowed to data/broker/Telegram/Anthropic endpoints; **no inbound ports exposed beyond the localhost dashboard**
- Pinned image versions and a lockfile (`uv`/`pip-tools`) for reproducible builds
- Resource limits so a runaway backtest can't starve the live worker
---
 
# 17. Project Structure
 
```
AlgoTrader/
  backend/
    app/            # FastAPI
    core/           # config, events, DI container
    data/           # providers, downloader, quality checks
    regime/         # regime detector
    strategies/     # plugins (one folder per strategy + config.yaml)
    validation/     # signal validation pipeline (stages/, scoring.py)
    risk/           # risk engine, position sizing
    execution/      # order state machine, broker adapters, paper broker
    portfolio/      # accounting + trade journal
    backtest/       # event-driven engine, cost models, walk-forward, monte_carlo
    ml/             # meta-labeling (Phase 2)
    watchdog/
    notifications/
    ai_analyst/     # optional Claude-powered reporting service
  frontend/
  configs/          # market.yaml broker.yaml risk.yaml validation.yaml strategies/
  database/         # migrations (Alembic)
  scripts/          # backup, data download, drills
  docker/
  logs/
  tests/
```
 
---
 
# 18. Dashboard
 
Sections: Dashboard, Portfolio, Open Positions, Orders, Strategies, **Validation Funnel**, **Regime Monitor**, Market Scanner, Signals, Charts, Backtests, Logs, Settings.
 
Key widgets: equity + daily PnL, open trades, running strategies with per-strategy state (active/paused/cooldown), **current regime**, latest signals *with validation scores and rejection reasons*, broker/API/watchdog status, drift-monitor status per strategy, **KILL SWITCH button**, CPU/RAM/disk.
 
---
 
# 19. MVP Scope
 
## Phase 1 — the trustworthy paper trader
✅ Docker Compose deployment · historical download + data-quality checks · live market data · charts + indicators · **Regime Detector v1** · 2–3 baseline strategies · **Signal Validation Pipeline stages 0–7 with funnel logging** · Risk Engine + volatility-based sizing · event-driven backtester with realistic costs · **walk-forward + Monte Carlo tooling** · paper trading via the identical code path · portfolio + journal · Telegram alerts · **watchdog + kill switch** · validation-funnel and regime dashboard views
 
## Phase 2 — carefully live
Live trading behind Gates A–C · IBKR adapter · **meta-labeling model (Stage 8)** · drift monitor auto-pause · multi-strategy regime-based allocation · pairs/stat-arb strategy · parameter plateau tooling in the UI · AI Analyst nightly reports · portfolio optimization
 
## Phase 3 — research frontier (still local)
Reinforcement learning **as research, never straight to live** · news/sentiment inputs (mind data licensing costs) · options support · multi-asset (crypto/forex via existing adapter interface) · automatic parameter re-optimization proposals (human-approved)
 
**Removed from roadmap (out of scope for local-first):** cloud deployment, distributed workers, strategy marketplace, mobile app.
 
---
 
# 20. Non-Functional Requirements & Development Guidelines
 
- Local-first; Docker-compatible; internet egress from containers; modular; clean architecture; SOLID; type hints everywhere; Pydantic-validated configs; high unit + integration test coverage (recorded API fixtures for broker/data adapters); deterministic backtests; graceful shutdown (cancel/flatten policy on SIGTERM); structured logging; plugin strategies; configuration-driven — no magic numbers
- Git from day one; strategies isolated from execution (lint-enforced); **all orders pass through Risk Engine — no exceptions**; dependency injection; composition over inheritance; tests for every strategy *and every validation stage*; separate paper/live credentials; **paper mode is the default state of the system, always**
---
 
# Appendix: Why this design should improve your win rate (and what it won't do)
 
It will: refuse regime-mismatched trades, refuse counter-trend entries against higher timeframes, refuse low-volume and extreme-volatility setups, refuse marginal-confluence signals, refuse correlated pile-ons, catch curve-fit strategies before they trade, catch decaying strategies while they trade, and prevent the operational failures (duplicate orders, stale data, unattended runaway losses) that quietly ruin live results.
 
It won't: manufacture an edge that isn't in the underlying strategy, predict the future, or make backtest numbers appear live. Expect fewer trades, a higher percentage of good ones, and — most importantly — survival long enough to iterate.