"""AlpacaDataProvider endpoint routing: stocks vs crypto (BTC/USD-style
pairs hit /v1beta3/crypto/us/bars with no feed/adjustment params)."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import httpx

from backend.core.config import Settings
from backend.data.alpaca_provider import AlpacaDataProvider

START = datetime(2026, 7, 1, tzinfo=timezone.utc)
END = datetime(2026, 7, 10, tzinfo=timezone.utc)


def make_provider(handler) -> AlpacaDataProvider:
    settings = Settings(ALPACA_PAPER_KEY_ID="key", ALPACA_PAPER_SECRET="secret",
                        _env_file=None)
    return AlpacaDataProvider(settings, transport=httpx.MockTransport(handler))


def test_crypto_symbol_routes_to_crypto_endpoint():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={
            "bars": {"BTC/USD": [
                {"t": "2026-07-09T00:00:00Z", "o": 108000.0, "h": 109500.0,
                 "l": 107200.0, "c": 109100.0, "v": 42.7},
            ]},
            "next_page_token": None,
        })

    provider = make_provider(handler)
    bars = asyncio.run(provider.get_bars("BTC/USD", "1h", START, END))

    request = seen[0]
    assert request.url.path == "/v1beta3/crypto/us/bars"
    assert request.url.params["symbols"] == "BTC/USD"
    assert request.url.params["timeframe"] == "1Hour"
    assert "feed" not in request.url.params          # crypto has no feed
    assert "adjustment" not in request.url.params    # ...and no adjustment
    assert request.headers["APCA-API-KEY-ID"] == "key"

    assert len(bars) == 1
    assert bars[0].symbol == "BTC/USD"
    assert bars[0].close == 109100.0
    assert bars[0].volume == 42.7                    # fractional coin volume


def test_stock_symbol_keeps_stock_endpoint():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={
            "bars": [{"t": "2026-07-09T14:00:00Z", "o": 100.0, "h": 101.0,
                      "l": 99.5, "c": 100.5, "v": 1_000_000}],
            "next_page_token": None,
        })

    provider = make_provider(handler)
    bars = asyncio.run(provider.get_bars("SPY", "1h", START, END))

    request = seen[0]
    assert request.url.path == "/v2/stocks/SPY/bars"
    assert request.url.params["feed"] == "iex"
    assert request.url.params["adjustment"] == "all"
    assert len(bars) == 1 and bars[0].symbol == "SPY"


def test_crypto_pagination_follows_next_page_token():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            assert "page_token" not in request.url.params
            return httpx.Response(200, json={
                "bars": {"BTC/USD": [
                    {"t": "2026-07-08T00:00:00Z", "o": 1, "h": 2, "l": 0.5,
                     "c": 1.5, "v": 1.0}]},
                "next_page_token": "abc",
            })
        assert request.url.params["page_token"] == "abc"
        return httpx.Response(200, json={
            "bars": {"BTC/USD": [
                {"t": "2026-07-09T00:00:00Z", "o": 1, "h": 2, "l": 0.5,
                 "c": 1.8, "v": 2.0}]},
            "next_page_token": None,
        })

    provider = make_provider(handler)
    bars = asyncio.run(provider.get_bars("BTC/USD", "1d", START, END))
    assert calls["n"] == 2
    assert [b.close for b in bars] == [1.5, 1.8]
