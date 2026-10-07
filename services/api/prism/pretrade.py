"""Pre-Trade Gate (AGENTS.md §34–§36, M6). Read-only: analyses a proposed trade, never places it.

Pipeline: parsed trade -> validate instrument -> price via public ticker -> merge into positions ->
rescale MM with the published position tier -> rerun scenarios and the Thaw Frontier before/after ->
wrong-way collateral for the traded factor -> deterministic verdict. Qwen only parses the request
upstream and may explain this output downstream.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal

from .connectors.bitget import BitgetClient
from .connectors.qwen import ProposedTrade
from .repair import fetch_book, walk
from .shadow import Frontier, PerpPosition, Scenario, ShadowResult, run_frontier, run_shadow
from .workbench import Workbench, fetch_mmr_rate, fetch_price, perp_factor
from . import wrong_way

VERSION = "prism-pretrade-v0.1"

# User-adjustable stress scenarios. They are NOT Reality-Engine outputs (M3 pending) and are labelled as such.
SCENARIOS = {
    "mild": Scenario(crypto_shock=Decimal("-0.05"), rtoken_shock=Decimal("-0.03"), label="STRESS SCENARIO mild"),
    "central": Scenario(crypto_shock=Decimal("-0.10"), rtoken_shock=Decimal("-0.06"), label="STRESS SCENARIO central"),
    "severe": Scenario(crypto_shock=Decimal("-0.15"), rtoken_shock=Decimal("-0.12"), label="STRESS SCENARIO severe"),
}


class TradeRejected(Exception):
    pass


def is_demo(wb: Workbench) -> bool:
    """Demo accounts must be priced with demo-environment tiers, books and marks (they differ from live)."""
    return wb.snapshot is not None and wb.snapshot.account_environment == "DEMO_PAPTRADING"


@dataclass(frozen=True)
class PreTradeResult:
    trade: ProposedTrade
    added_size: Decimal
    entry_price: Decimal
    entry_slippage_cost: Decimal
    mmr_rate: Decimal | None
    mmr_source: str
    before: dict[str, ShadowResult]
    after: dict[str, ShadowResult]
    frontier_before: Frontier
    frontier_after: Frontier
    wrong_way: list[wrong_way.WrongWayReport]
    verdict: str
    notes: list[str]
    version: str = VERSION


def apply_trade(client: BitgetClient, wb: Workbench, trade: ProposedTrade) -> tuple[Workbench, Decimal, Decimal, Decimal, Decimal | None, str]:
    if trade.category != "USDT-FUTURES":
        raise TradeRejected("Only USDT-margined perpetuals are supported by the Pre-Trade Gate so far.")
    if not trade.instrument or trade.direction is None:
        raise TradeRejected("The request needs an instrument and a direction.")
    demo = is_demo(wb)
    mark, mark_source = fetch_price(client, "USDT-FUTURES", trade.instrument, demo_env=demo)
    if trade.quantity:
        size = Decimal(str(trade.quantity))
    elif trade.notional_usd:
        size = Decimal(str(trade.notional_usd)) / mark
    else:
        raise TradeRejected("The request needs a size: a USD notional or a quantity.")
    direction = 1 if trade.direction == "long" else -1

    bids, asks, book_source = fetch_book(client, "USDT-FUTURES", trade.instrument, demo)
    w = walk(bids, asks, "buy" if direction > 0 else "sell", size, trade.instrument, book_source)
    if w.exhausted:
        raise TradeRejected("The visible order book cannot fill this size.")

    positions = list(wb.positions)
    existing = next((i for i, p in enumerate(positions) if p.symbol == trade.instrument), None)
    if existing is None:
        net_size, net_dir = size, direction
    else:
        old = positions.pop(existing)
        signed = old.direction * old.size + direction * size
        net_size, net_dir = abs(signed), (1 if signed >= 0 else -1)
    mmr, mmr_source = fetch_mmr_rate(client, trade.instrument, net_size * mark, demo_env=demo)
    if net_size > 0:
        positions.append(PerpPosition(trade.instrument, net_dir, net_size, mark, perp_factor(client, trade.instrument, wb.schedules, demo), mmr,
                                      f"{mark_source}; mmr {mmr_source}"))
    old_mm = sum((abs(p.size) * p.mark_price * (p.mmr_rate or 0) for p in wb.positions if p.symbol == trade.instrument), Decimal(0))
    new_mm = net_size * mark * (mmr or 0)
    baseline = replace(wb.baseline, effective_equity=wb.baseline.effective_equity - w.slippage_cost,
                       maintenance_margin=wb.baseline.maintenance_margin - old_mm + new_mm,
                       label=wb.baseline.label + " + PROPOSED TRADE (PRISM estimate)")
    return replace(wb, baseline=baseline, positions=positions), size, mark, w.slippage_cost, mmr, mmr_source


def verdict(after: dict[str, ShadowResult], frontier_before: Frontier, frontier_after: Frontier, ww: list[wrong_way.WrongWayReport]) -> str:
    lines = [f"After the trade: mild {after['mild'].state}, central {after['central'].state}, severe {after['severe'].state} (stress scenarios)."]
    before_n = sum(c.blind_zone for c in frontier_before.cells)
    after_n = sum(c.blind_zone for c in frontier_after.cells)
    lines.append(f"Blind-Zone cells on the frontier: {before_n} before, {after_n} after.")
    for r in ww:
        if r.wrong_way_assets:
            lines.append(f"Wrong-way collateral against {r.position}: {', '.join(r.wrong_way_assets)} ({r.wrong_way_share:.1%} of collateral value).")
    return " ".join(lines)


def analyze(client: BitgetClient, wb: Workbench, trade: ProposedTrade) -> PreTradeResult:
    new_wb, size, mark, slip, mmr, mmr_source = apply_trade(client, wb, trade)
    run = lambda w: {k: run_shadow(w.baseline, w.holdings, w.positions, s) for k, s in SCENARIOS.items()}  # noqa: E731
    before, after = run(wb), run(new_wb)
    f_before = run_frontier(wb.baseline, wb.holdings, wb.positions)
    f_after = run_frontier(new_wb.baseline, new_wb.holdings, new_wb.positions)
    ww = [r for r in wrong_way.analyze(client, new_wb.holdings, new_wb.positions) if r.position == trade.instrument]
    notes = ["Entry cost includes estimated slippage from the public order book; trading fees are excluded (Q-FEE).",
             "Initial margin is not checked: it depends on leverage, which was " + ("stated." if trade.leverage else "not stated.")]
    return PreTradeResult(trade, size, mark, slip, mmr, mmr_source, before, after, f_before, f_after, ww,
                          verdict(after, f_before, f_after, ww), notes)
