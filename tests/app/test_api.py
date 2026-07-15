"""Dashboard API: endpoints serve state-store snapshots; kill switch acts
through the KILL file (the cross-process trigger the worker polls)."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import backend.app.main as app_module
from backend.core.state import (
    KEY_FUNNEL_SUMMARY, KEY_PORTFOLIO, KEY_STRATEGIES, KEY_WORKER,
    InMemoryStateStore,
)

KILL_FILE = Path("data/KILL")
TOGGLE_FILE = Path("data/strategy_toggles.json")


@pytest.fixture()
def client():
    # Bypass lifespan (which would try Redis / embedded worker): install a
    # fresh in-memory store and exercise the routes directly.
    app_module.state_store = InMemoryStateStore()
    KILL_FILE.unlink(missing_ok=True)
    TOGGLE_FILE.unlink(missing_ok=True)
    with TestClient(app_module.app) as c:
        yield c
    KILL_FILE.unlink(missing_ok=True)
    TOGGLE_FILE.unlink(missing_ok=True)


def _seed(key, value):
    import asyncio

    asyncio.run(app_module.state_store.set(key, value))


def test_health_reports_paper_mode(client):
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["live_trading"] is False
    assert body["kill_switch_active"] is False


def test_portfolio_defaults_before_worker_populates(client):
    body = client.get("/api/portfolio").json()
    assert body["equity"] == 100_000.0
    assert body["positions"] == []


def test_portfolio_serves_worker_snapshot(client):
    _seed(KEY_PORTFOLIO, {"equity": 101_234.5, "cash": 90_000.0,
                          "daily_pnl": 12.3, "positions": [], "equity_curve": []})
    assert client.get("/api/portfolio").json()["equity"] == 101_234.5


def test_funnel_summary_endpoint_exists(client):
    # the dashboard fetches /api/validation/funnel/summary — this 404'd before
    _seed(KEY_FUNNEL_SUMMARY, [{"stage": "data_sanity", "passed": 3, "failed": 1}])
    body = client.get("/api/validation/funnel/summary").json()
    assert body == [{"stage": "data_sanity", "passed": 3, "failed": 1}]


def test_kill_switch_requires_typed_acknowledgment(client):
    body = client.post("/api/kill-switch",
                       json={"flatten": False, "acknowledgment": "yes"}).json()
    assert body["ok"] is False
    assert not KILL_FILE.exists()


def test_kill_switch_creates_kill_file_and_rearm_removes_it(client):
    body = client.post("/api/kill-switch",
                       json={"flatten": False, "acknowledgment": "KILL"}).json()
    assert body["ok"] is True
    assert KILL_FILE.exists()          # the worker's KillSwitch.watch sees this

    health = client.get("/api/health").json()
    assert health["kill_switch_active"] is True

    assert client.post("/api/rearm").json()["ok"] is True
    assert not KILL_FILE.exists()


def test_strategy_toggle_writes_file_worker_polls(client):
    import json

    _seed(KEY_STRATEGIES, [{"strategy_id": "btc_trend_momentum",
                            "state": "active", "enabled": True}])
    body = client.post("/api/strategies/btc_trend_momentum/toggle",
                       json={"enabled": False}).json()
    assert body["ok"] is True
    assert json.loads(TOGGLE_FILE.read_text(encoding="utf-8")) == {
        "btc_trend_momentum": False}

    # re-enabling merges rather than clobbering other entries
    body = client.post("/api/strategies/btc_trend_momentum/toggle",
                       json={"enabled": True}).json()
    assert body["ok"] is True
    assert json.loads(TOGGLE_FILE.read_text(encoding="utf-8")) == {
        "btc_trend_momentum": True}


def test_strategy_toggle_rejects_unknown_id(client):
    _seed(KEY_STRATEGIES, [{"strategy_id": "trend_pullback"}])
    body = client.post("/api/strategies/no_such_strategy/toggle",
                       json={"enabled": False}).json()
    assert body["ok"] is False
    assert not TOGGLE_FILE.exists()


def test_health_reflects_worker_heartbeat_state(client):
    _seed(KEY_WORKER, {"alive": True, "kill_switch_active": False,
                       "trading_halted": True})
    body = client.get("/api/health").json()
    assert body["worker_alive"] is True
    assert body["trading_halted"] is True
