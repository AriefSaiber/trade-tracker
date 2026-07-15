"""Trade Journal: append-only record of every signal (validated or rejected),
order event, fill, and risk veto. This is the dataset powering the funnel
analytics, the Phase-2 meta-model, and the AI Analyst."""
from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any

import structlog

log = structlog.get_logger("journal")


def _serialize(obj: Any) -> Any:
    if is_dataclass(obj) and not isinstance(obj, type):
        return {k: _serialize(v) for k, v in asdict(obj).items()}
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_serialize(v) for v in obj]
    return obj


class TradeJournal:
    """In-memory buffer flushed to Postgres by the journal writer task.
    The same journal object is used by backtest (flushed to a results store)
    and live (flushed to the journal tables)."""

    def __init__(self) -> None:
        self.entries: list[dict] = []

    def record(self, kind: str, payload: Any) -> None:
        entry = {
            "at": datetime.now(timezone.utc).isoformat(),
            "kind": kind,
            "payload": _serialize(payload),
        }
        self.entries.append(entry)
        log.info("journal", kind=kind)

    def drain(self) -> list[dict]:
        out, self.entries = self.entries, []
        return out
