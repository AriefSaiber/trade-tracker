"""Alpaca broker adapter (REST + trade-updates websocket). Paper and live
differ ONLY by base/stream URL and credentials — same code path.

Idempotency has two layers:
  1. client_order_id is passed to Alpaca so the broker rejects duplicates (422).
  2. a client_order_id -> broker_order_id record is cached in Redis, so a retry
     (even across a process restart) returns the original ack without a second
     POST. Redis is the fast, local, authoritative dedupe; the 422 handling is
     the backstop when the cache is cold.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

import httpx
import structlog
import websockets
from tenacity import retry, stop_after_attempt, wait_exponential

from backend.core.config import Settings, load_yaml_config
from backend.core.events import (
    Order, OrderAck, OrderSide, OrderStatus, OrderType, Position,
)
from backend.execution.broker_adapter import BrokerAdapter

log = structlog.get_logger(__name__)

_STATUS_MAP = {
    "new": OrderStatus.SUBMITTED,
    "accepted": OrderStatus.SUBMITTED,
    "pending_new": OrderStatus.SUBMITTED,
    "partially_filled": OrderStatus.PARTIAL,
    "filled": OrderStatus.FILLED,
    "canceled": OrderStatus.CANCELLED,
    "rejected": OrderStatus.REJECTED,
    "expired": OrderStatus.EXPIRED,
}


class AlpacaAdapter(BrokerAdapter):
    def __init__(
        self,
        settings: Settings,
        redis_client: Any | None = None,
        *,
        config=None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        """`redis_client` and `transport` are injectable so tests can mock every
        Alpaca/Redis call. In production both are left None and built lazily."""
        cfg = config or load_yaml_config("broker")
        self._settings = settings
        # LIVE_TRADING=false => paper URL + paper keys. Arming live requires the
        # full procedure in MVP §15, checked upstream by the worker.
        self._base = (
            cfg.get("alpaca.live_base_url")
            if settings.live_trading
            else cfg.get("alpaca.paper_base_url")
        )
        self._stream_url = (
            cfg.get("alpaca.live_stream_url")
            if settings.live_trading
            else cfg.get("alpaca.paper_stream_url")
        )
        self._headers = {
            "APCA-API-KEY-ID": settings.alpaca_key_id,
            "APCA-API-SECRET-KEY": settings.alpaca_secret,
        }
        self._transport = transport
        self._redis_client = redis_client
        self._idem_prefix = cfg.get("idempotency.redis_key_prefix", "alpaca:idempotency:")
        self._idem_ttl = int(cfg.get("idempotency.ttl_seconds", 604800))

    # ── infra seams ────────────────────────────────────────────────────────
    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=15.0, transport=self._transport)

    async def _redis(self):
        if self._redis_client is None:
            import redis.asyncio as aioredis  # local import: optional at import time

            self._redis_client = aioredis.Redis(
                host=self._settings.redis_host,
                port=self._settings.redis_port,
                decode_responses=True,
            )
        return self._redis_client

    def _idem_key(self, client_order_id: str) -> str:
        return f"{self._idem_prefix}{client_order_id}"

    # ── order submission ───────────────────────────────────────────────────
    async def submit_order(self, order: Order) -> OrderAck:
        if order.stop_loss is None:
            raise ValueError("entry order without stop_loss — refusing to submit")

        redis = await self._redis()
        cached = await redis.get(self._idem_key(order.client_order_id))
        if cached is not None:
            rec = json.loads(cached)
            log.info("idempotent_hit_redis", client_order_id=order.client_order_id,
                     broker_order_id=rec["broker_order_id"])
            return OrderAck(order.client_order_id, rec["broker_order_id"],
                            OrderStatus(rec["status"]), datetime.now(timezone.utc))

        ack = await self._submit_rest(order)
        await redis.set(
            self._idem_key(order.client_order_id),
            json.dumps({"broker_order_id": ack.broker_order_id, "status": ack.status.value}),
            ex=self._idem_ttl,
        )
        return ack

    @retry(stop=stop_after_attempt(3),
           wait=wait_exponential(multiplier=1, max=10), reraise=True)
    async def _submit_rest(self, order: Order) -> OrderAck:
        payload: dict = {
            "symbol": order.symbol,
            "qty": str(order.qty),
            "side": "buy" if order.side == OrderSide.BUY else "sell",
            "type": order.order_type.value.lower(),
            "time_in_force": order.time_in_force,
            "client_order_id": order.client_order_id,   # idempotency at the broker
        }
        if order.order_type == OrderType.LIMIT and order.limit_price:
            payload["limit_price"] = str(order.limit_price)
        if not order.metadata.get("exit"):
            payload["order_class"] = "bracket"
            payload["stop_loss"] = {"stop_price": str(round(order.stop_loss, 2))}
            if order.take_profit:
                payload["take_profit"] = {"limit_price": str(round(order.take_profit, 2))}

        async with self._client() as client:
            resp = await client.post(f"{self._base}/v2/orders",
                                     json=payload, headers=self._headers)
            if resp.status_code == 422 and "client_order_id must be unique" in resp.text:
                # duplicate submit — fetch the existing order instead of erroring
                existing = await self._get_by_client_id(client, order.client_order_id)
                log.info("duplicate_submit_resolved", client_order_id=order.client_order_id)
                return existing
            resp.raise_for_status()
            data = resp.json()
        return OrderAck(
            client_order_id=order.client_order_id,
            broker_order_id=data["id"],
            status=_STATUS_MAP.get(data["status"], OrderStatus.SUBMITTED),
            at=datetime.now(timezone.utc),
        )

    async def _get_by_client_id(self, client: httpx.AsyncClient,
                                client_order_id: str) -> OrderAck:
        resp = await client.get(
            f"{self._base}/v2/orders:by_client_order_id",
            params={"client_order_id": client_order_id},
            headers=self._headers,
        )
        resp.raise_for_status()
        data = resp.json()
        return OrderAck(client_order_id, data["id"],
                        _STATUS_MAP.get(data["status"], OrderStatus.SUBMITTED),
                        datetime.now(timezone.utc))

    async def cancel_order(self, client_order_id: str) -> None:
        async with self._client() as client:
            ack = await self._get_by_client_id(client, client_order_id)
            resp = await client.delete(f"{self._base}/v2/orders/{ack.broker_order_id}",
                                       headers=self._headers)
            if resp.status_code not in (204, 404):
                resp.raise_for_status()

    async def get_positions(self) -> list[Position]:
        async with self._client() as client:
            resp = await client.get(f"{self._base}/v2/positions", headers=self._headers)
            resp.raise_for_status()
            return [
                Position(
                    symbol=p["symbol"],
                    qty=float(p["qty"]),
                    avg_entry_price=float(p["avg_entry_price"]),
                    unrealized_pnl=float(p.get("unrealized_pl", 0)),
                )
                for p in resp.json()
            ]

    async def get_orders(self, status: str | None = None) -> list[Order]:
        params = {"status": status or "all", "limit": 500}
        async with self._client() as client:
            resp = await client.get(f"{self._base}/v2/orders",
                                    params=params, headers=self._headers)
            resp.raise_for_status()
            out: list[Order] = []
            for o in resp.json():
                out.append(Order(
                    client_order_id=o.get("client_order_id", o["id"]),
                    strategy_id=o.get("client_order_id", "unknown"),
                    symbol=o["symbol"],
                    side=OrderSide.BUY if o["side"] == "buy" else OrderSide.SELL,
                    qty=float(o["qty"]),
                    order_type=OrderType(o["type"].upper()) if o["type"].upper() in
                    OrderType.__members__ else OrderType.MARKET,
                    limit_price=float(o["limit_price"]) if o.get("limit_price") else None,
                    stop_loss=0.0,
                    take_profit=None,
                    time_in_force=o["time_in_force"],
                    status=_STATUS_MAP.get(o["status"], OrderStatus.SUBMITTED),
                    broker_order_id=o["id"],
                    filled_qty=float(o.get("filled_qty") or 0),
                    avg_fill_price=float(o["filled_avg_price"]) if o.get("filled_avg_price") else None,
                ))
            return out

    # ── order-update websocket ─────────────────────────────────────────────
    async def stream_order_updates(
        self,
        on_update: Callable[[OrderAck], Awaitable[None]],
        *,
        connect: Callable[..., Any] | None = None,
        reconnect: bool = True,
    ) -> None:
        """Listen to Alpaca account `trade_updates` and dispatch each order-status
        change as an OrderAck. Reconnects with backoff (the watchdog sees any
        gap); `reconnect=False` runs one connection then returns (tests / clean
        shutdown). `connect` is injectable so tests supply a fake websocket."""
        connect = connect or websockets.connect
        backoff = 1.0
        while True:
            try:
                async with connect(self._stream_url) as ws:
                    await ws.send(json.dumps({
                        "action": "authenticate",
                        "data": {"key_id": self._settings.alpaca_key_id,
                                 "secret_key": self._settings.alpaca_secret},
                    }))
                    await ws.send(json.dumps({
                        "action": "listen", "data": {"streams": ["trade_updates"]},
                    }))
                    log.info("order_stream_connected")
                    backoff = 1.0
                    async for message in ws:
                        ack = self._parse_trade_update(message)
                        if ack is not None:
                            await on_update(ack)
            except Exception as exc:  # reconnect with backoff; watchdog sees staleness
                if not reconnect:
                    log.error("order_stream_ended", error=str(exc))
                    return
                log.error("order_stream_disconnected", error=str(exc), retry_in=backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60.0)
                continue
            if not reconnect:
                return

    def _parse_trade_update(self, message: str | bytes) -> OrderAck | None:
        try:
            msg = json.loads(message)
        except (TypeError, ValueError):
            return None
        if msg.get("stream") != "trade_updates":
            return None
        order = (msg.get("data") or {}).get("order") or {}
        if not order.get("id"):
            return None
        return OrderAck(
            client_order_id=order.get("client_order_id", ""),
            broker_order_id=order["id"],
            status=_STATUS_MAP.get(order.get("status"), OrderStatus.SUBMITTED),
            at=datetime.now(timezone.utc),
        )
