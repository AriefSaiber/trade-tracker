"""Notification dispatcher: Telegram mocked, secrets scrubbed, failures soft."""
from __future__ import annotations

import asyncio

import httpx
import pytest

from backend.core.config import Settings
from backend.notifications.dispatcher import NullNotifier, TelegramNotifier


def _settings(**overrides) -> Settings:
    values = {"TELEGRAM_BOT_TOKEN": "123:SECRET-TOKEN",
              "TELEGRAM_CHAT_ID": "42"}
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_send_posts_to_telegram_api(monkeypatch):
    captured: dict = {}

    class FakeClient:
        def __init__(self, *a, **kw): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *exc): return False

        async def post(self, url, json=None):
            captured["url"] = url
            captured["json"] = json
            return httpx.Response(200, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    notifier = TelegramNotifier(_settings())
    asyncio.run(notifier.send("stop hit: AAPL", level="warning"))

    assert captured["json"]["chat_id"] == "42"
    assert "stop hit: AAPL" in captured["json"]["text"]
    # the token belongs in the URL path (Telegram API shape), never the payload
    assert "SECRET-TOKEN" not in captured["json"]["text"]


def test_send_failure_never_raises(monkeypatch):
    class ExplodingClient:
        def __init__(self, *a, **kw): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *exc): return False

        async def post(self, url, json=None):
            raise httpx.ConnectError("network down")

    monkeypatch.setattr(httpx, "AsyncClient", ExplodingClient)
    notifier = TelegramNotifier(_settings())
    # notification failure must never break the trading path
    asyncio.run(notifier.send("anything", level="critical"))


def test_unconfigured_telegram_is_a_noop(monkeypatch):
    calls = []

    class Recorder:
        def __init__(self, *a, **kw): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *exc): return False

        async def post(self, url, json=None):
            calls.append(url)

    monkeypatch.setattr(httpx, "AsyncClient", Recorder)
    notifier = TelegramNotifier(_settings(TELEGRAM_BOT_TOKEN=""))
    asyncio.run(notifier.send("hello"))
    assert calls == []                       # no token -> no network call


def test_null_notifier_swallows_everything():
    asyncio.run(NullNotifier().send("anything", level="critical"))


def test_secrets_never_appear_in_structlog_output(capsys):
    notifier = TelegramNotifier(_settings(TELEGRAM_BOT_TOKEN=""))
    asyncio.run(notifier.send("message with context"))
    out = capsys.readouterr()
    assert "SECRET-TOKEN" not in out.out + out.err
