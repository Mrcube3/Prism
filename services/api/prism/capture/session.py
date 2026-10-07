"""US session context from Bitget's Reality states/calendar (AGENTS.md §4, §47).

Records the components, not a conclusion. Bitget labels clock times "EST" with
daylightType "standard" even during U.S. daylight time (OPEN_QUESTIONS Q-TZ), so
each phase is derived under two interpretations and both are stored:

- ny_local: clock times are America/New_York wall-clock time.
- fixed_utc_minus_5: clock times are fixed UTC-5.

No weekend-closure boundary is invented: we report whether the date is a
`regularConfig` closed weekday and whether we are inside a `specificConfig`
window, and leave "when does the weekend start" to the thaw experiment.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
FIXED_EST = timezone(timedelta(hours=-5))
INTERPRETATIONS = {"ny_local": NY, "fixed_utc_minus_5": FIXED_EST}


def _hhmm(value: str) -> time:
    hours, minutes = value.split(":")
    return time(int(hours), int(minutes))


def phase_at(local: datetime, state_list: list[dict[str, Any]]) -> str | None:
    """Return the state whose [start, end) window contains the local clock time."""
    clock = local.time().replace(second=0, microsecond=0)
    for state in state_list:
        start, end = _hhmm(state["startTime"]), _hhmm(state["endTime"])
        inside = start <= clock < end if start < end else (clock >= start or clock < end)
        if inside:
            return state["state"]
    return None


def in_specific_closure(local: datetime, calendar: dict[str, Any]) -> bool:
    naive = local.replace(tzinfo=None)
    for window in calendar.get("specificConfig") or []:
        start = datetime.strptime(window["startTime"], "%Y-%m-%d %H:%M")
        end = datetime.strptime(window["endTime"], "%Y-%m-%d %H:%M")
        if start <= naive < end:
            return True
    return False


def session_context(now: datetime, states: dict[str, Any] | None, calendar: dict[str, Any] | None) -> dict[str, Any]:
    out: dict[str, Any] = {"assumption_note": "Bitget clock times evaluated under two time-zone readings; see Q-TZ."}
    for name, tz in INTERPRETATIONS.items():
        local = now.astimezone(tz)
        entry: dict[str, Any] = {"local_time": local.isoformat()}
        entry["phase"] = phase_at(local, states.get("stateList") or []) if states else None
        if calendar:
            entry["closed_weekday"] = local.strftime("%A").upper() in (calendar.get("regularConfig") or [])
            entry["in_specific_closure"] = in_specific_closure(local, calendar)
        out[name] = entry
    return out
