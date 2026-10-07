"""Liquidity and closure-window history from the capture service's files (docs/CAPTURE.md).

Only data PRISM itself recorded is used; nothing is backfilled. Reads data/capture/<date>/*.jsonl
and finished days' *.jsonl.gz for the last `days` days.
"""

from __future__ import annotations

import gzip
import json
import time
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Iterator

from ..provenance import utc_now
from .engine import NY, book_stats

_CACHE: dict[str, tuple[float, object]] = {}
TTL = 120


def _lines(root: Path, stream: str, days: int) -> Iterator[dict]:
    today = utc_now().date()
    for offset in range(days, -1, -1):
        day = (today - timedelta(days=offset)).isoformat()
        for path in (root / day / f"{stream}.jsonl", root / day / f"{stream}.jsonl.gz"):
            if not path.exists():
                continue
            opener = gzip.open if path.suffix == ".gz" else open
            with opener(path, "rt", encoding="utf-8") as handle:
                for line in handle:
                    try:
                        yield json.loads(line)
                    except ValueError:
                        continue


def _cached(key: str, build):
    hit = _CACHE.get(key)
    if hit and time.monotonic() - hit[0] < TTL:
        return hit[1]
    value = build()
    _CACHE[key] = (time.monotonic(), value)
    return value


def liquidity_history(root: Path, days: int = 7) -> dict[str, dict[str, list[Decimal]]]:
    """{symbol: {"spread": [...bps], "depth": [...usd within 50 bps]}} from captured books."""
    def build():
        out: dict[str, dict[str, list[Decimal]]] = defaultdict(lambda: {"spread": [], "depth": []})
        for row in _lines(root, "orderbooks", days):
            if not row.get("meta", {}).get("ok") or not row.get("bids") or not row.get("asks"):
                continue
            stats = book_stats(row["bids"], row["asks"])
            if stats.spread_bps is not None:
                out[row["symbol"]]["spread"].append(stats.spread_bps)
                out[row["symbol"]]["depth"].append(stats.depth_usd_50bps)
        return dict(out)
    return _cached(f"liq:{root}:{days}", build)


def next_reopen(boundary: datetime) -> datetime:
    """04:00 New York on the next weekday after a 20:00 boundary (weekends skipped; holidays not)."""
    day = boundary.astimezone(NY) + timedelta(days=1)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day.replace(hour=4, minute=0, second=0, microsecond=0)


def count_windows(times: list[datetime]) -> int:
    """Closure windows (one per 20:00 boundary, so a weekend counts once) observed inside and after reopen."""
    from .engine import last_extended_close

    times = sorted(times)
    if not times:
        return 0
    boundaries: set[datetime] = set()
    for t in times:
        local = t.astimezone(NY)
        b = last_extended_close(t, None)
        if local < next_reopen(b):  # inside the closure that began at b
            boundaries.add(b)
    latest = times[-1]
    return sum(1 for b in boundaries if latest >= next_reopen(b))


def closure_windows(root: Path, days: int = 30) -> dict[str, int]:
    """Per symbol: closure windows observed from inside the closure through the following reopen."""
    def build():
        seen: dict[str, list[datetime]] = defaultdict(list)
        for row in _lines(root, "tickers", days):
            if row.get("category") != "SPOT" or not row.get("meta", {}).get("ok"):
                continue
            seen[row["symbol"]].append(datetime.fromisoformat(row["meta"]["retrieved_at"]))
        return {symbol: count_windows(times) for symbol, times in seen.items()}
    return _cached(f"win:{root}:{days}", build)
