"""Typed configuration (CLAUDE.md §6): env via Pydantic Settings, thresholds
via configs/*.yaml. No module reads os.environ or opens YAML directly."""
from __future__ import annotations

import pytest

from backend.core.config import (
    CONFIG_DIR,
    Settings,
    YamlConfig,
    get_settings,
    load_yaml_config,
)


def test_paper_mode_is_the_default():
    """LIVE_TRADING must default to false everywhere (hard rule)."""
    assert Settings().live_trading is False


def test_settings_selects_paper_credentials_by_default(monkeypatch):
    monkeypatch.setenv("ALPACA_PAPER_KEY_ID", "paper-key")
    monkeypatch.setenv("ALPACA_LIVE_KEY_ID", "live-key")
    monkeypatch.setenv("ALPACA_PAPER_SECRET", "paper-secret")
    monkeypatch.setenv("ALPACA_LIVE_SECRET", "live-secret")
    monkeypatch.delenv("LIVE_TRADING", raising=False)

    s = Settings()
    assert s.live_trading is False
    assert s.alpaca_key_id == "paper-key"
    assert s.alpaca_secret == "paper-secret"


def test_settings_selects_live_credentials_when_armed(monkeypatch):
    monkeypatch.setenv("LIVE_TRADING", "true")
    monkeypatch.setenv("ALPACA_PAPER_KEY_ID", "paper-key")
    monkeypatch.setenv("ALPACA_LIVE_KEY_ID", "live-key")
    monkeypatch.setenv("ALPACA_LIVE_SECRET", "live-secret")

    s = Settings()
    assert s.live_trading is True
    assert s.alpaca_key_id == "live-key"
    assert s.alpaca_secret == "live-secret"


def test_database_url_is_async_postgres(monkeypatch):
    monkeypatch.setenv("POSTGRES_USER", "u")
    monkeypatch.setenv("POSTGRES_PASSWORD", "p")
    monkeypatch.setenv("POSTGRES_HOST", "db")
    monkeypatch.setenv("POSTGRES_PORT", "5433")
    monkeypatch.setenv("POSTGRES_DB", "algo")

    s = Settings()
    assert s.database_url == "postgresql+asyncpg://u:p@db:5433/algo"


def test_ai_analyst_disabled_by_default():
    assert Settings().ai_analyst_enabled is False


def test_get_settings_is_cached():
    assert get_settings() is get_settings()


def test_yaml_config_dotted_get():
    cfg = YamlConfig(name="t", data={"a": {"b": {"c": 42}}})
    assert cfg.get("a.b.c") == 42
    assert cfg.get("a.b") == {"c": 42}
    assert cfg.get("a.b.missing") is None
    assert cfg.get("a.b.missing", default=7) == 7
    assert cfg.get("nope.also", default="x") == "x"


@pytest.mark.parametrize("name", ["market", "broker", "risk", "validation",
                                  "watchdog", "worker"])
def test_all_shipped_configs_load_and_are_non_empty(name):
    cfg = load_yaml_config(name)
    assert isinstance(cfg, YamlConfig)
    assert cfg.name == name
    assert cfg.data, f"{name}.yaml loaded empty"
    assert (CONFIG_DIR / f"{name}.yaml").exists()


def test_risk_config_documents_defaults():
    risk = load_yaml_config("risk")
    assert risk.get("account.max_daily_loss_pct") == 3.0
    assert risk.get("account.max_leverage") == 1.0          # cash only
    assert risk.get("position.risk_per_trade_pct") == 0.75
    assert risk.get("stops.method") == "atr"                # volatility-based


def test_broker_config_defaults_to_paper():
    broker = load_yaml_config("broker")
    assert broker.get("paper_mode") is True


def test_validation_config_lists_stages_zero_through_seven():
    val = load_yaml_config("validation")
    stages = val.get("pipeline.stages")
    assert stages == [
        "data_sanity",
        "regime_gate",
        "mtf_alignment",
        "volume_confirmation",
        "volatility_band",
        "confluence_score",
        "event_filter",
        "portfolio_correlation",
    ]
    assert val.get("confluence_score.threshold") == 70
    assert val.get("ml_gate.enabled") is False   # Phase 2, disabled


def test_regime_thresholds_live_in_market_yaml():
    # Regime thresholds have exactly one home: the `regime:` section of
    # market.yaml (the RegimeDetector's actual config source). The former
    # standalone regime.yaml was an unread duplicate and was removed.
    market = load_yaml_config("market")
    assert market.get("regime.adx_trend_min") == 25
    assert market.get("regime.ema_fast") == 50
    assert market.get("regime.ema_slow") == 200
