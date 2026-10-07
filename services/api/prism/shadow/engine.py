"""Shadow account and Thaw Frontier (AGENTS.md §20, §29–§32, M4/M5).

Delta model anchored to the baseline account (Bitget-observed, or a labelled hypothetical):

    ShadowAdjustedEquity(s) = baseline effEquity + ΣΔCollateral_i(s) + ΣΔPnL_j(s)
    ΔCollateral_i(s)        = Tiered(q_i · p_i(s)) − Tiered(q_i · p_i)        (collateral-enabled only)
    ΔPnL_j(s)               = direction_j · size_j · (mark_j(s) − mark_j)     (linear USDT perps)
    ShadowMM(s)             = baseline MM + Σ |size_j| · (mark_j(s) − mark_j) · mmr_j
    shadow_core_ratio(s)    = ShadowMM(s) / ShadowAdjustedEquity(s)

`shadow_core_ratio` excludes the partial-liquidation fee term and is NOT Bitget's `mgnRatio`
(AGENTS.md §20). Position-tier changes under shocks are ignored and flagged. No LLM is involved.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum

from ..collateral import CollateralSchedule, TierMode, tiered_collateral_value

ENGINE_VERSION = "prism-shadow-v0.1"
ZERO = Decimal(0)


class Factor(StrEnum):
    CRYPTO = "crypto"
    RTOKEN = "rtoken"  # rToken / equity factor: the frontier's horizontal axis (§31)
    NONE = "none"


class RiskState(StrEnum):
    SAFE = "SAFE"
    WATCH = "WATCH"
    WARNING = "WARNING"
    BREACH = "BREACH"


@dataclass(frozen=True)
class Thresholds:
    """User-selectable boundaries on shadow_core_ratio (§32). 0.80 mirrors Bitget's documented warning
    reference and 1.00 the account-risk boundary; neither is a liquidation guarantee."""

    watch: Decimal = Decimal("0.50")
    warning: Decimal = Decimal("0.80")
    breach: Decimal = Decimal("1.00")

    def classify(self, ratio: Decimal | None) -> RiskState:
        if ratio is None or ratio >= self.breach:
            return RiskState.BREACH
        if ratio >= self.warning:
            return RiskState.WARNING
        if ratio >= self.watch:
            return RiskState.WATCH
        return RiskState.SAFE


@dataclass(frozen=True)
class Holding:
    coin: str
    quantity: Decimal
    price: Decimal  # currently recognized price
    factor: Factor
    collateral_enabled: bool
    schedule: CollateralSchedule | None
    price_source: str
    # Historical sensitivity to the crypto factor (wrong_way.py). When set, a crypto shock also moves this
    # holding by beta x shock, on top of its own factor's shock. Model-based; reported in warnings.
    crypto_beta: Decimal | None = None


@dataclass(frozen=True)
class PerpPosition:
    symbol: str
    direction: int  # +1 long, -1 short
    size: Decimal  # base units
    mark_price: Decimal
    factor: Factor
    mmr_rate: Decimal | None
    mark_source: str


@dataclass(frozen=True)
class Baseline:
    effective_equity: Decimal
    maintenance_margin: Decimal
    label: str  # e.g. "BITGET OBSERVED (DEMO_PAPTRADING)" or "HYPOTHETICAL ACCOUNT"
    reconciliation_mode: str

    @property
    def core_ratio(self) -> Decimal | None:
        return self.maintenance_margin / self.effective_equity if self.effective_equity > 0 else None


@dataclass(frozen=True)
class Scenario:
    crypto_shock: Decimal = ZERO  # fractional move, e.g. Decimal("-0.10")
    rtoken_shock: Decimal = ZERO
    price_overrides: dict[str, Decimal] = field(default_factory=dict)  # absolute shadow prices by coin/symbol
    label: str = "STRESS SCENARIO"

    def shocked(self, key: str, price: Decimal, factor: Factor, crypto_beta: Decimal | None = None) -> Decimal:
        if key in self.price_overrides:
            return self.price_overrides[key]
        move = {Factor.CRYPTO: self.crypto_shock, Factor.RTOKEN: self.rtoken_shock}.get(factor, ZERO)
        if crypto_beta is not None and factor is not Factor.CRYPTO:
            move += crypto_beta * self.crypto_shock
        return max(price * (1 + move), ZERO)


@dataclass(frozen=True)
class Component:
    name: str
    kind: str  # "collateral" | "pnl" | "maintenance"
    delta: Decimal


@dataclass(frozen=True)
class ShadowResult:
    scenario: Scenario
    shadow_effective_equity: Decimal
    shadow_maintenance_margin: Decimal
    shadow_core_ratio: Decimal | None
    latent_collateral_gap: Decimal
    pnl_delta: Decimal
    state: RiskState
    thaw_at_risk_pp: Decimal | None
    components: tuple[Component, ...]
    warnings: tuple[str, ...]
    # Tiered (collateral-weighted) value of rToken holdings before and under the scenario.
    recognized_rtoken_collateral: Decimal = ZERO
    shadow_rtoken_collateral: Decimal = ZERO
    version: str = ENGINE_VERSION

    @property
    def top_contributors(self) -> tuple[Component, ...]:
        equity = [c for c in self.components if c.kind != "maintenance" and c.delta != 0]
        return tuple(sorted(equity, key=lambda c: c.delta)[:3])


def run_shadow(
    baseline: Baseline,
    holdings: list[Holding],
    positions: list[PerpPosition],
    scenario: Scenario,
    tier_mode: TierMode = TierMode.MARGINAL,
    thresholds: Thresholds = Thresholds(),
) -> ShadowResult:
    components: list[Component] = []
    warnings: list[str] = []
    gap = ZERO
    rtoken_recognized = rtoken_shadow = ZERO
    for h in holdings:
        if not h.collateral_enabled:
            continue
        if h.schedule is None:
            warnings.append(f"{h.coin}: collateral-enabled but no tier schedule; excluded")
            continue
        new_price = scenario.shocked(h.coin, h.price, h.factor, h.crypto_beta)
        if h.crypto_beta is not None and scenario.crypto_shock != 0:
            warnings.append(f"{h.coin}: crypto shock propagated with historical beta {h.crypto_beta:.2f} (model-based)")
        delta = tiered_collateral_value(h.quantity * new_price, h.schedule, tier_mode) - tiered_collateral_value(
            h.quantity * h.price, h.schedule, tier_mode
        )
        gap += delta
        components.append(Component(h.coin, "collateral", delta))
        if h.factor is Factor.RTOKEN:
            base_value = tiered_collateral_value(h.quantity * h.price, h.schedule, tier_mode)
            rtoken_recognized += base_value
            rtoken_shadow += base_value + delta

    pnl = ZERO
    mm_delta = ZERO
    for p in positions:
        new_mark = scenario.shocked(p.symbol, p.mark_price, p.factor)
        d = p.direction * p.size * (new_mark - p.mark_price)
        pnl += d
        components.append(Component(p.symbol, "pnl", d))
        if p.mmr_rate is None:
            warnings.append(f"{p.symbol}: MMR rate unknown; maintenance margin not rescaled")
        else:
            m = abs(p.size) * (new_mark - p.mark_price) * p.mmr_rate
            mm_delta += m
            components.append(Component(p.symbol, "maintenance", m))
    if positions:
        warnings.append("Position-tier changes under the shock are ignored (CONSERVATIVE_APPROXIMATION pending tier modelling)")

    equity = baseline.effective_equity + gap + pnl
    mm = max(baseline.maintenance_margin + mm_delta, ZERO)
    ratio = mm / equity if equity > 0 else None
    current = baseline.core_ratio
    tar = (ratio - current) * 100 if ratio is not None and current is not None else None
    return ShadowResult(
        scenario=scenario, shadow_effective_equity=equity, shadow_maintenance_margin=mm, shadow_core_ratio=ratio,
        latent_collateral_gap=gap, pnl_delta=pnl, state=thresholds.classify(ratio), thaw_at_risk_pp=tar,
        components=tuple(components), warnings=tuple(warnings),
        recognized_rtoken_collateral=rtoken_recognized, shadow_rtoken_collateral=rtoken_shadow,
    )


@dataclass(frozen=True)
class FrontierCell:
    crypto_shock: Decimal
    rtoken_shock: Decimal
    result: ShadowResult
    blind_zone: bool


@dataclass(frozen=True)
class Frontier:
    crypto_shocks: tuple[Decimal, ...]
    rtoken_shocks: tuple[Decimal, ...]
    cells: tuple[FrontierCell, ...]
    boundary: Decimal
    current_state: RiskState
    baseline_label: str

    def cell(self, crypto: Decimal, rtoken: Decimal) -> FrontierCell:
        return next(c for c in self.cells if c.crypto_shock == crypto and c.rtoken_shock == rtoken)

    @property
    def nearest_blind_zone_entry(self) -> FrontierCell | None:
        """Blind-zone cell with the smallest combined shock magnitude."""
        zone = [c for c in self.cells if c.blind_zone]
        return min(zone, key=lambda c: (abs(c.crypto_shock) + abs(c.rtoken_shock), c.crypto_shock, c.rtoken_shock)) if zone else None


DEFAULT_SHOCKS = tuple(Decimal(x) / 100 for x in (10, 5, 0, -5, -10, -15, -20))


def run_frontier(
    baseline: Baseline,
    holdings: list[Holding],
    positions: list[PerpPosition],
    crypto_shocks: tuple[Decimal, ...] = DEFAULT_SHOCKS,
    rtoken_shocks: tuple[Decimal, ...] = DEFAULT_SHOCKS,
    tier_mode: TierMode = TierMode.MARGINAL,
    thresholds: Thresholds = Thresholds(),
    boundary: Decimal | None = None,
) -> Frontier:
    """Each cell reruns the shadow engine (§31). Blind zone: current state below `boundary`, cell at or above it (§32)."""
    boundary = boundary if boundary is not None else thresholds.warning
    current_ratio = baseline.core_ratio
    current_ok = current_ratio is not None and current_ratio < boundary
    cells = []
    for c in crypto_shocks:
        for r in rtoken_shocks:
            result = run_shadow(baseline, holdings, positions, Scenario(crypto_shock=c, rtoken_shock=r), tier_mode, thresholds)
            breached = result.shadow_core_ratio is None or result.shadow_core_ratio >= boundary
            cells.append(FrontierCell(c, r, result, blind_zone=current_ok and breached))
    return Frontier(
        crypto_shocks=crypto_shocks, rtoken_shocks=rtoken_shocks, cells=tuple(cells), boundary=boundary,
        current_state=thresholds.classify(current_ratio), baseline_label=baseline.label,
    )
