"""UTA reconciliation (AGENTS.md §14–§16, M2).

PRISM tries to reproduce Bitget's reported effective equity, maintenance margin and margin
ratio from per-coin values and public collateral tiers. Where Bitget's rules are not
verified, each competing interpretation is computed and scored against the observed account;
the result says which interpretation (if any) matched. It never declares RECONCILED on a
quantity it could not reconstruct.

Hypotheses tested for effective equity (OPEN_QUESTIONS Q-TIER-APPLY, Q-UPNL):
  tier mode    MARGINAL | WHOLE
  upnl         INCLUDED (per-coin usdValue already contains unrealised PnL) | ADDED (add account upnl)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from itertools import product

from ..account import AccountSnapshot
from ..collateral import TIER_UNIT_ASSUMPTION, CollateralSchedule, TierMode, tiered_collateral_value
from ..provenance import utc_now

RECONCILER_VERSION = "prism-reconcile-v0.1"
# Initial tolerances from AGENTS.md §15; tighten once real accounts reconcile.
EQUITY_TOLERANCE = Decimal("0.005")  # 0.50% relative
RATIO_TOLERANCE_PP = Decimal("0.5")  # percentage points


class ReconciliationMode(StrEnum):
    RECONCILED = "RECONCILED"
    OBSERVED_BASELINE = "OBSERVED_BASELINE"
    NO_EXPOSURE = "NO_EXPOSURE"


@dataclass(frozen=True)
class EquityHypothesis:
    tier_mode: TierMode
    upnl: str
    reconstructed: Decimal
    error: Decimal | None  # relative to observed effEquity


@dataclass(frozen=True)
class ReconciliationResult:
    mode: ReconciliationMode
    observed_effective_equity: Decimal | None
    observed_maintenance_margin: Decimal | None
    observed_margin_ratio_raw: Decimal | None
    equity_hypotheses: tuple[EquityHypothesis, ...]
    best_equity_hypothesis: EquityHypothesis | None
    reconstructed_maintenance_margin: Decimal | None
    margin_ratio_unit: str | None  # inferred "fraction"/"percent" when evidence allows
    reasons: tuple[str, ...]
    assumptions: tuple[str, ...]
    account_environment: str
    calculated_at: str = field(default_factory=lambda: utc_now().isoformat())
    version: str = RECONCILER_VERSION


def _eligible(coin: str, snapshot: AccountSnapshot, schedules: dict[str, CollateralSchedule]) -> bool:
    if snapshot.collateral_type == "custom" and snapshot.collateral_coins is not None:
        return coin in snapshot.collateral_coins
    return coin in schedules


def reconstruct_effective_equity(
    snapshot: AccountSnapshot, schedules: dict[str, CollateralSchedule], tier_mode: TierMode, upnl: str
) -> tuple[Decimal, list[str]]:
    total = Decimal(0)
    notes: list[str] = []
    for asset in snapshot.assets:
        if asset.usd_value is None:
            notes.append(f"{asset.coin}: usdValue missing")
            continue
        if asset.usd_value < 0:
            total += asset.usd_value  # liabilities count in full (assumption, Q-NEGATIVE-BALANCE)
            continue
        if not _eligible(asset.coin, snapshot, schedules):
            continue
        schedule = schedules.get(asset.coin)
        if schedule is None:
            notes.append(f"{asset.coin}: eligible but no tier schedule")
            continue
        total += tiered_collateral_value(asset.usd_value, schedule, tier_mode)
    if upnl == "ADDED" and snapshot.unrealised_pnl is not None:
        total += snapshot.unrealised_pnl
    return total, notes


def reconstruct_maintenance_margin(snapshot: AccountSnapshot) -> tuple[Decimal | None, str | None]:
    total = Decimal(0)
    for p in snapshot.positions:
        if p.size is None or p.mark_price is None or p.mmr_rate is None:
            return None, f"{p.symbol}: size, mark price or MMR rate not reported"
        total += abs(p.size) * p.mark_price * p.mmr_rate
    # The taker-fee term (AGENTS.md §18) is unknown (account/fee-rate unavailable), so this is a floor.
    return total, None


def infer_ratio_unit(snapshot: AccountSnapshot) -> str | None:
    mm, eq, ratio = snapshot.maintenance_margin, snapshot.effective_equity, snapshot.margin_ratio_raw
    if not mm or not eq or ratio is None or eq <= 0 or ratio == 0:
        return None
    implied = mm / eq
    if abs(implied - ratio) * 100 <= RATIO_TOLERANCE_PP:
        return "fraction"
    if abs(implied * 100 - ratio) <= RATIO_TOLERANCE_PP:
        return "percent"
    return None


def reconcile(snapshot: AccountSnapshot, schedules: dict[str, CollateralSchedule]) -> ReconciliationResult:
    assumptions = [TIER_UNIT_ASSUMPTION, "Negative coin balances reduce adjusted equity in full (Q-NEGATIVE-BALANCE)"]
    observed = snapshot.effective_equity
    common = dict(
        observed_effective_equity=observed,
        observed_maintenance_margin=snapshot.maintenance_margin,
        observed_margin_ratio_raw=snapshot.margin_ratio_raw,
        account_environment=snapshot.account_environment,
        assumptions=tuple(assumptions),
    )
    if snapshot.is_empty:
        return ReconciliationResult(
            mode=ReconciliationMode.NO_EXPOSURE, equity_hypotheses=(), best_equity_hypothesis=None,
            reconstructed_maintenance_margin=None, margin_ratio_unit=None,
            reasons=("Account has no assets or positions; there is nothing to reconcile.",), **common,
        )

    reasons: list[str] = []
    hypotheses = []
    for tier_mode, upnl in product(TierMode, ("INCLUDED", "ADDED")):
        value, notes = reconstruct_effective_equity(snapshot, schedules, tier_mode, upnl)
        reasons += [n for n in notes if n not in reasons]
        error = abs(value - observed) / abs(observed) if observed else None
        hypotheses.append(EquityHypothesis(tier_mode, upnl, value, error))
    scored = [h for h in hypotheses if h.error is not None]
    best = min(scored, key=lambda h: h.error) if scored else None
    equity_ok = best is not None and best.error <= EQUITY_TOLERANCE
    if not equity_ok:
        reasons.append("No tested interpretation reproduces effEquity within 0.50%.")
    elif sum(1 for h in scored if h.error <= EQUITY_TOLERANCE) > 1:
        reasons.append("Several interpretations match effEquity; this account cannot distinguish them.")

    if snapshot.open_orders:
        reasons.append(f"{len(snapshot.open_orders)} unfilled order(s) present: Bitget's mmr/imr include open orders, and PRISM does not yet reproduce the order component exactly (Q-ORDER-MARGIN).")
    mm, mm_note = reconstruct_maintenance_margin(snapshot)
    mm_ok = False
    if mm_note:
        reasons.append(mm_note)
    elif snapshot.maintenance_margin is not None:
        observed_mm = snapshot.maintenance_margin
        if observed_mm == 0:
            mm_ok = mm == 0
        else:
            mm_ok = abs(mm - observed_mm) / observed_mm <= EQUITY_TOLERANCE
        if snapshot.positions:
            reasons.append("Maintenance margin reconstruction omits the taker-fee term (fee rate unavailable); it is a floor.")
        if not mm_ok:
            reasons.append("Reconstructed maintenance margin differs from reported mmr by more than 0.50%.")
    unit = infer_ratio_unit(snapshot)
    if unit is None and snapshot.margin_ratio_raw not in (None, Decimal(0)):
        reasons.append("mgnRatio is not consistent with mmr/effEquity under either unit; partial-liquidation fee term unknown.")

    mode = ReconciliationMode.RECONCILED if (equity_ok and mm_ok) else ReconciliationMode.OBSERVED_BASELINE
    if mode is ReconciliationMode.OBSERVED_BASELINE:
        reasons.append("Shadow results must be delta-based from Bitget's observed account state.")
    return ReconciliationResult(
        mode=mode, equity_hypotheses=tuple(hypotheses), best_equity_hypothesis=best,
        reconstructed_maintenance_margin=mm, margin_ratio_unit=unit, reasons=tuple(reasons), **common,
    )
