"""Tiered collateral valuation (AGENTS.md §19).

Bitget's public `market/discount-rate` returns, per coin, `list[] = {tierStartValue, discountRate}`
(verified 2026-10-06). Two facts are NOT verified and are therefore explicit parameters, never
silent defaults (OPEN_QUESTIONS Q-TIER-UNIT, Q-TIER-APPLY):

- the unit of `tierStartValue` (treated here as USD notional, `TIER_UNIT_ASSUMPTION`);
- whether tiers apply marginally (value inside each tier × its rate) or as one rate for the
  whole holding. Both are implemented so reconciliation can test which one matches Bitget.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Any

TIER_UNIT_ASSUMPTION = "UNVERIFIED_ASSUMPTION: tierStartValue is USD notional (Q-TIER-UNIT)"


class TierMode(StrEnum):
    MARGINAL = "MARGINAL"
    WHOLE = "WHOLE"


@dataclass(frozen=True)
class CollateralTier:
    start: Decimal
    rate: Decimal


@dataclass(frozen=True)
class CollateralSchedule:
    coin: str
    tiers: tuple[CollateralTier, ...]
    source: str
    retrieved_at: str

    @classmethod
    def from_bitget(cls, row: dict[str, Any], source: str, retrieved_at: str) -> "CollateralSchedule":
        tiers = sorted(
            (CollateralTier(Decimal(t["tierStartValue"]), Decimal(t["discountRate"])) for t in row["list"]),
            key=lambda t: t.start,
        )
        if not tiers or tiers[0].start != 0:
            raise ValueError(f"{row.get('coin')}: schedule must start at 0")
        return cls(coin=row["coin"], tiers=tuple(tiers), source=source, retrieved_at=retrieved_at)


def tiered_collateral_value(notional: Decimal, schedule: CollateralSchedule, mode: TierMode) -> Decimal:
    """Collateral contribution of a holding worth `notional` (USD). Negative notional contributes nothing."""
    if notional <= 0:
        return Decimal(0)
    tiers = schedule.tiers
    if mode is TierMode.WHOLE:
        rate = next(t.rate for t in reversed(tiers) if notional >= t.start)
        return notional * rate
    value = Decimal(0)
    for i, tier in enumerate(tiers):
        upper = tiers[i + 1].start if i + 1 < len(tiers) else None
        if notional <= tier.start:
            break
        inside = (min(notional, upper) if upper is not None else notional) - tier.start
        value += inside * tier.rate
    return value


def schedules_from_payload(rows: list[dict[str, Any]], source: str, retrieved_at: str) -> dict[str, CollateralSchedule]:
    return {row["coin"].upper(): CollateralSchedule.from_bitget(row, source, retrieved_at) for row in rows if row.get("list")}
