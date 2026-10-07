"""Print the Dual Reality Ledger, reconciliation status and Thaw Frontier (AGENTS.md §2 surfaces A/B).

    uv run --project services/api python scripts/run_shadow.py --account judge            # hypothetical account, real prices
    uv run --project services/api python scripts/run_shadow.py --account judge --refresh  # rebuild fixture from live prices
    uv run --project services/api python scripts/run_shadow.py --account bitget           # your Bitget account (demo or live)
    ... --rtoken-shock -0.06 --crypto-shock -0.05                                         # the "central" scenario shown in the ledger

Read-only. Every figure is either Bitget-observed or a labelled PRISM shadow calculation.
"""

from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "services" / "api"))

from prism.config import load_settings  # noqa: E402
from prism.connectors.bitget import BitgetClient  # noqa: E402
from prism.shadow import Scenario, run_frontier, run_shadow  # noqa: E402
from prism.workbench import JUDGE_FIXTURE, bitget_workbench, build_judge_fixture, judge_workbench  # noqa: E402


def usd(x: Decimal | None) -> str:
    return "n/a" if x is None else f"${x:,.2f}"


def pct(x: Decimal | None) -> str:
    return "n/a (equity <= 0)" if x is None else f"{x * 100:.1f}%"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--account", choices=["judge", "bitget"], default="judge")
    parser.add_argument("--refresh", action="store_true", help="rebuild the judge fixture from live public prices")
    parser.add_argument("--rtoken-shock", type=Decimal, default=Decimal("-0.06"))
    parser.add_argument("--crypto-shock", type=Decimal, default=Decimal("-0.05"))
    args = parser.parse_args()

    settings = load_settings()
    with BitgetClient(settings) as client:
        if args.account == "judge":
            path = REPO_ROOT / "data" / "fixtures" / JUDGE_FIXTURE
            fixture = build_judge_fixture(client, path) if args.refresh or not path.exists() else json.loads(path.read_text(encoding="utf-8"))
            wb = judge_workbench(client, fixture)
        else:
            wb = bitget_workbench(client)

    print(f"\n=== {wb.baseline.label} ===   reconciliation: {wb.baseline.reconciliation_mode}")
    for note in wb.data_notes:
        print(f"  note: {note}")
    if wb.reconciliation and wb.reconciliation.best_equity_hypothesis:
        h = wb.reconciliation.best_equity_hypothesis
        print(f"  best effEquity hypothesis: tiers={h.tier_mode} upnl={h.upnl} -> {usd(h.reconstructed)} (error {h.error:.4%})")
    if not wb.holdings and not wb.positions:
        print("\nAccount holds nothing yet; the shadow account and frontier need at least one holding or position.")
        return 0

    print("\nHoldings")
    for h in wb.holdings:
        print(f"  {h.coin:<8} qty {h.quantity:>12}  @ {h.price:>12}  factor={h.factor:<6} collateral={'yes' if h.collateral_enabled else 'no'}")
    for p in wb.positions:
        print(f"  {p.symbol:<8} {'LONG' if p.direction > 0 else 'SHORT'} {p.size} @ mark {p.mark_price}  mmr={p.mmr_rate}  factor={p.factor}")

    central = run_shadow(wb.baseline, wb.holdings, wb.positions, Scenario(crypto_shock=args.crypto_shock, rtoken_shock=args.rtoken_shock))
    print(f"\nDUAL REALITY LEDGER   (shadow = STRESS SCENARIO crypto {args.crypto_shock:+.0%}, rToken {args.rtoken_shock:+.0%})")
    print(f"  {'':<28}{'BASELINE':>16}{'PRISM SHADOW':>16}")
    print(f"  {'Effective equity':<28}{usd(wb.baseline.effective_equity):>16}{usd(central.shadow_effective_equity):>16}")
    print(f"  {'Maintenance margin':<28}{usd(wb.baseline.maintenance_margin):>16}{usd(central.shadow_maintenance_margin):>16}")
    print(f"  {'Core margin ratio*':<28}{pct(wb.baseline.core_ratio):>16}{pct(central.shadow_core_ratio):>16}")
    print(f"  {'Latent Collateral Gap':<28}{'':>16}{usd(central.latent_collateral_gap):>16}")
    print(f"  {'Derivative PnL delta':<28}{'':>16}{usd(central.pnl_delta):>16}")
    tar = "n/a" if central.thaw_at_risk_pp is None else f"{central.thaw_at_risk_pp:+.1f}pp"
    print(f"  {'Thaw-at-Risk':<28}{'':>16}{tar:>16}")
    print(f"  {'State':<28}{'':>16}{central.state:>16}")
    print("  * MM / effective equity, excluding the partial-liquidation fee term; not Bitget's mgnRatio.")
    for w in central.warnings:
        print(f"  warning: {w}")

    frontier = run_frontier(wb.baseline, wb.holdings, wb.positions)
    print(f"\nTHAW FRONTIER  (rows: crypto shock, columns: rToken/equity shock; boundary {frontier.boundary:.0%}; '*' = Blind Zone)")
    print("  crypto\\rTok " + "".join(f"{r:>+9.0%}" for r in frontier.rtoken_shocks))
    for c in frontier.crypto_shocks:
        row = []
        for r in frontier.rtoken_shocks:
            cell = frontier.cell(c, r)
            ratio = cell.result.shadow_core_ratio
            row.append(f"{('BRCH' if ratio is None else f'{ratio * 100:.0f}%') + ('*' if cell.blind_zone else ''):>9}")
        print(f"  {c:>+10.0%} " + "".join(row))
    entry = frontier.nearest_blind_zone_entry
    if entry:
        top = ", ".join(f"{x.name} {usd(x.delta)}" for x in entry.result.top_contributors)
        print(f"\n  Nearest Blind-Zone entry: crypto {entry.crypto_shock:+.0%}, rToken {entry.rtoken_shock:+.0%} -> {pct(entry.result.shadow_core_ratio)}; top contributors: {top}")
    else:
        print(f"\n  No Blind Zone on this grid (current state {frontier.current_state}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
