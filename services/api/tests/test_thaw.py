import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

spec = importlib.util.spec_from_file_location("capture_thaw", Path(__file__).resolve().parents[3] / "scripts" / "capture_thaw.py")
ct = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ct)


def rows(prices, start=datetime(2026, 10, 12, 7, 58, tzinfo=timezone.utc)):
    return [{"status": "OBSERVED", "symbol": "RNVDAUSDT", "at": (start + timedelta(seconds=10 * i)).isoformat(),
             "implied_recognized_price": str(p)} for i, p in enumerate(prices)]


def test_thaw_is_the_first_change_after_flat_readings():
    found = ct.transitions(rows([100, 100, 100, 100, 100.5, 100.7, 100.6]))
    (thaw,) = [t for t in found if t["event"] == "THAW"]
    assert thaw["first_change_at"] == (datetime(2026, 10, 12, 7, 58, tzinfo=timezone.utc) + timedelta(seconds=40)).isoformat()


def test_freeze_is_detected_when_moves_stop():
    found = ct.transitions(rows([100, 100.2, 100.4, 100.4, 100.4, 100.4]))
    assert [t["event"] for t in found] == ["FREEZE"]


def test_no_holdings_produces_no_transitions():
    assert ct.transitions([{"status": "NO_RTOKEN_HOLDINGS", "at": "2026-10-12T08:00:00+00:00"}]) == []
