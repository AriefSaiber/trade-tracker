"""FastAPI application: REST + WebSocket for the dashboard.

Binds behind Docker to 127.0.0.1 only. All read endpoints serve snapshots the
worker publishes to the shared StateStore (Redis when worker and API are
separate processes; in-memory when the worker runs embedded via
WORKER_EMBEDDED=true). The only mutating endpoints are kill-switch and re-arm,
which act through the KILL file — the cross-process trigger the worker's
KillSwitch polls (MVP §13).
"""
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import structlog
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from backend.core.config import get_settings, load_yaml_config
from backend.core.state import (
    KEY_ALERTS, KEY_FUNNEL, KEY_FUNNEL_SUMMARY, KEY_ORDERS, KEY_PORTFOLIO,
    KEY_REGIME, KEY_SIGNALS, KEY_STRATEGIES, KEY_WORKER,
    InMemoryStateStore, StateStore, connect_state_store,
)

log = structlog.get_logger(__name__)

_DEFAULT_PORTFOLIO = {
    "equity": 100_000.0, "cash": 100_000.0, "daily_pnl": 0.0,
    "positions": [], "equity_curve": [],
}

state_store: StateStore = InMemoryStateStore()
_worker_task: asyncio.Task | None = None


def _kill_file() -> Path:
    cfg = load_yaml_config("watchdog")
    return Path(str(cfg.get("kill_switch.file_path", "data/KILL")))


def _toggle_file() -> Path:
    cfg = load_yaml_config("worker")
    return Path(str(cfg.get("strategy_toggles.file_path",
                            "data/strategy_toggles.json")))


@asynccontextmanager
async def lifespan(app: FastAPI):
    global state_store, _worker_task
    settings = get_settings()
    if settings.worker_embedded:
        # single-process mode: worker + API share an in-memory state store
        from backend.worker import TradingRuntime
        state_store = InMemoryStateStore()
        runtime = TradingRuntime(settings=settings, state=state_store)
        _worker_task = asyncio.create_task(runtime.run())
        log.info("embedded_worker_started")
    else:
        state_store = await connect_state_store(settings)
    yield
    if _worker_task is not None:
        _worker_task.cancel()
        try:
            await _worker_task
        except (asyncio.CancelledError, Exception):
            pass
    await state_store.close()


app = FastAPI(title="AlgoTrader AI", version="0.2.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class KillSwitchRequest(BaseModel):
    flatten: bool = False
    acknowledgment: str   # dashboard requires typed acknowledgment


@app.get("/api/health")
async def health() -> dict:
    settings = get_settings()
    worker = await state_store.get(KEY_WORKER, {}) or {}
    return {
        "status": "ok",
        "live_trading": settings.live_trading,      # false unless fully armed
        "kill_switch_active": bool(worker.get("kill_switch_active",
                                              _kill_file().exists())),
        "trading_halted": bool(worker.get("trading_halted", False)),
        "worker_alive": bool(worker.get("alive", False)),
        "worker": worker,
        "at": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/regime")
async def regime() -> dict:
    return await state_store.get(
        KEY_REGIME, {"regime": "TRANSITION", "metrics": {}, "as_of": None})


@app.get("/api/portfolio")
async def portfolio() -> dict:
    return await state_store.get(KEY_PORTFOLIO, _DEFAULT_PORTFOLIO)


@app.get("/api/signals")
async def signals(limit: int = 50) -> list[dict]:
    rows = await state_store.get(KEY_SIGNALS, []) or []
    return rows[-limit:]


@app.get("/api/validation/funnel")
async def funnel(limit: int = 200) -> list[dict]:
    rows = await state_store.get(KEY_FUNNEL, []) or []
    return rows[-limit:]


@app.get("/api/validation/funnel/summary")
async def funnel_summary() -> list[dict]:
    return await state_store.get(KEY_FUNNEL_SUMMARY, []) or []


@app.get("/api/strategies")
async def strategies() -> list[dict]:
    return await state_store.get(KEY_STRATEGIES, []) or []


class StrategyToggleRequest(BaseModel):
    enabled: bool


@app.post("/api/strategies/{strategy_id}/toggle")
async def toggle_strategy(strategy_id: str, req: StrategyToggleRequest) -> dict:
    """Enable/disable one strategy at runtime. Writes the toggle file the
    worker polls each cycle (cross-process, like the KILL file). Disabling
    stops new signals only — protective stops on open positions keep running."""
    rows = await state_store.get(KEY_STRATEGIES, []) or []
    known = {str(r.get("strategy_id")) for r in rows}
    if known and strategy_id not in known:
        return {"ok": False, "error": f"unknown strategy_id {strategy_id!r}",
                "known": sorted(known)}

    path = _toggle_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    toggles: dict = {}
    if path.exists():
        try:
            toggles = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            toggles = {}
    toggles[strategy_id] = req.enabled
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(toggles, indent=2), encoding="utf-8")
    tmp.replace(path)   # atomic: the worker never reads a half-written file
    log.warning("strategy_toggled", strategy_id=strategy_id, enabled=req.enabled,
                source="dashboard")
    return {"ok": True, "strategy_id": strategy_id, "enabled": req.enabled,
            "toggles": toggles}


@app.get("/api/orders")
async def orders(limit: int = 100) -> list[dict]:
    rows = await state_store.get(KEY_ORDERS, []) or []
    return rows[-limit:]


@app.get("/api/alerts")
async def alerts(limit: int = 50) -> list[dict]:
    rows = await state_store.get(KEY_ALERTS, []) or []
    return rows[-limit:]


@app.post("/api/kill-switch")
async def kill_switch(req: KillSwitchRequest) -> dict:
    if req.acknowledgment.strip().upper() != "KILL":
        return {"ok": False, "error": "acknowledgment must be 'KILL'"}
    # The KILL file is the cross-process trigger: the worker's KillSwitch.watch
    # loop picks it up within a second and cancels orders / disarms live mode.
    path = _kill_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(exist_ok=True)
    log.critical("kill_switch_requested", source="dashboard", flatten=req.flatten)
    return {"ok": True, "kill_switch_active": True}


@app.post("/api/rearm")
async def rearm() -> dict:
    path = _kill_file()
    if path.exists():
        path.unlink()
    log.warning("rearm_requested", source="dashboard")
    return {"ok": True, "kill_switch_active": False}


@app.websocket("/ws/live")
async def ws_live(ws: WebSocket) -> None:
    """Pushes portfolio/regime/worker snapshots each second."""
    await ws.accept()
    try:
        while True:
            await ws.send_json({
                "portfolio": await state_store.get(KEY_PORTFOLIO, _DEFAULT_PORTFOLIO),
                "regime": await state_store.get(KEY_REGIME, {}),
                "worker": await state_store.get(KEY_WORKER, {}),
                "at": datetime.now(timezone.utc).isoformat(),
            })
            await asyncio.sleep(1.0)
    except WebSocketDisconnect:
        log.info("ws_disconnected")
