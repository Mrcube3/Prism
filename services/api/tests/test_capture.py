import gzip
from datetime import datetime, timezone

from prism.capture.collector import Collector
from prism.capture.session import phase_at, session_context
from prism.config import load_settings

STATES = {
    "stateList": [
        {"state": "pre_market", "startTime": "04:00", "endTime": "09:30"},
        {"state": "regular", "startTime": "09:30", "endTime": "16:00"},
        {"state": "after_hours", "startTime": "16:00", "endTime": "20:00"},
        {"state": "overnight", "startTime": "20:00", "endTime": "04:00"},
    ]
}
CALENDAR = {
    "regularConfig": ["SATURDAY", "SUNDAY"],
    "specificConfig": [{"startTime": "2026-09-06 20:00", "endTime": "2026-09-07 20:00"}],
}


def test_phase_handles_window_crossing_midnight():
    assert phase_at(datetime(2026, 10, 6, 23, 0), STATES["stateList"]) == "overnight"
    assert phase_at(datetime(2026, 10, 6, 3, 59), STATES["stateList"]) == "overnight"
    assert phase_at(datetime(2026, 10, 6, 4, 0), STATES["stateList"]) == "pre_market"
    assert phase_at(datetime(2026, 10, 6, 9, 30), STATES["stateList"]) == "regular"


def test_both_timezone_readings_are_recorded_during_daylight_time():
    # 2026-10-06 13:45 UTC is 09:45 New York (EDT) but 08:45 at fixed UTC-5.
    ctx = session_context(datetime(2026, 10, 6, 13, 45, tzinfo=timezone.utc), STATES, CALENDAR)
    assert ctx["ny_local"]["phase"] == "regular"
    assert ctx["fixed_utc_minus_5"]["phase"] == "pre_market"
    assert ctx["ny_local"]["closed_weekday"] is False


def test_calendar_closures_are_reported_not_inferred():
    ctx = session_context(datetime(2026, 9, 7, 15, 0, tzinfo=timezone.utc), STATES, CALENDAR)
    assert ctx["ny_local"]["in_specific_closure"] is True
    weekend = session_context(datetime(2026, 10, 10, 15, 0, tzinfo=timezone.utc), STATES, CALENDAR)
    assert weekend["ny_local"]["closed_weekday"] is True


def test_missing_session_data_stays_unknown():
    ctx = session_context(datetime(2026, 10, 6, 13, 45, tzinfo=timezone.utc), None, None)
    assert ctx["ny_local"]["phase"] is None
    assert "closed_weekday" not in ctx["ny_local"]


def test_compress_old_days_leaves_today(tmp_path):
    collector = Collector(load_settings(tmp_path / "none.env"), root=tmp_path)
    old = tmp_path / "capture" / "2000-01-01" / "tickers.jsonl"
    old.parent.mkdir(parents=True)
    old.write_text('{"a":1}\n', encoding="utf-8")
    collector._append("tickers", {"b": 2})
    collector.compress_old_days()
    assert not old.exists()
    assert gzip.open(f"{old}.gz", "rt", encoding="utf-8").read() == '{"a":1}\n'
    assert list((tmp_path / "capture").rglob("tickers.jsonl"))  # today's file untouched
