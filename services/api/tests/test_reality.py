from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

from prism.reality.engine import (
    NY,
    BookStats,
    EvidenceMode,
    MarketState,
    Quality,
    RealityInputs,
    Regime,
    book_stats,
    build_envelope,
    classify_quality,
    last_extended_close,
    regime_at,
)

CAL = {"regularConfig": ["SATURDAY", "SUNDAY"], "specificConfig": [{"startTime": "2026-09-06 20:00", "endTime": "2026-09-07 20:00"}]}


def ny(y, m, d, h, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=NY).astimezone(timezone.utc)


def test_regime_follows_session_and_calendar():
    assert regime_at(ny(2026, 10, 7, 12), "regular", CAL) is Regime.LIVE_REFERENCE
    assert regime_at(ny(2026, 10, 7, 22), "overnight", CAL) is Regime.FROZEN_REFERENCE
    assert regime_at(ny(2026, 10, 10, 12), "regular", CAL) is Regime.FROZEN_REFERENCE  # Saturday
    assert regime_at(ny(2026, 9, 7, 12), "regular", CAL) is Regime.FROZEN_REFERENCE  # holiday window


def test_extended_close_skips_weekends_and_holidays():
    assert last_extended_close(ny(2026, 10, 11, 15), CAL) == datetime(2026, 10, 9, 20, tzinfo=NY)  # Sunday -> Friday
    assert last_extended_close(ny(2026, 10, 7, 21), CAL) == datetime(2026, 10, 7, 20, tzinfo=NY)
    assert last_extended_close(ny(2026, 10, 7, 3), CAL) == datetime(2026, 10, 6, 20, tzinfo=NY)
    assert last_extended_close(ny(2026, 9, 7, 22), CAL) == datetime(2026, 9, 4, 20, tzinfo=NY)  # Labor Day closed


def test_book_stats_spread_and_depth():
    s = book_stats([[100, 2], [99, 10]], [[100.2, 1], [102, 5]])
    assert s.spread_bps == D("0.2") / D("100.1") * 10000
    assert s.depth_usd_50bps == D(200) + D("100.2")  # 99 and 102 are outside +/-50 bps


def test_quality_needs_own_history_and_ranks_against_it():
    stats = BookStats(D(100), D("100.1"), D(10), D(50000))
    q, reasons, _ = classify_quality(stats, [D(5)] * 10, [D(1)] * 10)
    assert q is Quality.UNAVAILABLE and reasons[0].startswith("HISTORY_SHORT")
    spreads, depths = [D(x % 20) for x in range(600)], [D(x * 100) for x in range(600)]
    assert classify_quality(BookStats(D(1), D(1), D(2), D(59000)), spreads, depths)[0] is Quality.HIGH
    assert classify_quality(BookStats(D(1), D(1), D(19), D(30000)), spreads, depths)[0] is Quality.LOW


def base(**kw):
    now = ny(2026, 10, 10, 12)  # Saturday: frozen
    hist_s, hist_d = [D(x % 20) for x in range(600)], [D(x * 100) for x in range(600)]
    inputs = RealityInputs(
        symbol="RNVDAUSDT", underlying="NVDA", weekend_tradable=True, now=now, phase="regular", calendar=CAL,
        live_price=D(110), live_ts=now - timedelta(seconds=30), reference_price=D(100), reference_time=ny(2026, 10, 9, 20),
        perp_symbol="NVDAUSDT", perp_now=D(220), perp_at_reference=D(200), book=BookStats(D(1), D(1), D(2), D(59000)),
        spread_history=hist_s, depth_history=hist_d, closure_windows_captured=1, event_status="UNAVAILABLE",
    )
    return replace(inputs, **kw)


def test_corroborated_liquid_move_is_discovery_and_observed():
    e = build_envelope(base())
    assert e.regime is Regime.FROZEN_REFERENCE and e.move == D("0.1") and e.perp_move == D("0.1")
    assert e.reference_agreement == "AGREE" and e.market_state is MarketState.DISCOVERY and e.evidence_mode is EvidenceMode.OBSERVED
    assert e.lower_stress_bound is None and "BAND_UNAVAILABLE_UNCALIBRATED" in e.reason_codes


def test_thin_book_overrides():
    e = build_envelope(base(book=BookStats(D(1), D(1), D(19), D(1000))))
    assert e.market_quality is Quality.LOW and e.market_state is MarketState.THIN and e.evidence_mode is EvidenceMode.HYBRID


def test_conflict_and_overshoot():
    assert build_envelope(base(perp_now=D(160))).market_state is MarketState.CONFLICT  # perp −20%, rToken +10%
    over = build_envelope(base(perp_now=D(208), live_price=D("104.1")))  # perp +4%, rToken +4.1%: agree
    assert over.market_state is MarketState.DISCOVERY
    assert build_envelope(base(perp_now=D(204), live_price=D("103.5"))).market_state is MarketState.OVERSHOOT  # +3.5% vs +2%


def test_live_regime_has_no_closure_move():
    e = build_envelope(base(now=ny(2026, 10, 7, 12), live_ts=ny(2026, 10, 7, 12), phase="regular", perp_now=D(110)))
    assert e.regime is Regime.LIVE_REFERENCE and e.move is None and e.market_state is MarketState.NOT_APPLICABLE
    assert e.reference_agreement == "AGREE"  # live level comparison, same scale


def test_stale_or_untradable_market_is_inferred():
    stale = build_envelope(base(live_ts=ny(2026, 10, 10, 11)))
    assert stale.evidence_mode is EvidenceMode.INFERRED and stale.market_state is MarketState.INSUFFICIENT_DATA
    assert build_envelope(base(weekend_tradable=False)).evidence_mode is EvidenceMode.INFERRED


def test_scale_mismatch_is_not_compared():
    e = build_envelope(base(now=ny(2026, 10, 7, 12), live_ts=ny(2026, 10, 7, 12), phase="regular", perp_now=D(220)))
    assert e.reference_agreement == "NORMALIZATION_UNVERIFIED"


def test_calibrated_band_wraps_the_centre_only_while_frozen():
    e = build_envelope(base(band_q05=D("-0.004"), band_q95=D("0.003"), band_source="POOLED_442_WINDOWS"))
    assert e.lower_stress_bound == e.reality_center - D("0.004") and e.upper_stress_bound == e.reality_center + D("0.003")
    assert "BAND_POOLED_442_WINDOWS" in e.reason_codes
    live = build_envelope(base(now=ny(2026, 10, 7, 12), live_ts=ny(2026, 10, 7, 12), phase="regular", band_q05=D("-0.004"), band_q95=D("0.003")))
    assert live.lower_stress_bound is None and "BAND_NOT_NEEDED_REFERENCE_LIVE" in live.reason_codes
    assert build_envelope(base()).lower_stress_bound is None  # uncalibrated: no band is invented
