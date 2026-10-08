"""Historical closure windows and the calibrated stress band (AGENTS.md §26, §48–§52).

Rebuilds past closures from real recorded market data, using only what was knowable at forecast time:

  reference   underlying's last extended-hours trade before 20:00 New York   (Yahoo 60m pre/post bar 19:00, close)
  forecast    rToken's last Bitget price before the 04:00 New York reopen      (Bitget 1H candle 03:00, close)
              stock perp move over the same span                               (Bitget 1H candles, closes)
  realized    underlying's first trade at the reopen                           (Yahoo 60m pre-market bar 04:00, open)

Assumption (until the thaw experiment confirms it): Bitget's collateral index resumes with the
pre-market data at 04:00 New York, per its docs that U.S. trading hours include pre-market.

Methods scored (time-ordered, no shuffling, §51): A frozen reference (move 0), B live rToken move,
P PRISM centre (median of rToken and stock-perp implied moves).

Stress band: empirical 5th/95th percentiles of the residual (realized − PRISM centre). Per rToken
when it has >= MIN_ASSET windows, otherwise pooled across rTokens (hierarchical fallback, §52).
Coverage is measured walk-forward: each window's band uses only windows that closed before it.
"""

from __future__ import annotations

import json
import statistics
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx

from ..connectors.bitget import BitgetClient
from ..provenance import utc_now
from .engine import NY

MIN_ASSET = 20
MIN_GLOBAL = 30
YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/"
VERSION = "prism-backfill-v0.1"


@dataclass
class Window:
    symbol: str
    underlying: str
    boundary: str           # 20:00 New York, ISO
    reopen: str             # 04:00 New York, ISO
    kind: str               # OVERNIGHT / WEEKEND / HOLIDAY
    reference: float
    live_last: float
    live_move: float
    perp_move: float | None
    center: float
    realized_move: float
    sources: dict


def bitget_hourly(client: BitgetClient, category: str, symbol: str) -> dict[datetime, float]:
    r = client.get("/api/v3/market/candles", {"category": category, "symbol": symbol, "interval": "1H", "limit": "1000"})
    if not r.ok or not r.data:
        return {}
    return {datetime.fromtimestamp(int(c[0]) / 1000, timezone.utc): float(c[4]) for c in r.data}


def yahoo_hourly(ticker: str) -> dict[datetime, tuple[float, float]]:
    """{bar start (New York): (open, close)} for 60 days of 60m bars including pre/post market."""
    try:
        r = httpx.get(YAHOO + ticker, params={"range": "60d", "interval": "60m", "includePrePost": "true"},
                      headers={"User-Agent": "PRISM read-only research workbench"}, timeout=20)
        r.raise_for_status()
        res = r.json()["chart"]["result"][0]
        q = res["indicators"]["quote"][0]
    except (httpx.HTTPError, KeyError, IndexError, ValueError):
        return {}
    out = {}
    for t, o, c in zip(res.get("timestamp") or [], q["open"], q["close"]):
        if o and c:
            out[datetime.fromtimestamp(t, timezone.utc).astimezone(NY)] = (float(o), float(c))
    return out


def windows_for(client: BitgetClient, symbol: str, underlying: str, perp: str | None) -> list[Window]:
    ybars = yahoo_hourly(underlying)
    rtk = bitget_hourly(client, "SPOT", symbol)
    pp = bitget_hourly(client, "USDT-FUTURES", perp) if perp else {}
    if not ybars or not rtk:
        return []
    by_day: dict[Any, dict[int, tuple[float, float]]] = {}
    for start, oc in ybars.items():
        by_day.setdefault(start.date(), {})[start.hour] = oc
    days = sorted(by_day)
    out = []
    for i, day in enumerate(days):
        if 19 not in by_day[day]:
            continue
        nxt = next((d for d in days[i + 1:] if 4 in by_day[d]), None)
        if nxt is None:
            continue
        boundary = datetime(day.year, day.month, day.day, 20, tzinfo=NY)
        reopen = datetime(nxt.year, nxt.month, nxt.day, 4, tzinfo=NY)
        last_key = (reopen - timedelta(hours=1)).astimezone(timezone.utc)
        live_last = rtk.get(last_key)
        if live_last is None:
            continue
        reference = by_day[day][19][1]
        realized = by_day[nxt][4][0]
        live_move = live_last / reference - 1
        perp_move = None
        p0, p1 = pp.get((boundary - timedelta(hours=1)).astimezone(timezone.utc)), pp.get(last_key)
        if p0 and p1:
            perp_move = p1 / p0 - 1
        center = statistics.median([m for m in (live_move, perp_move) if m is not None])
        gap_days = (nxt - day).days
        kind = "OVERNIGHT" if gap_days == 1 else "WEEKEND" if gap_days == 3 and day.weekday() == 4 else "HOLIDAY"
        out.append(Window(symbol, underlying, boundary.isoformat(), reopen.isoformat(), kind, reference, live_last,
                          live_move, perp_move, center, realized / reference - 1,
                          {"reference": f"yahoo {underlying} 60m pre/post bar 19:00 close", "forecast": f"bitget {symbol} 1H candle 03:00 close",
                           "perp": f"bitget {perp} 1H closes" if perp_move is not None else None, "realized": f"yahoo {underlying} 60m bar 04:00 open"}))
    return out


def _q(values: list[float], q: float) -> float:
    v = sorted(values)
    pos = q * (len(v) - 1)
    lo, hi = int(pos), min(int(pos) + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (pos - lo)


def calibrate(windows: list[Window]) -> dict:
    windows = sorted(windows, key=lambda w: w.reopen)
    resid = lambda ws: [w.realized_move - w.center for w in ws]  # noqa: E731
    per_asset = {}
    for sym in sorted({w.symbol for w in windows}):
        r = resid([w for w in windows if w.symbol == sym])
        per_asset[sym] = {"n": len(r), "q05": _q(r, .05), "q95": _q(r, .95)} if len(r) >= MIN_ASSET else {"n": len(r)}
    all_r = resid(windows)
    pooled = {"n": len(all_r), "q05": _q(all_r, .05), "q95": _q(all_r, .95)} if len(all_r) >= MIN_GLOBAL else {"n": len(all_r)}

    # Walk-forward coverage: bands built only from windows that reopened before this one.
    hits, widths, scored = 0, [], 0
    for i, w in enumerate(windows):
        prior = [x for x in windows[:i] if x.reopen < w.boundary]
        own = [x for x in prior if x.symbol == w.symbol]
        base = own if len(own) >= MIN_ASSET else prior
        if len(base) < MIN_GLOBAL and base is prior:
            continue
        r = resid(base)
        lo, hi = w.center + _q(r, .05), w.center + _q(r, .95)
        scored += 1
        hits += lo <= w.realized_move <= hi
        widths.append(hi - lo)

    def method(pred) -> dict:
        errs = [abs(w.realized_move - pred(w)) * 100 for w in windows]
        dirs = [(pred(w) > 0) == (w.realized_move > 0) for w in windows if pred(w) != 0 and w.realized_move != 0]
        return {"n": len(errs), "mae_pp": round(statistics.mean(errs), 3) if errs else None,
                "median_ae_pp": round(statistics.median(errs), 3) if errs else None,
                "directional_accuracy": round(sum(dirs) / len(dirs), 3) if dirs else None}

    by_kind = {k: len([w for w in windows if w.kind == k]) for k in ("OVERNIGHT", "WEEKEND", "HOLIDAY")}
    return {
        "generated_at": utc_now().isoformat(), "version": VERSION, "windows": len(windows), "by_kind": by_kind,
        "per_asset": per_asset, "pooled": pooled,
        "walk_forward": {"scored": scored, "coverage": round(hits / scored, 3) if scored else None,
                         "target_coverage": 0.90, "mean_width_pp": round(statistics.mean(widths) * 100, 3) if widths else None},
        "methods": {"A_frozen_reference": method(lambda w: 0.0), "B_live_rtoken": method(lambda w: w.live_move),
                    "P_prism_centre": method(lambda w: w.center)},
        "assumption": "Bitget's collateral index resumes with pre-market data at 04:00 New York (to be confirmed by the thaw experiment).",
    }


def run(client: BitgetClient, watchlist: list[tuple[str, str, str | None]], out_dir: Path) -> dict:
    windows: list[Window] = []
    for symbol, underlying, perp in watchlist:
        windows += windows_for(client, symbol, underlying, perp)
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "closure_history.jsonl").open("w", encoding="utf-8") as f:
        for w in sorted(windows, key=lambda w: (w.reopen, w.symbol)):
            f.write(json.dumps(asdict(w)) + "\n")
    cal = calibrate(windows)
    (out_dir / "band_calibration.json").write_text(json.dumps(cal, indent=2) + "\n", encoding="utf-8")
    return cal


def load_band(out_dir: Path, symbol: str) -> tuple[Decimal | None, Decimal | None, str, int]:
    """(q05, q95, source, windows) for a symbol from band_calibration.json; Nones when not calibrated."""
    try:
        cal = json.loads((out_dir / "band_calibration.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, None, "UNCALIBRATED", 0
    asset = cal.get("per_asset", {}).get(symbol, {})
    if "q05" in asset:
        return Decimal(str(asset["q05"])), Decimal(str(asset["q95"])), f"OWN_{asset['n']}_WINDOWS", asset["n"]
    pooled = cal.get("pooled", {})
    if "q05" in pooled:
        return Decimal(str(pooled["q05"])), Decimal(str(pooled["q95"])), f"POOLED_{pooled['n']}_WINDOWS", pooled["n"]
    return None, None, "UNCALIBRATED", int(pooled.get("n", 0))
