"""Kill switch: file / dashboard / Telegram triggers cancel orders + disarm."""
import asyncio

from backend.core.config import YamlConfig
from backend.core.event_bus import TOPIC_ALERT, EventBus
from backend.watchdog.kill_switch import KillSwitch


def cfg(tmp_path, flatten=False) -> YamlConfig:
    return YamlConfig(name="watchdog", data={"kill_switch": {
        "file_path": str(tmp_path / "KILL"),
        "flatten_on_trigger": flatten,
    }})


def alert_recorder(bus: EventBus) -> list:
    alerts: list = []

    async def on_alert(payload):
        alerts.append(payload)

    bus.subscribe(TOPIC_ALERT, on_alert)
    return alerts


def make_switch(tmp_path, bus=None, flatten=False):
    cancelled = {"n": 0}
    flattened = {"n": 0}

    async def cancel_orders():
        cancelled["n"] += 1

    async def flatten_all():
        flattened["n"] += 1

    ks = KillSwitch(bus=bus, cancel_orders=cancel_orders, flatten=flatten_all,
                    config=cfg(tmp_path, flatten))
    return ks, cancelled, flattened


def test_dashboard_trigger_cancels_and_disarms(tmp_path):
    bus = EventBus()
    alerts = alert_recorder(bus)
    ks, cancelled, flattened = make_switch(tmp_path, bus)

    asyncio.run(ks.trigger_from_dashboard())

    assert ks.active is True
    assert ks.live_armed is False
    assert cancelled["n"] == 1
    assert flattened["n"] == 0            # flatten off by default
    assert ks.file_path.exists()          # trip persisted for restart
    assert len(alerts) == 1
    assert alerts[0]["source"] == "kill_switch.dashboard"
    assert alerts[0]["level"] == "critical"


def test_telegram_trigger_provenance(tmp_path):
    bus = EventBus()
    alerts = alert_recorder(bus)
    ks, cancelled, _ = make_switch(tmp_path, bus)

    asyncio.run(ks.trigger_from_telegram())

    assert cancelled["n"] == 1
    assert alerts[0]["source"] == "kill_switch.telegram"


def test_file_trigger_via_watch_loop(tmp_path):
    ks, cancelled, _ = make_switch(tmp_path)
    # operator drops the KILL file
    ks.file_path.parent.mkdir(parents=True, exist_ok=True)
    ks.file_path.touch()

    asyncio.run(ks.watch(iterations=1, interval_seconds=0.0))

    assert ks.active is True
    assert ks.live_armed is False
    assert cancelled["n"] == 1


def test_flatten_when_configured(tmp_path):
    ks, cancelled, flattened = make_switch(tmp_path, flatten=True)
    asyncio.run(ks.trigger("dashboard"))
    assert cancelled["n"] == 1
    assert flattened["n"] == 1


def test_rearm_removes_file_but_keeps_live_disarmed(tmp_path):
    ks, _, _ = make_switch(tmp_path)
    asyncio.run(ks.trigger("file"))
    assert ks.file_path.exists()
    assert ks.live_armed is False

    ks.rearm()
    assert ks.active is False
    assert not ks.file_path.exists()
    # re-arm clears the halt but must NOT re-enable live (MVP §15)
    assert ks.live_armed is False


def test_watch_does_not_retrigger_while_active(tmp_path):
    ks, cancelled, _ = make_switch(tmp_path)
    ks.file_path.parent.mkdir(parents=True, exist_ok=True)
    ks.file_path.touch()
    asyncio.run(ks.watch(iterations=3, interval_seconds=0.0))
    assert cancelled["n"] == 1   # triggered once, not three times
