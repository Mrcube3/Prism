"""Repair Solver and execution feasibility (AGENTS.md §37–§40, M8). Advisory only: nothing is placed.

Each candidate is recomputed through the shadow engine under the same stress scenario. Execution
cost is estimated by walking Bitget's public order book (rTokens: public `market/orderbook`, not
the whitelisted Reality book, Q-DEPTH). Trading fees are unknown for this account (fee-rate
unavailable) and are excluded and flagged; never silently set to zero and called exact.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal

from .collateral import CollateralSchedule, TierMode, tiered_collateral_value
from .connectors.bitget import BitgetClient
from .fees import FeeRate, fee_rate
from .shadow import Baseline, Factor, Holding, PerpPosition, Scenario, ShadowResult, run_shadow

VERSION = "prism-repair-v0.1"
FEE_NOTE = "Execution cost = order-book slippage + taker fee (fee source shown per candidate; excluded only where unavailable)."


@dataclass(frozen=True)
class BookWalk:
    symbol: str
    side: str  # "sell" walks bids, "buy" walks asks
    requested: Decimal
    filled: Decimal
    vwap: Decimal | None
    mid: Decimal | None
    slippage_cost: Decimal  # vs mid, in quote (USD)
    slippage_bps: Decimal | None
    levels_used: int
    exhausted: bool
    source: str
    fee_cost: Decimal = Decimal(0)
    fee_source: str = "UNAVAILABLE"

    @property
    def total_cost(self) -> Decimal:
        return self.slippage_cost + self.fee_cost


def fetch_book(client: BitgetClient, category: str, symbol: str, demo_env: bool = False) -> tuple[list[list[Decimal]], list[list[Decimal]], str]:
    response = client.get("/api/v3/market/orderbook", {"category": category, "symbol": symbol, "limit": "50"}, demo_env=demo_env)
    if not response.ok or not isinstance(response.data, dict):
        return [], [], f"unavailable: {response.error or response.msg}"
    conv = lambda rows: [[Decimal(str(p)), Decimal(str(q))] for p, q in rows]  # noqa: E731
    return conv(response.data.get("b") or []), conv(response.data.get("a") or []), f"bitget:{response.endpoint} ts={response.data.get('ts')}"


def walk(bids: list[list[Decimal]], asks: list[list[Decimal]], side: str, qty: Decimal, symbol: str, source: str,
         fee: "FeeRate | None" = None) -> BookWalk:
    levels = bids if side == "sell" else asks
    mid = (bids[0][0] + asks[0][0]) / 2 if bids and asks else None
    filled = notional = Decimal(0)
    used = 0
    for price, size in levels:
        if filled >= qty:
            break
        take = min(size, qty - filled)
        filled += take
        notional += take * price
        used += 1
    vwap = notional / filled if filled > 0 else None
    cost = abs(mid * filled - notional) if mid is not None and filled > 0 else Decimal(0)
    bps = (cost / (mid * filled) * 10000) if mid and filled > 0 else None
    fee_cost = notional * fee.taker if fee is not None and fee.known else Decimal(0)
    return BookWalk(symbol, side, qty, filled, vwap, mid, cost, bps, used, filled < qty, source,
                    fee_cost, fee.source if fee is not None else "UNAVAILABLE")


@dataclass(frozen=True)
class RepairCandidate:
    action: str
    detail: str
    cash_required: Decimal
    exposure_change_pct: Decimal
    execution_cost: Decimal | None
    slippage_bps: Decimal | None
    shadow_after: ShadowResult | None
    feasible: bool
    reason: str
    liquidity: str  # HIGH / MEDIUM / LOW / UNAVAILABLE / N/A


def _liquidity(walked: BookWalk | None) -> str:
    if walked is None:
        return "N/A"
    if walked.exhausted or walked.slippage_bps is None:
        return "UNAVAILABLE" if walked.filled == 0 else "LOW"
    # Display buckets only (not used in any calculation); thresholds to be learned per asset (§24).
    return "HIGH" if walked.slippage_bps < 10 else "MEDIUM" if walked.slippage_bps < 50 else "LOW"


def _ratio_ok(result: ShadowResult, target: Decimal) -> bool:
    return result.shadow_core_ratio is not None and result.shadow_core_ratio < target


def _search(fn, target: Decimal, steps: int = 30) -> Decimal | None:
    """Smallest fraction f in (0, 1] with fn(f) under target (fn assumed monotone in f)."""
    if not _ratio_ok(fn(Decimal(1)), target):
        return None
    lo, hi = Decimal(0), Decimal(1)
    for _ in range(steps):
        mid = (lo + hi) / 2
        lo, hi = (lo, mid) if _ratio_ok(fn(mid), target) else (mid, hi)
    return hi


def solve(
    client: BitgetClient,
    baseline: Baseline,
    holdings: list[Holding],
    positions: list[PerpPosition],
    scenario: Scenario,
    target: Decimal,
    stable_schedule: CollateralSchedule | None,
    tier_mode: TierMode = TierMode.MARGINAL,
    demo_env: bool = False,
) -> tuple[ShadowResult, list[RepairCandidate], list[str]]:
    notes = [FEE_NOTE, "A dedicated hedge is not modelled yet. In one-way mode an opposite perp nets against the position (equivalent to a reduction); in hedge mode it would add a separate position."]
    before = run_shadow(baseline, holdings, positions, scenario, tier_mode)
    if _ratio_ok(before, target):
        return before, [], notes + ["Account already meets the target under this scenario; no repair needed."]
    candidates: list[RepairCandidate] = []
    stable_rate = tiered_collateral_value(Decimal(1), stable_schedule, tier_mode) if stable_schedule else Decimal(1)

    def with_stable(base: Baseline, result: ShadowResult) -> Decimal:
        # ratio = MM / (Eq + X·rate) < target  =>  X > (MM/target − Eq) / rate
        need = (result.shadow_maintenance_margin / target - result.shadow_effective_equity) / stable_rate
        return max(need, Decimal(0)) * Decimal("1.0001")

    # 1. Add stable collateral (closed form; USDT carries no shock).
    x = with_stable(baseline, before)
    after = run_shadow(replace(baseline, effective_equity=baseline.effective_equity + x * stable_rate), holdings, positions, scenario, tier_mode)
    candidates.append(RepairCandidate("ADD_STABLE", f"Deposit {x:,.2f} USDT", x, Decimal(0), Decimal(0), None, after, True, "No execution needed.", "N/A"))

    # 2. Reduce each perp position.
    for i, p in enumerate(positions):
        bids, asks, source = fetch_book(client, "USDT-FUTURES", p.symbol, demo_env)
        side = "sell" if p.direction > 0 else "buy"
        perp_fee = fee_rate(client, p.symbol)

        def reduced(f: Decimal, i=i, p=p, bids=bids, asks=asks, side=side, source=source, perp_fee=perp_fee) -> ShadowResult:
            w = walk(bids, asks, side, p.size * f, p.symbol, source, perp_fee)
            mm_cut = p.size * f * p.mark_price * (p.mmr_rate or Decimal(0))
            new_base = replace(baseline, effective_equity=baseline.effective_equity - w.total_cost,
                               maintenance_margin=max(baseline.maintenance_margin - mm_cut, Decimal(0)))
            new_pos = positions[:i] + [replace(p, size=p.size * (1 - f))] + positions[i + 1:]
            return run_shadow(new_base, holdings, new_pos, scenario, tier_mode)

        f = _search(reduced, target)
        if f is None:
            candidates.append(RepairCandidate("REDUCE_PERP", f"Reduce {p.symbol}", Decimal(0), Decimal(100), None, None, None, False,
                                              "Closing the whole position does not reach the target.", "N/A"))
            continue
        w = walk(bids, asks, side, p.size * f, p.symbol, source, perp_fee)
        candidates.append(RepairCandidate(
            "REDUCE_PERP", f"Reduce {p.symbol} {'long' if p.direction > 0 else 'short'} by {f:.1%} ({p.size * f:.4f})",
            Decimal(0), f * 100, w.total_cost, w.slippage_bps, reduced(f), not w.exhausted,
            "Visible book too thin for this size." if w.exhausted else f"Book walk over {w.levels_used} levels ({source}); fee {w.fee_source}.", _liquidity(w)))

    # 3. Sell closure-sensitive collateral (rTokens) into the public book; proceeds become USDT.
    for i, h in enumerate(holdings):
        if h.factor is not Factor.RTOKEN or not h.collateral_enabled or h.schedule is None:
            continue
        bids, asks, source = fetch_book(client, "SPOT", f"{h.coin}USDT", demo_env)
        spot_fee = fee_rate(client, f"{h.coin}USDT", "SPOT")

        def sold(f: Decimal, i=i, h=h, bids=bids, asks=asks, source=source, spot_fee=spot_fee) -> ShadowResult:
            w = walk(bids, asks, "sell", h.quantity * f, h.coin, source, spot_fee)
            proceeds = (w.vwap or Decimal(0)) * w.filled - w.fee_cost
            keep = h.quantity - w.filled
            eq_change = (tiered_collateral_value(keep * h.price, h.schedule, tier_mode)
                         - tiered_collateral_value(h.quantity * h.price, h.schedule, tier_mode) + proceeds * stable_rate)
            new_base = replace(baseline, effective_equity=baseline.effective_equity + eq_change)
            new_hold = holdings[:i] + [replace(h, quantity=keep)] + holdings[i + 1:]
            return run_shadow(new_base, new_hold, positions, scenario, tier_mode)

        f = _search(sold, target)
        if f is None:
            candidates.append(RepairCandidate("SELL_RTOKEN", f"Sell {h.coin}", Decimal(0), Decimal(0), None, None, None, False,
                                              "Selling the entire holding does not reach the target.", "N/A"))
            continue
        w = walk(bids, asks, "sell", h.quantity * f, h.coin, source, spot_fee)
        candidates.append(RepairCandidate(
            "SELL_RTOKEN", f"Sell {f:.1%} of {h.coin} ({h.quantity * f:.4f})", Decimal(0), Decimal(0), w.total_cost, w.slippage_bps,
            sold(f), not w.exhausted,
            "Visible public book too thin for this size." if w.exhausted else f"Public rToken book walk over {w.levels_used} levels; weekend orders may be cancelled at reopen (§40).",
            _liquidity(w)))

    # 4. Mixed: half of the perp reduction, rest in stable collateral.
    reduce = next((c for c in candidates if c.action == "REDUCE_PERP" and c.feasible), None)
    if reduce is not None and positions:
        p = positions[0]
        half = reduce.exposure_change_pct / 200
        bids, asks, source = fetch_book(client, "USDT-FUTURES", p.symbol, demo_env)
        w = walk(bids, asks, "sell" if p.direction > 0 else "buy", p.size * half, p.symbol, source, fee_rate(client, p.symbol))
        mm_cut = p.size * half * p.mark_price * (p.mmr_rate or Decimal(0))
        base2 = replace(baseline, effective_equity=baseline.effective_equity - w.total_cost,
                        maintenance_margin=max(baseline.maintenance_margin - mm_cut, Decimal(0)))
        pos2 = [replace(p, size=p.size * (1 - half))] + positions[1:]
        mid = run_shadow(base2, holdings, pos2, scenario, tier_mode)
        x2 = with_stable(base2, mid)
        final = run_shadow(replace(base2, effective_equity=base2.effective_equity + x2 * stable_rate), holdings, pos2, scenario, tier_mode)
        candidates.append(RepairCandidate("MIXED", f"Reduce {p.symbol} by {half:.1%} and deposit {x2:,.2f} USDT", x2, half * 100,
                                          w.total_cost, w.slippage_bps, final, not w.exhausted, f"Book walk ({source}).", _liquidity(w)))
    return before, candidates, notes


OBJECTIVES = {
    "min_cash": lambda c: (not c.feasible, c.cash_required, c.exposure_change_pct),
    "min_exposure_change": lambda c: (not c.feasible, c.exposure_change_pct, c.cash_required),
    "min_execution_cost": lambda c: (not c.feasible, c.execution_cost if c.execution_cost is not None else Decimal("1e18"), c.cash_required),
    "max_safety": lambda c: (not c.feasible, c.shadow_after.shadow_core_ratio if c.shadow_after and c.shadow_after.shadow_core_ratio is not None else Decimal("1e18")),
}
