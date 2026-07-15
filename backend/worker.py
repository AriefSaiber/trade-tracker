"""Worker: the paper-trading runtime (MVP §5 — one code path).

Drives the identical decision path the backtester and the integration test
exercise, against a live-advancing clock:

    DataProvider -> RegimeDetector -> strategies -> SignalValidationPipeline
        -> RiskEngine -> ExecutionManager(PaperBroker) -> Portfolio + Journal

supervised by the watchdog trio (HeartbeatMonitor, StalenessMonitor,
KillSwitch), persisted via PersistenceService, and published to the dashboard
through the shared StateStore.

Two clock modes, selected by ``configs/market.yaml: provider``:
- ``simulated`` — the clock advances one session bar per cycle through the
  deterministic synthetic series (paper trading with zero API keys).
- ``alpaca``    — wall clock; bars are polled from the Alpaca data API.

LIVE_TRADING=false keeps everything on the paper path; live mode refuses to
start until the full arming procedure (MVP §15) exists.
"""
from __future__ import annotations

import argparse
import asyncio
import importlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import structlog
import yaml

from backend.core.config import Settings, get_settings, load_yaml_config
from backend.core.event_bus import TOPIC_ALERT, EventBus
from backend.core.events import (
    Bar, Fill, Regime, RegimeState, Signal, ValidatedSignal,
)
from backend.core.state import (
    KEY_ALERTS, KEY_FUNNEL, KEY_FUNNEL_SUMMARY, KEY_ORDERS, KEY_PORTFOLIO,
    KEY_REGIME, KEY_SIGNALS, KEY_STRATEGIES, KEY_WORKER,
    InMemoryStateStore, StateStore, connect_state_store,
)
from backend.data import indicators as ind
from backend.data.provider import DataProvider
from backend.data.simulated_provider import SimulatedDataProvider
from backend.execution.manager import ExecutionManager
from backend.execution.paper_broker import PaperBroker
from backend.notifications.dispatcher import Notifier, NullNotifier, TelegramNotifier
from backend.portfolio.journal import TradeJournal
from backend.portfolio.persistence import PersistenceService
from backend.portfolio.portfolio import Portfolio
from backend.regime.detector import RegimeDetector
from backend.risk.engine import AccountState, RiskEngine
from backend.strategies.base import StrategyBase, StrategyContext
from backend.validation.funnel_logger import FunnelLogger
from backend.validation.pipeline import SignalValidationPipeline
from backend.watchdog.kill_switch import KillSwitch
from backend.watchdog.monitor import HeartbeatMonitor, InMemoryHeartbeatStore
from backend.watchdog.staleness import StalenessMonitor, interval_to_seconds

log = structlog.get_logger(__name__)

STRATEGIES_DIR = Path(__file__).resolve().parent / "strategies"


# --------------------------------------------------------------------------- #
# Strategy loading (plugins declared by their own config.yaml)
# --------------------------------------------------------------------------- #
def load_strategy_configs() -> list[dict]:
    configs: list[dict] = []
    for path in sorted(STRATEGIES_DIR.glob("*/config.yaml")):
        with open(path, "r", encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh) or {}
        if cfg.get("enabled", False):
            configs.append(cfg)
    return configs


def instantiate_strategy(class_path: str) -> StrategyBase:
    module_name, _, class_name = class_path.rpartition(".")
    cls = getattr(importlib.import_module(module_name), class_name)
    return cls()


def load_strategy_toggles(path: Path) -> dict[str, bool]:
    """Runtime enable/disable overrides ({strategy_id: bool}). Written by the
    dashboard API, polled by the worker each cycle — same cross-process file
    pattern as the KILL switch. Missing/corrupt file => no overrides."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(k): bool(v) for k, v in raw.items()} if isinstance(raw, dict) else {}


class StrategySlot:
    """One strategy instance bound to one symbol (plugins are per-bar state
    machines; a dedicated instance per symbol keeps that state unambiguous)."""

    def __init__(self, config: dict, symbol: str, ctx: StrategyContext) -> None:
        self.config = config
        self.symbol = symbol
        self.strategy_id = str(config["strategy_id"])
        self.interval = str(config["interval"])
        # crypto strategies anchor regime on their own market (e.g. BTC/USD),
        # not the equity benchmark — empty string means "use the global one"
        self.regime_benchmark = str(config.get("regime_benchmark") or "")
        self.strategy = instantiate_strategy(str(config["class"]))
        self.strategy.initialize(config, ctx)
        self.last_processed: datetime | None = None


# --------------------------------------------------------------------------- #
# Clocks
# --------------------------------------------------------------------------- #
class WallClock:
    mode = "wall"

    def tick(self) -> datetime | None:
        return datetime.now(timezone.utc)


class SimClock:
    """Advances one session-bar timestamp per tick; None when exhausted."""

    mode = "sim"

    def __init__(self, timeline: list[datetime]) -> None:
        self._timeline = timeline
        self._i = 0

    def tick(self) -> datetime | None:
        if self._i >= len(self._timeline):
            return None
        now = self._timeline[self._i]
        self._i += 1
        return now


# --------------------------------------------------------------------------- #
# The runtime
# --------------------------------------------------------------------------- #
class TradingRuntime:
    def __init__(
        self,
        settings: Settings | None = None,
        provider: DataProvider | None = None,
        state: StateStore | None = None,
        notifier: Notifier | None = None,
        poll_seconds: float | None = None,
        broker_config=None,
        database_url: str | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._market = load_yaml_config("market")
        self._worker_cfg = load_yaml_config("worker")
        self._risk_cfg = load_yaml_config("risk")

        self.bus = EventBus()
        self.state = state or InMemoryStateStore()
        self.notifier = notifier or (
            TelegramNotifier(self.settings)
            if self.settings.telegram_bot_token else NullNotifier()
        )

        self.provider = provider or self._build_provider()
        self.benchmark = str(self._market.get("benchmark_symbol", "SPY"))
        self.journal = TradeJournal()
        self.funnel = FunnelLogger(self.journal)
        self.pipeline = SignalValidationPipeline(funnel=self.funnel)
        self.risk = RiskEngine()
        self.detector = RegimeDetector(bus=self.bus)

        self.latest_price: dict[str, float] = {}
        self.broker = PaperBroker(config=broker_config,
                                  price_source=self.latest_price.get)
        self.execution = ExecutionManager(self.broker, self.bus)
        self.portfolio = Portfolio(
            starting_cash=float(self._market.get("starting_cash", 100_000.0)))

        self.heartbeats = HeartbeatMonitor(InMemoryHeartbeatStore(), bus=self.bus)
        self.staleness = StalenessMonitor(bus=self.bus)
        self.kill_switch = KillSwitch(
            bus=self.bus,
            cancel_orders=self.execution.cancel_all,
            flatten=self._flatten_all,
        )
        self.persistence = PersistenceService(
            database_url or self.settings.database_url,
            flush_interval_seconds=float(
                self._worker_cfg.get("persistence.flush_seconds", 5.0)),
        )

        if poll_seconds is not None:
            self.poll_seconds = poll_seconds
        elif isinstance(self.provider, SimulatedDataProvider):
            self.poll_seconds = float(self._worker_cfg.get("worker.poll_seconds", 2.0))
        else:
            # wall-clock providers: pace polls for API rate limits, but stay
            # under heartbeat.timeout_seconds — the worker beats once per cycle
            self.poll_seconds = float(
                self._worker_cfg.get("worker.poll_seconds_wallclock", 15.0))
        self._reconcile_every = int(
            self._worker_cfg.get("worker.reconcile_every_cycles", 10))
        self._toggle_path = Path(str(self._worker_cfg.get(
            "strategy_toggles.file_path", "data/strategy_toggles.json")))
        self._toggles: dict[str, bool] = {}

        # runtime trading state
        self.history: dict[tuple[str, str], pd.DataFrame] = {}
        self.clock: WallClock | SimClock = WallClock()
        self.now: datetime = datetime.now(timezone.utc)
        self.halted_today = False
        self._session_date = None
        self._bars_held: dict[str, int] = {}
        self._consec_losses: dict[str, int] = {}
        self._cooldown_until: dict[str, datetime] = {}
        self._seen_trades = 0
        self._signal_log: list[dict] = []
        self._alert_log: list[dict] = []
        self._regime_state: RegimeState | None = None
        self._cycle = 0
        self._tasks: list[asyncio.Task] = []
        self.slots: list[StrategySlot] = []

        self.bus.subscribe(TOPIC_ALERT, self._on_alert)

    # ── construction helpers ───────────────────────────────────────────────
    def _build_provider(self) -> DataProvider:
        name = str(self._market.get("provider", "simulated")).lower()
        if name == "simulated":
            return SimulatedDataProvider(config=self._market)
        if name == "alpaca":
            if not (self.settings.alpaca_key_id and self.settings.alpaca_secret):
                raise RuntimeError(
                    "market.yaml sets provider: alpaca but ALPACA_PAPER_KEY_ID / "
                    "ALPACA_PAPER_SECRET are not configured. Set them in .env or "
                    "switch provider back to 'simulated'."
                )
            from backend.data.alpaca_provider import AlpacaDataProvider
            return AlpacaDataProvider(self.settings)
        raise RuntimeError(f"unsupported market data provider: {name!r}")

    async def _on_alert(self, payload) -> None:
        entry = payload if isinstance(payload, dict) else {"message": str(payload)}
        self._alert_log.append(entry)
        cap = int(self._worker_cfg.get("worker.alert_log_cap", 100))
        del self._alert_log[:-cap]
        self.journal.record("alert", entry)
        level = str(entry.get("level", "info"))
        if level in ("critical", "error"):
            await self.notifier.send(str(entry.get("message", "alert")), level=level)

    async def _flatten_all(self) -> None:
        """Emergency flatten (kill switch / staleness hard limit): close every
        open position at market through the risk engine's explicit emergency
        path — still a RiskDecision, still the ExecutionManager, never a raw
        broker call from here."""
        for pos in list(self.portfolio.positions.values()):
            signal = Signal(
                strategy_id=pos.strategy_id or "watchdog",
                symbol=pos.symbol, direction="FLAT", confidence=1.0,
                bar_time=self.now, metadata={"reason": "emergency_flatten"},
            )
            await self._execute_exit(signal, ignore_survival_gates=True)

    # ── startup ────────────────────────────────────────────────────────────
    async def start(self) -> None:
        strategy_configs = load_strategy_configs()
        if not strategy_configs:
            raise RuntimeError("no enabled strategies found under backend/strategies/")

        ctx = StrategyContext(now=datetime.now(timezone.utc),
                              regime=Regime.TRANSITION, history={})
        self._ctx = ctx
        for cfg in strategy_configs:
            for symbol in cfg.get("symbols", []):
                self.slots.append(StrategySlot(cfg, str(symbol), ctx))

        await self.persistence.start()
        if not self.persistence.available:
            await self.bus.publish(TOPIC_ALERT, {
                "level": "error",
                "message": "journal persistence unavailable — trading continues, "
                           "audit trail is memory-only until the DB returns",
            })

        # clock + backfill
        if isinstance(self.provider, SimulatedDataProvider):
            bench_bars = await self.provider.get_bars(
                self.benchmark, "1h",
                datetime(2000, 1, 1, tzinfo=timezone.utc),
                datetime(2100, 1, 1, tzinfo=timezone.utc))
            timeline = [b.timestamp for b in bench_bars]
            start_back = int(self._worker_cfg.get("worker.sim_start_bars_back", 240))
            timeline = timeline[-start_back:] if start_back < len(timeline) else timeline
            self.clock = SimClock(timeline)
            self.now = timeline[0] - timedelta(hours=1)
        else:
            self.clock = WallClock()
            self.now = datetime.now(timezone.utc)

        await self._backfill()

        # restart safety: an existing KILL file keeps the switch tripped
        if self.kill_switch.check_file() and not self.kill_switch.active:
            await self.kill_switch.trigger_from_file()

        log.info("worker_started", mode="paper",
                 provider=type(self.provider).__name__,
                 clock=self.clock.mode, strategies=len(self.slots),
                 persistence=self.persistence.available)
        await self.notifier.send(
            f"worker started in PAPER mode ({type(self.provider).__name__}, "
            f"{len(self.slots)} strategy slots)")

    async def _backfill(self) -> None:
        daily_bars = int(self._worker_cfg.get("worker.backfill_daily_bars", 420))
        intraday_days = int(self._worker_cfg.get("worker.backfill_intraday_days", 120))
        pairs: set[tuple[str, str]] = {(self.benchmark, "1d")}
        for slot in self.slots:
            pairs.add((slot.symbol, "1d"))
            pairs.add((slot.symbol, slot.interval))
            if slot.regime_benchmark:
                pairs.add((slot.regime_benchmark, "1d"))
        for symbol, interval in sorted(pairs):
            span = timedelta(days=daily_bars * 2 if interval == "1d"
                             else intraday_days)
            bars = self._closed_only(
                await self.provider.get_bars(symbol, interval,
                                             self.now - span, self.now))
            self.history[(symbol, interval)] = _bars_to_frame(bars)
            if bars:
                self.staleness.record_quote(
                    symbol, self._quote_time(bars[-1]), interval)
                self.latest_price[symbol] = bars[-1].close
            log.info("backfill", symbol=symbol, interval=interval, bars=len(bars))

    # ── main loop ──────────────────────────────────────────────────────────
    async def run(self, max_cycles: int | None = None) -> None:
        await self.start()
        # persistence flushes synchronously inside each cycle; the background
        # tasks are the always-on supervisors
        self._tasks = [
            asyncio.create_task(self.kill_switch.watch()),
            asyncio.create_task(self.heartbeats.run()),
        ]

        try:
            while max_cycles is None or self._cycle < max_cycles:
                now = self.clock.tick()
                if now is None:
                    log.info("simulation_exhausted",
                             cycles=self._cycle, trades=len(self.portfolio.closed_trades))
                    await self.bus.publish(TOPIC_ALERT, {
                        "level": "warning",
                        "message": "simulated data exhausted — worker idle "
                                   "(staleness will block any further entries)",
                    })
                    break
                self.now = now
                await self._cycle_once()
                self._cycle += 1
                if self.poll_seconds > 0:
                    await asyncio.sleep(self.poll_seconds)
        finally:
            await self.shutdown()

    async def _cycle_once(self) -> None:
        await self.heartbeats.beat("worker")

        # cross-process rearm: operator removed the KILL file
        if self.kill_switch.active and not self.kill_switch.check_file():
            self.kill_switch.rearm()
            self.heartbeats.rearm()
            await self.bus.publish(TOPIC_ALERT, {
                "level": "warning", "message": "kill switch re-armed by operator",
            })

        self._roll_session_if_needed()
        self._toggles = load_strategy_toggles(self._toggle_path)

        new_bars = await self._fetch_new_bars()
        await self.staleness.check(
            now=self.now,
            has_open_positions=bool(self.portfolio.positions),
            flatten=self._flatten_all,
        )

        for slot in self.slots:
            for bar in new_bars.get((slot.symbol, slot.interval), []):
                await self._process_bar(slot, bar)

        marks = dict(self.latest_price)
        self.portfolio.snapshot_equity(self.now, marks)
        self._check_daily_circuit_breaker()

        if self._cycle % self._reconcile_every == 0:
            await self.execution.reconcile(list(self.portfolio.positions.values()))

        await self.persistence.flush(
            self.journal, self.portfolio,
            await self.broker.get_orders(), self.broker.fills)
        await self._publish_state()

    def _closed_only(self, bars: list[Bar]) -> list[Bar]:
        """Wall-clock providers stamp bars at their OPEN and return the
        still-forming bar; ingesting it would freeze a partial OHLC into
        history. Keep closed bars only. Sim bars are close-stamped — exempt."""
        if isinstance(self.provider, SimulatedDataProvider):
            return bars
        return [
            b for b in bars
            if b.timestamp + timedelta(seconds=interval_to_seconds(b.interval))
            <= self.now
        ]

    def _quote_time(self, bar: Bar) -> datetime:
        """Freshness reference for the StalenessMonitor: wall-clock bars are
        open-stamped, so their information time is the close; sim bars are
        close-stamped already."""
        if isinstance(self.provider, SimulatedDataProvider):
            return bar.timestamp
        return bar.timestamp + timedelta(
            seconds=interval_to_seconds(bar.interval))

    async def _fetch_new_bars(self) -> dict[tuple[str, str], list[Bar]]:
        out: dict[tuple[str, str], list[Bar]] = {}
        for (symbol, interval), df in self.history.items():
            # wall-clock fetch sequences can outlast heartbeat.timeout_seconds;
            # the worker is alive while it works through them, so keep beating
            await self.heartbeats.beat("worker")
            last = df.index[-1].to_pydatetime() if len(df) else self.now - timedelta(days=5)
            try:
                bars = await self.provider.get_bars(
                    symbol, interval, last + timedelta(seconds=1), self.now)
            except Exception as exc:
                log.error("bar_fetch_failed", symbol=symbol, interval=interval,
                          error=str(exc))
                await self.bus.publish(TOPIC_ALERT, {
                    "level": "error",
                    "message": f"data fetch failed for {symbol} {interval}: {exc}",
                })
                continue
            # a successful poll proves the feed is alive even when no bar has
            # closed yet (1h/1d bars); bar recency is the StalenessMonitor's job
            await self.heartbeats.beat("data")
            bars = self._closed_only(bars)
            if not bars:
                continue
            self.history[(symbol, interval)] = pd.concat(
                [df, _bars_to_frame(bars)]).sort_index()
            self.staleness.record_quote(
                symbol, self._quote_time(bars[-1]), interval)
            self.latest_price[symbol] = bars[-1].close
            out[(symbol, interval)] = bars
        return out

    # ── per-bar decision path (identical shape to backtester) ──────────────
    async def _process_bar(self, slot: StrategySlot, bar: Bar) -> None:
        now = self.now
        pit = {key: df[df.index <= pd.Timestamp(now)]
               for key, df in self.history.items()}
        marks = dict(self.latest_price)

        benchmark = slot.regime_benchmark or self.benchmark
        daily_bench = pit.get((benchmark, "1d"), pd.DataFrame())
        regime_state = (
            self.detector.classify(daily_bench, benchmark, as_of=now)
            if len(daily_bench) >= 60
            else RegimeState(benchmark, Regime.TRANSITION, now)
        )
        if benchmark == self.benchmark:
            # dashboard regime widget keeps tracking the global benchmark
            self._regime_state = regime_state

        # protective exits first: stop-loss / take-profit against this bar
        await self._check_stops(slot, bar, regime_state)

        # position context for the strategy (portfolio = source of truth)
        pos = self.portfolio.positions.get(slot.symbol)
        if pos is not None and pos.strategy_id == slot.strategy_id:
            self._bars_held[slot.symbol] = self._bars_held.get(slot.symbol, 0) + 1
        elif slot.symbol in self._bars_held and pos is None:
            del self._bars_held[slot.symbol]

        # runtime toggle: a disabled strategy generates no signals (no new
        # entries, no strategy exits) — protective stop/TP above still runs,
        # so disabling never orphans an open position
        if not self._toggles.get(slot.strategy_id, True):
            slot.last_processed = bar.timestamp
            return

        ctx = self._ctx
        ctx.now = now
        ctx.regime = regime_state.regime
        ctx.history = pit
        ctx.position_qty = {s: p.qty for s, p in self.portfolio.positions.items()}
        ctx.bars_held = dict(self._bars_held)

        slot.strategy.on_bar(bar)
        signal = slot.strategy.generate_signal()
        slot.last_processed = bar.timestamp
        if signal is None:
            return

        if signal.direction == "FLAT":
            await self._execute_exit(signal, regime_state=regime_state)
            return

        from backend.validation.context import ValidationContext
        vctx = ValidationContext(
            now=now, regime=regime_state, benchmark_symbol=benchmark,
            history=pit, strategy_config=slot.config,
            open_positions=list(self.portfolio.positions.values()),
            equity=self.portfolio.equity(marks),
        )
        validated = self.pipeline.validate(signal, vctx)
        if validated is None:
            await self._journal_rejection(signal)
            return
        self.journal.record("signal_validated", {
            "strategy_id": signal.strategy_id, "symbol": signal.symbol,
            "score": validated.score, "regime": validated.regime,
            "bar_time": signal.bar_time.isoformat(),
        })
        self._log_signal_row(signal, score=validated.score, validated=True)

        daily_sym = pit.get((slot.symbol, "1d"), pd.DataFrame())
        atr_period = int(self._risk_cfg.get("stops.atr_period", 14))
        atr_value = (float(ind.atr(daily_sym, atr_period).iloc[-1])
                     if len(daily_sym) >= atr_period + 6 else 0.0)

        decision = self.risk.evaluate(validated, self._account_state(marks),
                                      bar.close, atr_value)
        if not decision.approved or decision.order is None:
            self.journal.record("signal_rejected", {
                "bar_time": signal.bar_time.isoformat(), "phase": "risk",
                "stage": "risk_engine", "reason": decision.reason,
            })
            self._log_signal_row(signal, score=validated.score, validated=False,
                                 stage_failed="risk_engine", reason=decision.reason)
            return

        await self._submit_and_book(decision, fill_price_hint=bar.close)

    async def _check_stops(self, slot: StrategySlot, bar: Bar,
                           regime_state: RegimeState) -> None:
        pos = self.portfolio.positions.get(bar.symbol)
        if pos is None or pos.qty == 0:
            return
        is_long = pos.qty > 0
        exit_price: float | None = None
        reason = ""
        if pos.stop_loss is not None and (
                (is_long and bar.low <= pos.stop_loss)
                or (not is_long and bar.high >= pos.stop_loss)):
            exit_price, reason = pos.stop_loss, "stop_loss"
        elif pos.take_profit is not None and (
                (is_long and bar.high >= pos.take_profit)
                or (not is_long and bar.low <= pos.take_profit)):
            exit_price, reason = pos.take_profit, "take_profit"
        if exit_price is None:
            return

        signal = Signal(
            strategy_id=pos.strategy_id or slot.strategy_id, symbol=bar.symbol,
            direction="FLAT", confidence=1.0, bar_time=bar.timestamp,
            metadata={"reason": reason, "trigger_price": exit_price},
        )
        await self._execute_exit(signal, regime_state=regime_state,
                                 exit_price=exit_price)
        await self.notifier.send(
            f"{reason.replace('_', ' ')} hit: {bar.symbol} @ {exit_price:.2f}",
            level="warning" if reason == "stop_loss" else "info")

    async def _execute_exit(self, signal: Signal,
                            regime_state: RegimeState | None = None,
                            exit_price: float | None = None,
                            ignore_survival_gates: bool = False) -> None:
        """Exit path: skips the quality gauntlet (exits are survival actions)
        but still passes RiskEngine.evaluate() and the ExecutionManager."""
        regime = (regime_state or self._regime_state
                  or RegimeState(self.benchmark, Regime.TRANSITION, self.now))
        wrapper = ValidatedSignal(signal=signal, score=100.0, stage_results=[],
                                  regime=regime.regime.value, validated_at=self.now)
        account = self._account_state(dict(self.latest_price))
        if ignore_survival_gates:
            # emergency flatten runs while the kill switch is active by design
            account.kill_switch_active = False
            account.watchdog_halted = False
            account.data_stale = False
            account.stale_symbols = []
        decision = self.risk.evaluate(
            wrapper, account, self.latest_price.get(signal.symbol, 0.0) or 1.0,
            atr_value=1.0)
        if not decision.approved or decision.order is None:
            if decision.order is None and decision.approved:
                return  # nothing to exit (no open position)
            log.warning("exit_rejected", symbol=signal.symbol, reason=decision.reason)
            return
        await self._submit_and_book(decision, fill_price_hint=exit_price)

    async def _submit_and_book(self, decision, fill_price_hint: float | None) -> None:
        """Submit through the ExecutionManager and book resulting fills into
        the portfolio + journal. ``fill_price_hint`` pins the paper quote (a
        stop that triggered intrabar executes at the stop, not the close)."""
        symbol = decision.order.symbol
        previous = self.latest_price.get(symbol)
        if fill_price_hint is not None:
            self.latest_price[symbol] = fill_price_hint
        fills_before = len(self.broker.fills)
        try:
            ack = await self.execution.execute(decision)
        finally:
            if previous is not None:
                self.latest_price[symbol] = previous
        if ack is None:
            return
        for fill in self.broker.fills[fills_before:]:
            self.portfolio.apply_fill(fill, decision.order.strategy_id,
                                      decision.order.stop_loss,
                                      decision.order.take_profit)
            self.journal.record("fill", fill)
            self._track_closed_trades()
            await self.notifier.send(
                f"paper fill: {fill.side.value} {fill.qty:g} {fill.symbol} "
                f"@ {fill.price:.2f}")
        if decision.order.metadata.get("exit") or \
                decision.order.metadata.get("reason"):
            self._bars_held.pop(symbol, None)

    # ── accounting / guards ────────────────────────────────────────────────
    def _account_state(self, marks: dict[str, float]) -> AccountState:
        per_strategy: dict[str, int] = {}
        for p in self.portfolio.positions.values():
            if p.strategy_id:
                per_strategy[p.strategy_id] = per_strategy.get(p.strategy_id, 0) + 1
        return AccountState(
            equity=self.portfolio.equity(marks),
            equity_peak=self.portfolio.equity_peak,
            daily_pnl=self.portfolio.daily_pnl,
            open_positions=list(self.portfolio.positions.values()),
            open_positions_by_strategy=per_strategy,
            consecutive_losses_by_strategy=dict(self._consec_losses),
            cooldown_until_by_strategy=dict(self._cooldown_until),
            halted_today=self.halted_today,
            kill_switch_active=self.kill_switch.active,
            reconciliation_ok=not self.execution.blocked,
            watchdog_halted=self.heartbeats.trading_halted,
            # staleness blocks per symbol: weekend-stale equities must not
            # freeze 24/7 crypto entries (and vice versa)
            data_stale=False,
            stale_symbols=list(self.staleness.stale_symbols),
            now=self.now,
        )

    def _track_closed_trades(self) -> None:
        """Update consecutive-loss counters / cooldowns from new closed trades."""
        new = self.portfolio.closed_trades[self._seen_trades:]
        self._seen_trades = len(self.portfolio.closed_trades)
        limit = int(self._risk_cfg.get("cooldown.consecutive_losses", 4))
        pause = float(self._risk_cfg.get("cooldown.pause_minutes", 240))
        for trade in new:
            sid = trade.strategy_id
            self._consec_losses[sid] = 0 if trade.pnl > 0 \
                else self._consec_losses.get(sid, 0) + 1
            if self._consec_losses[sid] >= limit and sid not in self._cooldown_until:
                self._cooldown_until[sid] = self.now + timedelta(minutes=pause)
                self.journal.record("strategy_cooldown", {
                    "strategy_id": sid, "losses": self._consec_losses[sid],
                    "until": self._cooldown_until[sid].isoformat(),
                })

    def _roll_session_if_needed(self) -> None:
        date = self.now.date()
        if self._session_date is None:
            self._session_date = date
            return
        if date != self._session_date:
            self.journal.record("daily_summary", {
                "date": self._session_date.isoformat(),
                "daily_pnl": round(self.portfolio.daily_pnl, 2),
                "equity": round(self.portfolio.equity(dict(self.latest_price)), 2),
                "closed_trades": len(self.portfolio.closed_trades),
            })
            self.portfolio.reset_daily()
            self.halted_today = False
            # expired cooldowns unlock with a clean slate
            for sid, until in list(self._cooldown_until.items()):
                if self.now >= until:
                    del self._cooldown_until[sid]
                    self._consec_losses[sid] = 0
            self._session_date = date

    def _check_daily_circuit_breaker(self) -> None:
        limit_pct = float(self._risk_cfg.get("account.max_daily_loss_pct", 3.0))
        equity = self.portfolio.equity(dict(self.latest_price))
        if not self.halted_today and \
                self.portfolio.daily_pnl <= -(equity * limit_pct / 100.0):
            self.halted_today = True
            log.error("daily_circuit_breaker", daily_pnl=self.portfolio.daily_pnl)
            self.journal.record("circuit_breaker", {
                "daily_pnl": round(self.portfolio.daily_pnl, 2),
                "at": self.now.isoformat(),
            })

    # ── journaling / dashboard state ───────────────────────────────────────
    async def _journal_rejection(self, signal: Signal) -> None:
        failing = next(
            (r for r in reversed(self.funnel.records)
             if r["bar_time"] == signal.bar_time.isoformat() and not r["passed"]),
            None,
        )
        stage = failing["stage"] if failing else "unknown"
        reason = failing["reason"] if failing else "unknown"
        self.journal.record("signal_rejected", {
            "bar_time": signal.bar_time.isoformat(), "phase": "validation",
            "stage": stage, "reason": reason,
        })
        self._log_signal_row(signal, score=None, validated=False,
                             stage_failed=stage, reason=reason)
        if stage == "data_sanity":
            # MVP §8 stage 0: failure drops the signal AND fires a data alert
            await self.bus.publish(TOPIC_ALERT, {
                "level": "error",
                "source": "validation.data_sanity",
                "message": f"data-sanity failure on {signal.symbol}: {reason}",
                "at": self.now.isoformat(),
            })

    def _log_signal_row(self, signal: Signal, score: float | None,
                        validated: bool, stage_failed: str | None = None,
                        reason: str | None = None) -> None:
        row = {
            "strategy_id": signal.strategy_id, "symbol": signal.symbol,
            "direction": signal.direction, "score": score,
            "validated": validated, "bar_time": signal.bar_time.isoformat(),
        }
        if stage_failed:
            row["stage_failed"] = stage_failed
            row["reason"] = reason
        self._signal_log.append(row)
        cap = int(self._worker_cfg.get("worker.signal_log_cap", 200))
        del self._signal_log[:-cap]

    def _funnel_summary(self) -> list[dict]:
        stages = [s.name for s in self.pipeline._stages]
        counts = {s: {"stage": s, "passed": 0, "failed": 0} for s in stages}
        for rec in self.funnel.records:
            bucket = counts.get(rec["stage"])
            if bucket is not None:
                bucket["passed" if rec["passed"] else "failed"] += 1
        return [counts[s] for s in stages]

    def _strategy_rows(self) -> list[dict]:
        rows = []
        for cfg in {slot.strategy_id: slot.config for slot in self.slots}.values():
            sid = str(cfg["strategy_id"])
            trades = [t for t in self.portfolio.closed_trades if t.strategy_id == sid]
            wins = [t for t in trades if t.pnl > 0]
            enabled = self._toggles.get(sid, True)
            state = "active"
            if not enabled:
                state = "disabled"
            elif self.kill_switch.active or self.heartbeats.trading_halted:
                state = "paused"
            elif sid in self._cooldown_until and self.now < self._cooldown_until[sid]:
                state = "cooldown"
            rows.append({
                "strategy_id": sid, "state": state, "enabled": enabled,
                "regimes": list(cfg.get("allowed_regimes", [])),
                "trades": len(trades),
                "win_rate": round(len(wins) / len(trades), 3) if trades else 0.0,
                "expectancy": round(sum(t.pnl for t in trades) / len(trades), 2)
                if trades else 0.0,
            })
        return rows

    async def _publish_state(self) -> None:
        marks = dict(self.latest_price)
        regime = self._regime_state
        curve_cap = int(self._worker_cfg.get("worker.equity_curve_cap", 500))
        await self.state.set(KEY_REGIME, {
            "regime": regime.regime.value if regime else "TRANSITION",
            "metrics": regime.metrics if regime else {},
            "as_of": regime.as_of.isoformat() if regime else None,
        })
        await self.state.set(KEY_PORTFOLIO, {
            "equity": round(self.portfolio.equity(marks), 2),
            "cash": round(self.portfolio.cash, 2),
            "daily_pnl": round(self.portfolio.daily_pnl, 2),
            "positions": [
                {"symbol": p.symbol, "qty": p.qty,
                 "avg_entry_price": round(p.avg_entry_price, 4),
                 "unrealized_pnl": round(
                     (marks.get(p.symbol, p.avg_entry_price) - p.avg_entry_price)
                     * p.qty, 2),
                 "strategy_id": p.strategy_id}
                for p in self.portfolio.positions.values()
            ],
            "equity_curve": [
                [at.isoformat(), round(eq, 2)]
                for at, eq in self.portfolio.equity_curve[-curve_cap:]
            ],
        })
        await self.state.set(KEY_SIGNALS, self._signal_log)
        await self.state.set(KEY_FUNNEL, self.funnel.records[-200:])
        await self.state.set(KEY_FUNNEL_SUMMARY, self._funnel_summary())
        await self.state.set(KEY_STRATEGIES, self._strategy_rows())
        await self.state.set(KEY_ORDERS, [
            {"client_order_id": o.client_order_id, "symbol": o.symbol,
             "side": o.side.value, "qty": o.qty, "status": o.status.value,
             "avg_fill_price": o.avg_fill_price, "stop_loss": o.stop_loss,
             "take_profit": o.take_profit, "strategy_id": o.strategy_id}
            for o in (await self.broker.get_orders())[-100:]
        ])
        await self.state.set(KEY_ALERTS, self._alert_log)
        await self.state.set(KEY_WORKER, {
            "alive": True, "cycle": self._cycle, "clock": self.clock.mode,
            "sim_time": self.now.isoformat(),
            "provider": type(self.provider).__name__,
            "kill_switch_active": self.kill_switch.active,
            "trading_halted": (self.heartbeats.trading_halted
                               or self.kill_switch.active or self.halted_today),
            "entries_blocked_stale": self.staleness.entries_blocked,
            "reconciliation_blocked": self.execution.blocked,
            "persistence": self.persistence.available,
            "closed_trades": len(self.portfolio.closed_trades),
            "at": datetime.now(timezone.utc).isoformat(),
        })

    # ── shutdown ───────────────────────────────────────────────────────────
    async def shutdown(self) -> None:
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        self._tasks = []
        await self.persistence.stop(self.journal, self.portfolio,
                                    await self.broker.get_orders(),
                                    self.broker.fills)
        worker_state = await self.state.get(KEY_WORKER, {}) or {}
        worker_state["alive"] = False
        await self.state.set(KEY_WORKER, worker_state)
        await self.notifier.send("worker stopped")
        log.info("worker_stopped", cycles=self._cycle,
                 closed_trades=len(self.portfolio.closed_trades))


def _bars_to_frame(bars: list[Bar]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open": [b.open for b in bars],
            "high": [b.high for b in bars],
            "low": [b.low for b in bars],
            "close": [b.close for b in bars],
            "volume": [b.volume for b in bars],
        },
        index=pd.DatetimeIndex([b.timestamp for b in bars]),
    ).sort_index()


# --------------------------------------------------------------------------- #
# Entrypoint
# --------------------------------------------------------------------------- #
async def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="AlgoTrader paper-trading worker")
    parser.add_argument("--cycles", type=int, default=None,
                        help="stop after N cycles (default: run until interrupted)")
    parser.add_argument("--poll-seconds", type=float, default=None,
                        help="override worker.poll_seconds")
    args = parser.parse_args(argv)

    settings = get_settings()
    if settings.live_trading:
        # ASSUMPTION: the arming procedure (signed confirmation file + dashboard
        # acknowledgment, MVP §15) is Phase 2; until then live mode refuses to start.
        log.critical("live_trading_requested_but_arming_not_implemented — refusing")
        return

    state = await connect_state_store(settings)
    runtime = TradingRuntime(settings=settings, state=state,
                             poll_seconds=args.poll_seconds)
    try:
        await runtime.run(max_cycles=args.cycles)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
