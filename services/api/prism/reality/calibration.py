"""Learn Reality Engine agreement thresholds from PRISM's own captured history (AGENTS.md §24).

Source: data/capture/<date>/reality.jsonl, written every 5 minutes by the capture service. Only
live-regime readings compared against the *underlying stock* are used: there the rToken and the
stock should trade at the same price, so the spread of their gap is the normal noise level.

- AGREE  = 95th percentile of |gap| (normal noise), floored at 10 bps.
- CONFLICT = max(3 x AGREE, 99.5th percentile of |gap|).

Until MIN_OBS readings exist the hand-set defaults in engine.py are used and labelled DEFAULT.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from .engine import AGREE_BPS, CONFLICT_BPS
from .history import _lines

MIN_OBS = 500
_CACHE: dict[str, tuple[float, "Thresholds"]] = {}


@dataclass(frozen=True)
class Thresholds:
    agree_bps: Decimal
    conflict_bps: Decimal
    source: str  # DEFAULT or CALIBRATED_<n>_OBS
    observations: int


def _quantile(values: list[Decimal], q: float) -> Decimal:
    v = sorted(values)
    return v[min(len(v) - 1, int(q * (len(v) - 1) + 0.5))]


def live_gaps(root: Path, days: int = 30) -> list[Decimal]:
    gaps = []
    for row in _lines(root, "reality", days):
        e = row.get("envelope") or {}
        if e.get("regime") == "LIVE_REFERENCE" and "underlying" in str(e.get("agreement_reference") or "") and e.get("reference_gap_bps") is not None:
            gaps.append(abs(Decimal(str(e["reference_gap_bps"]))))
    return gaps


def thresholds(root: Path, days: int = 30) -> Thresholds:
    hit = _CACHE.get(str(root))
    if hit and time.monotonic() - hit[0] < 600:
        return hit[1]
    gaps = live_gaps(root, days)
    if len(gaps) < MIN_OBS:
        result = Thresholds(AGREE_BPS, CONFLICT_BPS, "DEFAULT", len(gaps))
    else:
        agree = max(Decimal(10), _quantile(gaps, 0.95))
        conflict = max(3 * agree, _quantile(gaps, 0.995))
        result = Thresholds(agree.quantize(Decimal("0.1")), conflict.quantize(Decimal("0.1")), f"CALIBRATED_{len(gaps)}_OBS", len(gaps))
    _CACHE[str(root)] = (time.monotonic(), result)
    return result
