from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

import pytest

from prism.connectors.us_reference import sec, yahoo
from prism.reality.engine import MarketState, build_envelope
from tests.test_reality import base, ny

PAYLOAD = {"chart": {"result": [{
    "meta": {"symbol": "NVDA", "currency": "USD", "regularMarketPrice": 237.47, "regularMarketTime": 1791403200},
    "timestamp": [1791403140, 1791403200, 1791417540, 1791417600],
    "indicators": {"quote": [{"close": [237.4, 237.47, None, 237.55]}]},
}]}}


def test_yahoo_parse_drops_empty_minutes_and_finds_boundary_trade():
    data = yahoo.parse(PAYLOAD, "NVDA")
    assert len(data.points) == 3 and data.regular_price == D("237.47")
    at = data.at_or_before(datetime.fromtimestamp(1791417590, timezone.utc))
    assert at[1] == D("237.47")


def test_yahoo_parse_rejects_wrong_symbol():
    with pytest.raises(yahoo.UnderlyingUnavailable):
        yahoo.parse(PAYLOAD, "AAPL")


def test_sec_filing_description_names_items_and_skips_exhibits():
    f = sec.Filing("8-K", datetime(2026, 10, 5, 16, tzinfo=timezone.utc), ("2.02", "9.01"), "0001")
    assert f.description == "8-K: 2.02 Results of operations (earnings)"


def test_fresh_underlying_is_preferred_for_live_agreement():
    now = ny(2026, 10, 7, 17)
    e = build_envelope(base(now=now, live_ts=now, phase="after_hours", live_price=D("100.03"),
                            perp_now=D(101), underlying_last=D(100), underlying_last_ts=now - timedelta(minutes=2)))
    assert e.reference_agreement == "AGREE" and "underlying" in e.agreement_reference


def test_stale_underlying_falls_back_to_perp():
    now = ny(2026, 10, 7, 17)
    e = build_envelope(base(now=now, live_ts=now, phase="after_hours", live_price=D(100),
                            perp_now=D("100.1"), underlying_last=D(90), underlying_last_ts=now - timedelta(hours=2)))
    assert e.agreement_reference == "NVDAUSDT index" and e.reference_agreement == "AGREE"


def test_primary_event_corroborates_an_unreferenced_move():
    e = build_envelope(base(perp_now=None, perp_at_reference=None, perp_symbol=None, event_status="PRIMARY_EVENT"))
    assert e.market_state is MarketState.DISCOVERY and "EVENT_SUPPORTED" in e.reason_codes
    assert build_envelope(base(perp_now=None, perp_at_reference=None, perp_symbol=None)).market_state is MarketState.DRIFT
