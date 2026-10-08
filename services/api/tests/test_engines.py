from datetime import datetime, timezone
from decimal import Decimal as D

import pytest

from prism.account import snapshot_from_bitget
from prism.collateral import CollateralSchedule, TierMode, tiered_collateral_value
from prism.provenance import DataMode, Provenance
from prism.reconciliation import ReconciliationMode, reconcile
from prism.shadow import Baseline, Factor, Holding, PerpPosition, RiskState, Scenario, run_frontier, run_shadow

# Shape copied from the verified public discount-rate payload (rNVDA class, truncated).
RNVDA = CollateralSchedule.from_bitget(
    {"coin": "rNVDA", "list": [
        {"tierStartValue": "0", "discountRate": "0.95"},
        {"tierStartValue": "500000", "discountRate": "0.94"},
        {"tierStartValue": "1000000", "discountRate": "0.93"},
        {"tierStartValue": "50000000", "discountRate": "0"},
    ]}, "test", "t")
USDT = CollateralSchedule.from_bitget({"coin": "USDT", "list": [{"tierStartValue": "0", "discountRate": "1"}, {"tierStartValue": "1000000000", "discountRate": "0"}]}, "test", "t")
PROV = Provenance(source="test", source_timestamp=None, retrieved_at=datetime.now(timezone.utc), data_mode=DataMode.HYPOTHETICAL)


# ---- tiers -------------------------------------------------------------------------------

def test_marginal_tiers_split_value_across_bands():
    assert tiered_collateral_value(D(600000), RNVDA, TierMode.MARGINAL) == D(500000) * D("0.95") + D(100000) * D("0.94")


def test_whole_tier_uses_single_rate():
    assert tiered_collateral_value(D(600000), RNVDA, TierMode.WHOLE) == D(600000) * D("0.94")


def test_zero_ratio_tail_adds_nothing():
    below = tiered_collateral_value(D(50_000_000), RNVDA, TierMode.MARGINAL)
    assert tiered_collateral_value(D(60_000_000), RNVDA, TierMode.MARGINAL) == below
    assert tiered_collateral_value(D(60_000_000), RNVDA, TierMode.WHOLE) == 0


def test_schedule_must_start_at_zero():
    with pytest.raises(ValueError):
        CollateralSchedule.from_bitget({"coin": "X", "list": [{"tierStartValue": "10", "discountRate": "1"}]}, "t", "t")


@pytest.mark.parametrize("mode", list(TierMode))
def test_collateral_value_is_monotonic(mode):
    values = [tiered_collateral_value(D(n), RNVDA, mode) for n in range(0, 2_000_000, 50_000)]
    if mode is TierMode.MARGINAL:
        assert values == sorted(values)


# ---- reconciliation ----------------------------------------------------------------------

def make_snapshot(assets, eff, mmr="0", ratio="0", positions=None, upnl="0", collateral=None):
    return snapshot_from_bitget(
        {"accountEquity": eff, "effEquity": eff, "mmr": mmr, "imr": "0", "mgnRatio": ratio, "unrealisedPnl": upnl, "assets": assets},
        {"list": positions or []}, PROV, "HYPOTHETICAL", collateral,
    )


def test_empty_account_is_no_exposure():
    result = reconcile(make_snapshot([], "0"), {})
    assert result.mode is ReconciliationMode.NO_EXPOSURE


def test_reconciles_when_a_hypothesis_matches():
    eff = str(D(10000) + D(600000) * D("0.94"))  # USDT + whole-tier rNVDA
    snap = make_snapshot([{"coin": "USDT", "usdValue": "10000"}, {"coin": "RNVDA", "usdValue": "600000"}], eff)
    result = reconcile(snap, {"USDT": USDT, "RNVDA": RNVDA})
    assert result.mode is ReconciliationMode.RECONCILED
    assert result.best_equity_hypothesis.tier_mode is TierMode.WHOLE


def test_mismatch_falls_back_to_observed_baseline_with_reason():
    snap = make_snapshot([{"coin": "USDT", "usdValue": "10000"}], "12000")
    result = reconcile(snap, {"USDT": USDT})
    assert result.mode is ReconciliationMode.OBSERVED_BASELINE
    assert any("0.50%" in r for r in result.reasons)


def test_custom_collateral_mode_excludes_unlisted_coins():
    snap = make_snapshot([{"coin": "USDT", "usdValue": "10000"}, {"coin": "RNVDA", "usdValue": "5000"}], "10000",
                         collateral={"collateralType": "custom", "collateralCoins": "USDT,USDC"})
    assert reconcile(snap, {"USDT": USDT, "RNVDA": RNVDA}).mode is ReconciliationMode.RECONCILED


def test_missing_position_fields_are_listed_not_guessed():
    snap = make_snapshot([], "100", positions=[{"symbol": "BTCUSDT"}])
    assert "positions[BTCUSDT].mark_price" in snap.unverified_fields


def test_demo_data_cannot_be_labelled_live():
    live = PROV.model_copy(update={"data_mode": DataMode.LIVE})
    with pytest.raises(ValueError):
        snapshot_from_bitget({"assets": []}, {"list": []}, live, "DEMO_PAPTRADING")


# ---- shadow ------------------------------------------------------------------------------

BASE = Baseline(effective_equity=D(40000), maintenance_margin=D(8000), label="HYPOTHETICAL ACCOUNT", reconciliation_mode="HYPOTHETICAL")
HOLD = [Holding("RNVDA", D(100), D(240), Factor.RTOKEN, True, RNVDA, "test"),
        Holding("USDT", D(20000), D(1), Factor.NONE, True, USDT, "test")]
POS = [PerpPosition("BTCUSDT", 1, D("0.5"), D(85000), Factor.CRYPTO, D("0.004"), "test")]


def test_identical_prices_mean_zero_deltas():
    r = run_shadow(BASE, HOLD, POS, Scenario())
    assert r.latent_collateral_gap == 0 and r.pnl_delta == 0
    assert r.shadow_effective_equity == BASE.effective_equity
    assert r.thaw_at_risk_pp == 0


def test_rtoken_shock_creates_negative_latent_gap():
    r = run_shadow(BASE, HOLD, POS, Scenario(rtoken_shock=D("-0.10")))
    assert r.latent_collateral_gap == D(100) * D(-24) * D("0.95")


def test_long_loses_on_crypto_drop_and_mm_scales():
    r = run_shadow(BASE, HOLD, POS, Scenario(crypto_shock=D("-0.10")))
    assert r.pnl_delta == D("0.5") * D(-8500)
    assert r.shadow_maintenance_margin == D(8000) + D("0.5") * D(-8500) * D("0.004")


def test_larger_negative_shock_cannot_raise_equity():
    eq = [run_shadow(BASE, HOLD, POS, Scenario(crypto_shock=s, rtoken_shock=s)).shadow_effective_equity for s in (D(0), D("-0.05"), D("-0.1"), D("-0.2"))]
    assert eq == sorted(eq, reverse=True)


def test_adding_stable_collateral_never_lowers_equity():
    more = HOLD + [Holding("USDC", D(5000), D(1), Factor.NONE, True, USDT, "test")]
    for s in (D("-0.2"), D(0)):
        assert run_shadow(BASE, more, POS, Scenario(rtoken_shock=s)).shadow_effective_equity >= run_shadow(BASE, HOLD, POS, Scenario(rtoken_shock=s)).shadow_effective_equity


def test_negative_equity_is_breach():
    r = run_shadow(Baseline(D(100), D(10), "H", "H"), [], POS, Scenario(crypto_shock=D("-0.5")))
    assert r.shadow_core_ratio is None and r.state is RiskState.BREACH


def test_frontier_blind_zone_only_when_current_state_is_acceptable():
    tight = Baseline(D(10000), D(5000), "H", "H")  # current core ratio 50%
    big = [PerpPosition("BTCUSDT", 1, D(1), D(85000), Factor.CRYPTO, D("0.004"), "t")]
    f = run_frontier(tight, HOLD, big)
    assert f.cell(D(0), D(0)).blind_zone is False
    assert f.cell(D("-0.2"), D("-0.2")).blind_zone is True
    entry = f.nearest_blind_zone_entry
    assert entry is not None and entry.crypto_shock < 0
    already_bad = Baseline(D(10000), D(9000), "H", "H")
    assert not any(c.blind_zone for c in run_frontier(already_bad, HOLD, big).cells)


# ---- repair / book walking ---------------------------------------------------------------

from prism.repair import walk  # noqa: E402


def test_book_walk_vwap_and_slippage():
    bids = [[D(100), D(1)], [D(99), D(2)]]
    asks = [[D(101), D(5)]]
    w = walk(bids, asks, "sell", D(2), "X", "t")
    assert w.filled == 2 and w.vwap == D("99.5") and w.mid == D("100.5")
    assert w.slippage_cost == D(2)  # (100.5 - 99.5) * 2
    assert not w.exhausted


def test_book_walk_reports_exhaustion():
    w = walk([[D(100), D(1)]], [[D(101), D(1)]], "sell", D(3), "X", "t")
    assert w.exhausted and w.filled == 1


# ---- tier-aware maintenance margin and stock-perp anchors -----------------------------------

from prism.shadow.engine import tier_rate  # noqa: E402

TIERS = ((D(0), D(150000), D("0.004")), (D(150000), D(900000), D("0.005")), (D(900000), D(12000000), D("0.01")))


def test_tier_rate_uses_the_tier_containing_the_whole_value():
    assert tier_rate(TIERS, D(100000)) == D("0.004")
    assert tier_rate(TIERS, D(150000)) == D("0.005")
    assert tier_rate(TIERS, D(20000000)) == D("0.01")


def test_crossing_a_tier_changes_the_rate_not_just_the_notional():
    base = Baseline(D(100000), D(560), "H", "H")
    pos = [PerpPosition("BTCUSDT", 1, D(2), D(70000), Factor.CRYPTO, D("0.004"), "t", mmr_tiers=TIERS)]  # 140k, tier 1
    up = run_shadow(base, [], pos, Scenario(crypto_shock=D("0.10")))  # 154k, tier 2
    assert up.shadow_maintenance_margin == D(560) + (D(154000) * D("0.005") - D(140000) * D("0.004"))
    assert not any("linearly" in w for w in up.warnings)
    linear = run_shadow(base, [], [replace_tiers(pos[0], ())], Scenario(crypto_shock=D("0.10")))
    assert any("CONSERVATIVE_APPROXIMATION" in w for w in linear.warnings)


def replace_tiers(p, tiers):
    from dataclasses import replace
    return replace(p, mmr_tiers=tiers)


def test_stock_perp_moves_only_the_remaining_gap_to_the_reopen_price():
    # Frozen reference 100; the perp already trades at 110 during the closure.
    base = Baseline(D(100000), D(0), "H", "H")
    perp = PerpPosition("NVDAUSDT", 1, D(10), D(110), Factor.RTOKEN, None, "t", anchor_price=D(100))
    flat = run_shadow(base, [], [perp], Scenario(rtoken_shock=D(0)))  # reopen at the reference: perp falls 110 -> 100
    assert flat.pnl_delta == D(10) * D(-10)
    up = run_shadow(base, [], [perp], Scenario(rtoken_shock=D("0.10")))  # reopen at 110: no further move
    assert up.pnl_delta == 0
    live = run_shadow(base, [], [PerpPosition("NVDAUSDT", 1, D(10), D(110), Factor.RTOKEN, None, "t")], Scenario(rtoken_shock=D("0.10")))
    assert live.pnl_delta == D(10) * D(11)  # without an anchor the shock applies from the mark
