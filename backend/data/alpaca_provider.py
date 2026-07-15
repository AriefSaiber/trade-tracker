"""Alpaca market-data provider (REST bars + websocket live bars).

Speaks both asset classes: stock bars from /v2/stocks/{symbol}/bars and
crypto bars from /v1beta3/crypto/us/bars (slash pairs like BTC/USD). Crypto
has no feed parameter, no adjustment, and returns bars keyed by symbol.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Awaitable, Callable

import httpx
import structlog
import websockets

from backend.core.assets import is_crypto
from backend.core.config import Settings, load_yaml_config
from backend.core.events import Bar
from backend.data.provider import DataProvider

log = structlog.get_logger(__name__)

_INTERVAL_MAP = {
    "1m": "1Min",
    "5m": "5Min",
    "15m": "15Min",
    "1h": "1Hour",
    "1d": "1Day",
}


class AlpacaDataProvider(DataProvider):
    def __init__(self, settings: Settings,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._settings = settings
        broker_cfg = load_yaml_config("broker")
        self._data_url: str = broker_cfg.get("alpaca.data_url", "https://data.alpaca.markets")
        self._feed: str = str(broker_cfg.get("alpaca.data_feed", "iex"))
        self._crypto_path: str = str(
            broker_cfg.get("alpaca.crypto_bars_path", "/v1beta3/crypto/us/bars"))
        self._crypto_stream_url: str = str(broker_cfg.get(
            "alpaca.crypto_stream_url",
            "wss://stream.data.alpaca.markets/v1beta3/crypto/us"))
        self._transport = transport   # test injection; None => real network
        self._headers = {
            "APCA-API-KEY-ID": settings.alpaca_key_id,
            "APCA-API-SECRET-KEY": settings.alpaca_secret,
        }

    async def get_bars(
        self, symbol: str, interval: str, start: datetime, end: datetime
    ) -> list[Bar]:
        timeframe = _INTERVAL_MAP.get(interval)
        if timeframe is None:
            raise ValueError(f"Unsupported interval: {interval}")

        crypto = is_crypto(symbol)
        url = (f"{self._data_url}{self._crypto_path}" if crypto
               else f"{self._data_url}/v2/stocks/{symbol}/bars")

        bars: list[Bar] = []
        page_token: str | None = None
        # 10s cap: a hung request must not eat the worker's 30s heartbeat window
        async with httpx.AsyncClient(timeout=10.0, transport=self._transport) as client:
            while True:
                params: dict = {
                    "timeframe": timeframe,
                    "start": start.astimezone(timezone.utc).isoformat(),
                    "end": end.astimezone(timezone.utc).isoformat(),
                    "limit": 10_000,
                }
                if crypto:
                    # crypto endpoint is multi-symbol; no feed / adjustment params
                    params["symbols"] = symbol
                else:
                    params["adjustment"] = "all"
                    params["feed"] = self._feed
                if page_token:
                    params["page_token"] = page_token
                resp = await client.get(url, params=params, headers=self._headers)
                resp.raise_for_status()
                payload = resp.json()
                raw_bars = ((payload.get("bars") or {}).get(symbol) if crypto
                            else payload.get("bars")) or []
                for raw in raw_bars:
                    bars.append(
                        Bar(
                            symbol=symbol,
                            interval=interval,
                            timestamp=datetime.fromisoformat(raw["t"].replace("Z", "+00:00")),
                            open=float(raw["o"]),
                            high=float(raw["h"]),
                            low=float(raw["l"]),
                            close=float(raw["c"]),
                            volume=float(raw["v"]),
                        )
                    )
                page_token = payload.get("next_page_token")
                if not page_token:
                    break
        log.info("bars_fetched", symbol=symbol, interval=interval, count=len(bars))
        return bars

    async def subscribe_live(
        self, symbols: list[str], callback: Callable[[Bar], Awaitable[None]]
    ) -> None:
        stocks = [s for s in symbols if not is_crypto(s)]
        cryptos = [s for s in symbols if is_crypto(s)]
        streams = []
        if stocks:
            streams.append(self._stream_loop(
                f"wss://stream.data.alpaca.markets/v2/{self._feed}", stocks, callback))
        if cryptos:
            streams.append(self._stream_loop(self._crypto_stream_url, cryptos, callback))
        if streams:
            await asyncio.gather(*streams)

    async def _stream_loop(
        self, url: str, symbols: list[str],
        callback: Callable[[Bar], Awaitable[None]],
    ) -> None:
        backoff = 1.0
        while True:
            try:
                async with websockets.connect(url) as ws:
                    await ws.send(json.dumps({
                        "action": "auth",
                        "key": self._settings.alpaca_key_id,
                        "secret": self._settings.alpaca_secret,
                    }))
                    await ws.send(json.dumps({"action": "subscribe", "bars": symbols}))
                    log.info("live_stream_connected", url=url, symbols=symbols)
                    backoff = 1.0
                    async for message in ws:
                        for item in json.loads(message):
                            if item.get("T") != "b":
                                continue
                            await callback(
                                Bar(
                                    symbol=item["S"],
                                    interval="1m",
                                    timestamp=datetime.fromisoformat(
                                        item["t"].replace("Z", "+00:00")
                                    ),
                                    open=float(item["o"]),
                                    high=float(item["h"]),
                                    low=float(item["l"]),
                                    close=float(item["c"]),
                                    volume=float(item["v"]),
                                )
                            )
            except Exception as exc:  # reconnect with backoff; watchdog sees staleness
                log.error("live_stream_disconnected", url=url, error=str(exc),
                          retry_in=backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60.0)
