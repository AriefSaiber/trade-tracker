#!/usr/bin/env bash
# Watchdog drills (MVP §13 / Promotion Gate B). Thin wrapper — the drills
# themselves live in watchdog_drill.py so they run identically on Windows.
set -e
cd "$(dirname "$0")/.."
exec python scripts/watchdog_drill.py "$@"
