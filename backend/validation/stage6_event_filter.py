"""Stage 6 — Event & time filters: session open/close blackouts, earnings
proximity, macro-event blackout dates."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from backend.core.events import Signal, StageResult
from backend.validation.base import ValidationStage
from backend.validation.context import ValidationContext


class EventFilterStage(ValidationStage):
    name = "event_filter"

    def __init__(self, config: dict, session_config: dict | None = None) -> None:
        super().__init__(config)
        session = session_config or {}
        self._tz = ZoneInfo(session.get("timezone", "America/New_York"))
        self._open = time.fromisoformat(session.get("open", "09:30"))
        self._close = time.fromisoformat(session.get("close", "16:00"))

    def validate(self, signal: Signal, context: ValidationContext) -> StageResult:
        if signal.direction == "FLAT":
            return self._skipped("exit signal")
        # 24/7 assets have no session opens/closes or earnings to black out
        if bool(self._override(context, "skip", False)):
            return self._skipped("skipped by strategy config")

        local = context.now.astimezone(self._tz)
        open_dt = datetime.combine(local.date(), self._open, tzinfo=self._tz)
        close_dt = datetime.combine(local.date(), self._close, tzinfo=self._tz)
        open_blackout = timedelta(minutes=int(self.config["session_open_blackout_minutes"]))
        close_blackout = timedelta(minutes=int(self.config["session_close_blackout_minutes"]))

        measured: dict = {"local_time": local.isoformat()}

        if open_dt <= local < open_dt + open_blackout:
            return StageResult(self.name, False, measured, "inside session-open blackout")
        if close_dt - close_blackout <= local <= close_dt:
            return StageResult(self.name, False, measured, "inside session-close blackout")

        # Earnings proximity
        blackout_days = int(self.config["earnings_blackout_days"])
        for iso in context.earnings_calendar.get(signal.symbol, []):
            earnings = date.fromisoformat(iso)
            days_until = (earnings - local.date()).days
            if 0 <= days_until <= blackout_days:
                measured["earnings_date"] = iso
                return StageResult(self.name, False, measured,
                                   f"earnings in {days_until} day(s)")

        # Macro blackout calendar
        macro = [date.fromisoformat(d) for d in self.config.get("macro_blackout_dates", [])]
        if local.date() in macro:
            measured["macro_blackout"] = local.date().isoformat()
            return StageResult(self.name, False, measured, "macro-event blackout date")

        return StageResult(self.name, True, measured, "ok")
