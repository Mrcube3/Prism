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


def closure_windows(root: Path, days: int = 30) -> dict[str, int]:
    """Per symbol: closure windows (overnight or weekend) observed from start through reopen.

    A window starting at a 20:00 New York boundary counts once the capture holds observations
    both inside the closure and at or after the following 04:00 New York reopen.
    """
    def build():
        seen: dict[str, set[str]] = defaultdict(set)
        for row in _lines(root, "tickers", days):
            if row.get("category") != "SPOT" or not row.get("meta", {}).get("ok"):
                continue
            at = datetime.fromisoformat(row["meta"]["retrieved_at"]).astimezone(NY)
            seen[row["symbol"]].add(at.strftime("%Y-%m-%dT%H"))
        counts: dict[str, int] = {}
        for symbol, hours in seen.items():
            days_seen = sorted({h[:10] for h in hours})
            n = 0
            for d in days_seen:
                inside = any(f"{d}T{hh:02d}" in hours for hh in (20, 21, 22, 23))
                nxt = (datetime.fromisoformat(d) + timedelta(days=1)).strftime("%Y-%m-%d")
                reopened = any(h.startswith(nxt) and int(h[11:13]) >= 4 for h in hours) or any(
                    h[:10] > nxt for h in hours)
                if inside and reopened:
                    n += 1
            counts[symbol] = n
        return counts
    return _cached(f"win:{root}:{days}", build)
