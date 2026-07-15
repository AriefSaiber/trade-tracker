"""Runtime strategy toggles: the file the dashboard writes and the worker
polls each cycle (cross-process, like the KILL file)."""
from __future__ import annotations

import json
from pathlib import Path

from backend.worker import load_strategy_toggles


def test_missing_file_means_no_overrides(tmp_path: Path):
    assert load_strategy_toggles(tmp_path / "nope.json") == {}


def test_corrupt_file_fails_safe_to_no_overrides(tmp_path: Path):
    path = tmp_path / "toggles.json"
    path.write_text("{not json", encoding="utf-8")
    assert load_strategy_toggles(path) == {}


def test_non_dict_payload_ignored(tmp_path: Path):
    path = tmp_path / "toggles.json"
    path.write_text(json.dumps(["btc_trend_momentum"]), encoding="utf-8")
    assert load_strategy_toggles(path) == {}


def test_valid_toggles_load_with_bool_coercion(tmp_path: Path):
    path = tmp_path / "toggles.json"
    path.write_text(json.dumps({"btc_trend_momentum": False,
                                "trend_pullback": 1}), encoding="utf-8")
    assert load_strategy_toggles(path) == {
        "btc_trend_momentum": False, "trend_pullback": True}
