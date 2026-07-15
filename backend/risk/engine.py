"""Risk Engine (MVP §10). Validation is about quality; risk is about survival.

Every order MUST pass RiskEngine.evaluate() — there is no code path from a
ValidatedSignal to the Execution Manager that skips it. An entry without a
stop is rejected. Fail flat on any uncertain state.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

import structlog

from backend.core.assets import is_crypto
from backend.core.config import YamlConfig, load_yaml_config
from backend.core.events import (
    Order,
    OrderSide,
    OrderType,
    Position,
    ValidatedSignal,
)
from backend.risk.sizing import fixed_fractional_size, volatility_stop

log = structlog.get_logger(__name__)


@dataclass
class RiskDecision:
    approved: bool
    reason: str
    order: Order | None = None
    measured: dict = field(default_factory=dict)


@dataclass
class AccountState:
    """Snapshot the Risk Engine evaluates against. Built from Portfolio +
    broker reconciliation; passing a snapshot keeps evaluate() deterministic."""

    equity: float
    equity_peak: float
    daily_pnl: float
    open_positions: list[Position]
    open_positions_by_strategy: dict[str, int]
    consecutive_losses_by_strategy: dict[str, int]
    cooldown_until_by_strategy: dict[str, datetime]
    halted_today: bool = False
    kill_switch_active: bool = False
    reconciliation_ok: bool = True
    watchdog_halted: bool = False   # heartbeat missed (MVP §13) — fail flat
    data_stale: bool = False        # global override: block ALL symbols
    # symbols whose quotes are older than 2x interval (MVP §13). Staleness is
    # per-symbol: weekend-stale equities must not block 24/7 crypto entries.
    stale_symbols: list[str] = field(default_factory=list)
    now: datetime | None = None


def make_client_order_id(strategy_id: str, symbol: str, bar_time: datetime) -> str:
    """Stable idempotency key: same (strategy, symbol, bar) can never create
    two orders. UUID5 over a deterministic name."""
    name = f"{strategy_id}:{symbol}:{bar_time.isoformat()}"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, name))


class RiskEngine:
    def __init__(self, config: YamlConfig | None = None) -> None:
        self._cfg = config or load_yaml_config("risk")

    def evaluate(
        self,
        validated: ValidatedSignal,
        account: AccountState,
        entry_price: float,
        atr_value: float,
    ) -> RiskDecision:
        signal = validated.signal
        now = account.now or datetime.now(timezone.utc)
        cfg = self._cfg

        def reject(reason: str, **measured: object) -> RiskDecision:
            log.info("risk_rejected", strategy_id=signal.strategy_id,
                     symbol=signal.symbol, reason=reason, **measured)
            return RiskDecision(False, reason, None, dict(measured))

        # ── survival gates ────────────────────────────────────────────────
        if account.kill_switch_active:
            return reject("kill switch active")
        if account.watchdog_halted:
            return reject("watchdog halt — heartbeat missed, new entries blocked")
        if account.data_stale:
            return reject("stale market data — new entries blocked")
        if signal.symbol in account.stale_symbols:
            return reject("stale market data for symbol — blocked")
        if not account.reconciliation_ok:
            return reject("broker reconciliation mismatch — new entries blocked")
        if account.halted_today:
            return reject("daily circuit breaker tripped")

        max_daily_loss = account.equity * float(cfg.get("account.max_daily_loss_pct")) / 100
        if account.daily_pnl <= -max_daily_loss:
            return reject("max daily loss reached", daily_pnl=account.daily_pnl,
                          limit=-max_daily_loss)

        if account.equity_peak > 0:
            drawdown_pct = (account.equity_peak - account.equity) / account.equity_peak * 100
            if drawdown_pct >= float(cfg.get("account.max_drawdown_pct")):
                return reject("max drawdown from peak — manual re-arm required",
                              drawdown_pct=round(drawdown_pct, 2))

        if signal.direction == "FLAT":
            # exits are always allowed through to execution
            return RiskDecision(True, "exit approved", self._exit_order(validated, account, now))

        # ── session gate: new entries only during regular trading hours ──
        # (exits above are exempt — never blocked by the session clock;
        #  crypto trades 24/7 and is exempted by config)
        crypto = is_crypto(signal.symbol)
        session_exempt = crypto and bool(cfg.get("trading_hours.crypto_exempt", True))
        if bool(cfg.get("trading_hours.enforce", True)) and not session_exempt:
            tz = ZoneInfo(str(cfg.get("trading_hours.timezone", "America/New_York")))
            local = now.astimezone(tz)
            session_start = time.fromisoformat(
                str(cfg.get("trading_hours.session_start", "09:30")))
            session_end = time.fromisoformat(
                str(cfg.get("trading_hours.session_end", "16:00")))
            if local.weekday() >= 5 or not (session_start <= local.time() < session_end):
                return reject("outside trading hours — entries blocked",
                              now_local=local.isoformat())

        # ── per-strategy gates ────────────────────────────────────────────
        cooldown_until = account.cooldown_until_by_strategy.get(signal.strategy_id)
        if cooldown_until and now < cooldown_until:
            return reject("strategy in consecutive-loss cooldown",
                          until=cooldown_until.isoformat())
        losses = account.consecutive_losses_by_strategy.get(signal.strategy_id, 0)
        if losses >= int(cfg.get("cooldown.consecutive_losses")):
            return reject("consecutive-loss limit reached", losses=losses)

        max_global = int(cfg.get("position.max_open_positions_global"))
        if len(account.open_positions) >= max_global:
            return reject("max open positions (global)", open=len(account.open_positions))
        per_strategy = account.open_positions_by_strategy.get(signal.strategy_id, 0)
        if per_strategy >= int(cfg.get("position.max_open_positions_per_strategy")):
            return reject("max open positions (strategy)", open=per_strategy)

        # ── sizing with mandatory volatility stop ─────────────────────────
        if atr_value <= 0 or entry_price <= 0:
            return reject("invalid ATR/price for stop computation — fail flat",
                          atr=atr_value, entry=entry_price)

        is_long = signal.direction == "LONG"
        stop = volatility_stop(entry_price, atr_value,
                               float(cfg.get("stops.atr_multiplier")), is_long)
        sized = fixed_fractional_size(
            equity=account.equity,
            risk_per_trade_pct=float(
                validated.signal.metadata.get("risk_per_trade_pct")
                or cfg.get("position.risk_per_trade_pct")
            ),
            entry=entry_price,
            stop_price=stop,
            max_position_pct=float(cfg.get("position.max_position_pct")),
            is_long=is_long,
            take_profit_r_multiple=float(
                validated.signal.metadata.get("take_profit_r_multiple")
                or cfg.get("stops.take_profit_r_multiple")
            ),
            fractional=crypto,
        )
        if sized.shares <= 0:
            return reject("position size rounded to zero", stop=stop)
        if crypto:
            notional = sized.shares * entry_price
            min_notional = float(cfg.get("position.min_notional_usd", 10.0))
            if notional < min_notional:
                return reject("below minimum order notional",
                              notional=round(notional, 2), min_notional=min_notional)

        # ── portfolio heat re-verified here (defense in depth vs Stage 7) ─
        heat = sized.risk_amount + sum(
            abs(p.avg_entry_price - p.stop_loss) * abs(p.qty)
            for p in account.open_positions
            if p.stop_loss is not None
        )
        max_heat = account.equity * float(cfg.get("portfolio.max_heat_pct")) / 100
        if heat > max_heat:
            return reject("portfolio heat cap (risk engine)", heat=round(heat, 2),
                          max_heat=round(max_heat, 2))

        order = Order(
            client_order_id=make_client_order_id(
                signal.strategy_id, signal.symbol, signal.bar_time
            ),
            strategy_id=signal.strategy_id,
            symbol=signal.symbol,
            side=OrderSide.BUY if is_long else OrderSide.SELL,
            qty=float(sized.shares),
            order_type=OrderType.MARKET,
            limit_price=None,
            stop_loss=sized.stop_price,       # every order carries a stop
            take_profit=sized.take_profit,
            time_in_force="gtc" if crypto else "day",   # Alpaca crypto rejects "day"
            created_at=now,
            metadata={"validation_score": validated.score, "regime": validated.regime},
        )
        log.info("risk_approved", strategy_id=signal.strategy_id, symbol=signal.symbol,
                 qty=order.qty, stop=order.stop_loss, tp=order.take_profit)
        return RiskDecision(True, "approved", order,
                            {"risk_amount": sized.risk_amount, "heat": heat})

    def _exit_order(self, validated: ValidatedSignal, account: AccountState,
                    now: datetime) -> Order | None:
        signal = validated.signal
        pos = next((p for p in account.open_positions if p.symbol == signal.symbol), None)
        if pos is None or pos.qty == 0:
            return None
        return Order(
            client_order_id=make_client_order_id(
                signal.strategy_id, signal.symbol, signal.bar_time
            ),
            strategy_id=signal.strategy_id,
            symbol=signal.symbol,
            side=OrderSide.SELL if pos.qty > 0 else OrderSide.BUY,
            qty=abs(pos.qty),
            order_type=OrderType.MARKET,
            limit_price=None,
            stop_loss=pos.stop_loss or 0.0,
            take_profit=None,
            time_in_force="gtc" if is_crypto(signal.symbol) else "day",
            created_at=now,
            metadata={"exit": True},
        )
