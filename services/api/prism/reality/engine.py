"""Reality Engine (AGENTS.md §21–§28, M3): how much evidence stands behind an off-hours rToken price.

Pure, deterministic rules with reason codes; no LLM involvement. Every rule and threshold is
named here so the Evidence Lock can show it. Facts that are not verified are carried as explicit
assumptions rather than silently used:

- A1 (Q-RECOGNIZED-PRICE): Bitget's frozen collateral index is not exposed by any verified
  endpoint. The *reference proxy* is the underlying's last extended-hours trade at or before the
  20:00 New York boundary (Yahoo chart, unofficial), per Bitget's documented "frozen at the most
  recent extended-session close"; if unavailable, the rToken's own close at that boundary.
- A2 (Q-TZ): session clock times are read as New York wall-clock time.
- A3: the matching USDT-margined stock perp is used as an independent price reference. It is a
  different instrument with its own index and funding; agreement is corroboration, not proof.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Any
from zoneinfo import ZoneInfo

ENGINE_VERSION = "prism-reality-v0.1"
NY = ZoneInfo("America/New_York")
EXTENDED_CLOSE = time(20, 0)

ASSUMPTIONS = (
    "A1 reference proxy = underlying's last extended-hours trade at/before 20:00 New York (Yahoo, unofficial), else the rToken close there; Bitget's collateral index is not exposed (Q-RECOGNIZED-PRICE)",
    "A2 session clock times read as New York wall-clock time (Q-TZ)",
    "A3 the matching stock perp is an independent reference; agreement is corroboration, not proof",
)

# Rule thresholds (transparent, documented; revisit once closure history accumulates, §24, §49).
MIN_MOVE_FOR_STATE = Decimal("0.005")      # |move| below 0.5%: no meaningful off-hours move
AGREE_BPS = Decimal("75")                   # rToken vs perp-implied price within 75 bps: references agree
CONFLICT_BPS = Decimal("200")               # beyond 200 bps: credible references materially disagree
OVERSHOOT_RATIO = Decimal("1.5")
NORMALIZATION_LIMIT_BPS = Decimal("500")
UNDERLYING_FRESH_SECONDS = 900            # underlying trade older than 15 minutes is not used as a live reference    # live level comparison only when rToken and perp trade within 5%            # same direction but rToken move > 1.5x the reference move
STALE_SECONDS = 300                         # ticker older than 5 minutes: no usable live market
MIN_HISTORY_OBS = 500                       # one-minute observations needed before own-history percentiles
BAND_MIN_WINDOWS = 8                        # closure windows with realized reopen before any stress band (§26, §52)


class Regime(StrEnum):
    LIVE_REFERENCE = "LIVE_REFERENCE"       # U.S. pre-market/regular/after-hours: collateral index tracks the underlying
    FROZEN_REFERENCE = "FROZEN_REFERENCE"   # overnight, weekend, holiday: index frozen at the last extended-session close
    UNKNOWN = "UNKNOWN"


class Quality(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    UNAVAILABLE = "UNAVAILABLE"


class EvidenceMode(StrEnum):
    OBSERVED = "OBSERVED"
    HYBRID = "HYBRID"
    INFERRED = "INFERRED"


class MarketState(StrEnum):
    DISCOVERY = "DISCOVERY"
    DRIFT = "DRIFT"
    OVERSHOOT = "OVERSHOOT"
    THIN = "THIN"
    CONFLICT = "CONFLICT"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    NOT_APPLICABLE = "NOT_APPLICABLE"       # reference is live; there is no closure gap to assess


# ---- session -----------------------------------------------------------------------------------

def _closed_day(d: datetime, calendar: dict[str, Any] | None) -> bool:
    if not calendar:
        return d.weekday() >= 5
    if d.strftime("%A").upper() in (calendar.get("regularConfig") or []):
        return True
    for w in calendar.get("specificConfig") or []:
        start = datetime.strptime(w["startTime"], "%Y-%m-%d %H:%M").replace(tzinfo=NY)
        end = datetime.strptime(w["endTime"], "%Y-%m-%d %H:%M").replace(tzinfo=NY)
        noon = d.replace(hour=12, minute=0, second=0, microsecond=0)
        if start <= noon < end:
            return True
    return False


def regime_at(now: datetime, phase: str | None, calendar: dict[str, Any] | None) -> Regime:
    local = now.astimezone(NY)
    if _closed_day(local, calendar):
        return Regime.FROZEN_REFERENCE
    if phase is None:
        return Regime.UNKNOWN
    return Regime.FROZEN_REFERENCE if phase == "overnight" else Regime.LIVE_REFERENCE


def last_extended_close(now: datetime, calendar: dict[str, Any] | None) -> datetime:
    """Most recent 20:00 New York boundary that ended a trading day (skips weekend/holiday days)."""
    local = now.astimezone(NY)
    day = local if local.time() >= EXTENDED_CLOSE else local - timedelta(days=1)
    for _ in range(14):
        if not _closed_day(day, calendar):
            return day.replace(hour=20, minute=0, second=0, microsecond=0)
        day -= timedelta(days=1)
    raise ValueError("no trading day found in the last two weeks")


# ---- liquidity -------------------------------------------------------------------------------

@dataclass(frozen=True)
class BookStats:
    best_bid: Decimal | None
    best_ask: Decimal | None
    spread_bps: Decimal | None
    depth_usd_50bps: Decimal | None  # bid + ask notional within 50 bps of mid


def book_stats(bids: list[list[Any]], asks: list[list[Any]]) -> BookStats:
    if not bids or not asks:
        return BookStats(None, None, None, None)
    bid, ask = Decimal(str(bids[0][0])), Decimal(str(asks[0][0]))
    mid = (bid + ask) / 2
    if mid <= 0:
        return BookStats(bid, ask, None, None)
    lo, hi = mid * Decimal("0.995"), mid * Decimal("1.005")
    depth = sum((Decimal(str(p)) * Decimal(str(q)) for p, q in bids if Decimal(str(p)) >= lo), Decimal(0)) + \
        sum((Decimal(str(p)) * Decimal(str(q)) for p, q in asks if Decimal(str(p)) <= hi), Decimal(0))
    return BookStats(bid, ask, (ask - bid) / mid * 10000, depth)


def percentile_rank(value: Decimal, history: list[Decimal]) -> Decimal | None:
    if not history:
        return None
    below = sum(1 for h in history if h < value)
    equal = sum(1 for h in history if h == value)
    return Decimal(below + equal / 2) / len(history) * 100


def classify_quality(stats: BookStats, spread_hist: list[Decimal], depth_hist: list[Decimal]) -> tuple[Quality, list[str], str]:
    """HIGH/MEDIUM/LOW from this asset's own captured history (§24). Returns quality, reasons, support label."""
    if stats.spread_bps is None or stats.depth_usd_50bps is None:
        return Quality.UNAVAILABLE, ["BOOK_EMPTY"], "NONE"
    if len(spread_hist) < MIN_HISTORY_OBS:
        return Quality.UNAVAILABLE, [f"HISTORY_SHORT_{len(spread_hist)}_OF_{MIN_HISTORY_OBS}_OBS"], "LOW"
    sp = percentile_rank(stats.spread_bps, spread_hist)
    dp = percentile_rank(stats.depth_usd_50bps, depth_hist)
    reasons = [f"SPREAD_P{int(sp)}", f"DEPTH50_P{int(dp)}"]
    if sp >= 90 or dp <= 10:
        return Quality.LOW, reasons, "OWN_HISTORY"
    if sp <= 50 and dp >= 50:
        return Quality.HIGH, reasons, "OWN_HISTORY"
    return Quality.MEDIUM, reasons, "OWN_HISTORY"


# ---- envelope ---------------------------------------------------------------------------------

@dataclass(frozen=True)
class RealityInputs:
    symbol: str
    underlying: str
    weekend_tradable: bool | None
    now: datetime
    phase: str | None
    calendar: dict[str, Any] | None
    live_price: Decimal | None
    live_ts: datetime | None
    reference_price: Decimal | None          # rToken close at the boundary (proxy, A1)
    reference_time: datetime | None
    perp_symbol: str | None
    perp_now: Decimal | None                 # perp index (or mark) now
    perp_at_reference: Decimal | None        # perp close at the boundary
    book: BookStats
    spread_history: list[Decimal]
    depth_history: list[Decimal]
    closure_windows_captured: int
    event_status: str                        # PRIMARY_EVENT / NO_FILING / NOT_APPLICABLE / UNAVAILABLE: ...
    sources: dict[str, str] = field(default_factory=dict)
    reference_source: str = "rToken close at boundary"
    underlying_last: Decimal | None = None   # underlying's latest trade incl. pre/post market
    underlying_last_ts: datetime | None = None
    events: tuple[str, ...] = ()             # human-readable primary-source events in the window
    # Agreement thresholds: hand-set defaults until calibration.py learns them from captured history.
    agree_bps: Decimal = AGREE_BPS
    conflict_bps: Decimal = CONFLICT_BPS
    threshold_source: str = "DEFAULT"


@dataclass(frozen=True)
class RealityEnvelope:
    symbol: str
    underlying: str
    regime: Regime
    weekend_tradable: bool | None
    live_price: Decimal | None
    live_age_seconds: int | None
    reference_price: Decimal | None
    reference_time: str | None
    reference_age_seconds: int | None
    move: Decimal | None                     # live / reference − 1
    perp_symbol: str | None
    perp_move: Decimal | None
    reference_gap_bps: Decimal | None        # rToken vs perp-implied price
    reference_agreement: str                 # AGREE / DISAGREE / CONFLICT / UNAVAILABLE
    agreement_reference: str | None          # which independent reference the agreement used
    market_quality: Quality
    liquidity: BookStats
    evidence_mode: EvidenceMode
    market_state: MarketState
    reality_center: Decimal | None           # uncalibrated: median of available implied moves
    lower_stress_bound: Decimal | None       # None until BAND_MIN_WINDOWS realized reopens exist
    upper_stress_bound: Decimal | None
    data_support: str
    event_support: str
    events: tuple[str, ...]
    reference_source: str
    underlying_last: Decimal | None
    threshold_source: str
    reason_codes: tuple[str, ...]
    assumptions: tuple[str, ...]
    sources: dict[str, str]
    version: str = ENGINE_VERSION


def _median(values: list[Decimal]) -> Decimal | None:
    v = sorted(values)
    if not v:
        return None
    m = len(v) // 2
    return v[m] if len(v) % 2 else (v[m - 1] + v[m]) / 2


def build_envelope(i: RealityInputs) -> RealityEnvelope:
    reasons: list[str] = []
    regime = regime_at(i.now, i.phase, i.calendar)
    reasons.append(f"REGIME_{regime}")

    live_age = int((i.now - i.live_ts).total_seconds()) if i.live_ts else None
    live_usable = i.live_price is not None and live_age is not None and live_age <= STALE_SECONDS
    if not live_usable:
        reasons.append("LIVE_MARKET_STALE_OR_MISSING")
    if i.weekend_tradable is False and regime is Regime.FROZEN_REFERENCE and i.now.astimezone(NY).weekday() >= 5:
        reasons.append("NOT_WEEKEND_TRADABLE")
        live_usable = False

    ref_age = int((i.now - i.reference_time).total_seconds()) if i.reference_time else None
    frozen = regime is Regime.FROZEN_REFERENCE
    # A move against the frozen reference only exists while the collateral index is frozen.
    move = (i.live_price / i.reference_price - 1) if frozen and live_usable and i.reference_price else None
    perp_move = (i.perp_now / i.perp_at_reference - 1) if frozen and i.perp_now and i.perp_at_reference else None

    gap_bps = None
    agreement = "UNAVAILABLE"
    agreement_ref = None
    underlying_age = int((i.now - i.underlying_last_ts).total_seconds()) if i.underlying_last_ts else None
    underlying_fresh = i.underlying_last is not None and underlying_age is not None and underlying_age <= UNDERLYING_FRESH_SECONDS
    if move is not None and perp_move is not None and i.reference_price:
        implied = i.reference_price * (1 + perp_move)
        gap_bps = (i.live_price / implied - 1) * 10000
        agreement = "AGREE" if abs(gap_bps) <= i.agree_bps else "CONFLICT" if abs(gap_bps) > i.conflict_bps else "DISAGREE"
        agreement_ref = f"{i.perp_symbol} move since reference"
        reasons.append(f"REFERENCE_{agreement}_{int(gap_bps)}BPS")
    elif not frozen and live_usable and (underlying_fresh or i.perp_now):
        # Live regime: compare price levels with the stock itself when its trade is fresh, else the stock perp,
        # and only when both trade on the same 1:1 scale (a gap over 5% means the share ratio is unverified).
        other = i.underlying_last if underlying_fresh else i.perp_now
        agreement_ref = f"{i.underlying} underlying (Yahoo)" if underlying_fresh else f"{i.perp_symbol} index"
        level_gap = (i.live_price / other - 1) * 10000
        if abs(level_gap) <= NORMALIZATION_LIMIT_BPS:
            gap_bps = level_gap
            agreement = "AGREE" if abs(gap_bps) <= i.agree_bps else "CONFLICT" if abs(gap_bps) > i.conflict_bps else "DISAGREE"
            reasons.append(f"LIVE_REFERENCE_{agreement}_{int(gap_bps)}BPS")
        else:
            agreement = "NORMALIZATION_UNVERIFIED"
            reasons.append("REFERENCE_SCALE_DIFFERS")
    elif i.perp_symbol is None:
        reasons.append("NO_INDEPENDENT_REFERENCE")

    quality, q_reasons, q_support = classify_quality(i.book, i.spread_history, i.depth_history)
    reasons += q_reasons

    if not live_usable:
        mode = EvidenceMode.INFERRED
    elif quality in (Quality.HIGH, Quality.MEDIUM) and agreement == "AGREE":
        mode = EvidenceMode.OBSERVED
    else:
        mode = EvidenceMode.HYBRID

    if regime is Regime.LIVE_REFERENCE:
        state = MarketState.NOT_APPLICABLE
    elif move is None:
        state = MarketState.INSUFFICIENT_DATA
    elif quality is Quality.LOW:
        state = MarketState.THIN
    elif agreement == "CONFLICT":
        state = MarketState.CONFLICT
    elif abs(move) < MIN_MOVE_FOR_STATE:
        state = MarketState.DRIFT
        reasons.append("MOVE_BELOW_0.5PCT")
    elif perp_move is None and i.event_status == "PRIMARY_EVENT" and quality is not Quality.UNAVAILABLE:
        state = MarketState.DISCOVERY
        reasons.append("EVENT_SUPPORTED")
    elif perp_move is None or quality is Quality.UNAVAILABLE:
        state = MarketState.DRIFT
        reasons.append("MOVE_UNCORROBORATED")
    elif perp_move != 0 and (move > 0) == (perp_move > 0) and abs(move) > OVERSHOOT_RATIO * abs(perp_move):
        state = MarketState.OVERSHOOT
    elif agreement == "AGREE":
        state = MarketState.DISCOVERY
    else:
        state = MarketState.DRIFT
    reasons.append(f"STATE_{state}")
    reasons.append(f"THRESHOLDS_{i.threshold_source}")

    center = _median([m for m in (move, perp_move) if m is not None])
    windows = i.closure_windows_captured
    support = "LOW" if windows < 3 else "MODERATE" if windows < BAND_MIN_WINDOWS else "HIGH"
    reasons.append(f"BAND_UNAVAILABLE_{windows}_OF_{BAND_MIN_WINDOWS}_CLOSURE_WINDOWS")

    return RealityEnvelope(
        symbol=i.symbol, underlying=i.underlying, regime=regime, weekend_tradable=i.weekend_tradable,
        live_price=i.live_price, live_age_seconds=live_age,
        reference_price=i.reference_price, reference_time=i.reference_time.isoformat() if i.reference_time else None,
        reference_age_seconds=ref_age, move=move, perp_symbol=i.perp_symbol, perp_move=perp_move,
        reference_gap_bps=gap_bps, reference_agreement=agreement, agreement_reference=agreement_ref,
        market_quality=quality, liquidity=i.book,
        evidence_mode=mode, market_state=state, reality_center=center if regime is Regime.FROZEN_REFERENCE else None,
        lower_stress_bound=None, upper_stress_bound=None,
        data_support=f"closure windows {windows} ({support}); liquidity history {q_support}",
        event_support=i.event_status, events=i.events, reference_source=i.reference_source,
        underlying_last=i.underlying_last, threshold_source=i.threshold_source,
        reason_codes=tuple(reasons + [f"EVENT_{i.event_status.split(':')[0]}"]), assumptions=ASSUMPTIONS, sources=i.sources,
    )
