"""Validate the Reality Engine against captured closures (AGENTS.md §47, §49–§51, M11).

    uv run --project services/api python scripts/validate_reality.py [--data-dir data]

Writes data/research/validation.json and docs/VALIDATION.md (between AUTO markers). Uses only what
the capture service recorded; nothing is backfilled. Two checks:

1. Frozen-reference proxy. During each closure, if a Bitget stock perp's index stays flat, that
   plateau is the best available observation of a Bitget-frozen reference for the underlying. It is
   compared with PRISM's proxy (the underlying's last extended-hours trade at 20:00 New York).
2. Reopen autopsy. For each closure window whose reopen was captured, the last frozen-regime
   reading is the forecast; the realized move is the underlying's first trade after the 04:00 New
   York reopen relative to the reference (pre-market data resumes the index per Bitget's docs:
   an assumption until the thaw experiment confirms it). Methods (time-ordered, no shuffling):
     A  frozen reference (move = 0)
     B  live rToken price (move = rToken move at the last closure reading)
     P  PRISM reality centre (median of the rToken and stock-perp implied moves)
   Baseline C (live price + fixed band) and interval coverage need a calibrated band (§26) and are
   reported as not yet available.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "services" / "api"))

from prism.provenance import utc_now  # noqa: E402
from prism.reality.engine import NY, last_extended_close  # noqa: E402
from prism.reality.history import _lines, next_reopen  # noqa: E402

FLAT_BPS = Decimal("1")
MIN_PLATEAU_OBS = 30
DOC = REPO_ROOT / "docs" / "VALIDATION.md"
START, END = "<!-- AUTO:validate_reality START -->", "<!-- AUTO:validate_reality END -->"


def closure_of(t: datetime) -> datetime | None:
    b = last_extended_close(t, None)
    return b if t.astimezone(NY) < next_reopen(b) else None


def proxy_check(root: Path) -> list[dict]:
    index: dict[tuple[str, datetime], list[Decimal]] = defaultdict(list)
    rtokens = {row["symbol"] for row in _lines(root, "tickers", 60) if row.get("category") == "SPOT"}
    for row in _lines(root, "tickers", 60):
        if row.get("category") != "USDT-FUTURES" or not row.get("index_price") or not row.get("meta", {}).get("ok"):
            continue
        if f"R{row['symbol']}" not in rtokens:  # only stock perps with a matching rToken (excludes BTC/ETH)
            continue
        t = datetime.fromisoformat(row["meta"]["retrieved_at"])
        b = closure_of(t)
        if b:
            index[(row["symbol"], b)].append(Decimal(str(row["index_price"])))
    proxies: dict[tuple[str, datetime], Decimal] = {}
    for row in _lines(root, "reality", 60):
        e = row.get("envelope") or {}
        if e.get("regime") == "FROZEN_REFERENCE" and e.get("reference_price") and e.get("reference_time"):
            b = datetime.fromisoformat(e["reference_time"]).astimezone(NY)
            proxies[(f"{e['underlying']}USDT", b)] = Decimal(str(e["reference_price"]))
    out = []
    for (symbol, b), values in sorted(index.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        mean = sum(values) / len(values)
        spread = (max(values) - min(values)) / mean * 10000
        flat = len(values) >= MIN_PLATEAU_OBS and spread <= FLAT_BPS
        proxy = proxies.get((symbol, b))
        out.append({
            "perp": symbol, "closure_from": b.isoformat(), "observations": len(values),
            "index_range_bps": float(round(spread, 2)), "index_flat": flat,
            "plateau": str(values[-1]) if flat else None, "prism_proxy": str(proxy) if proxy else None,
            "proxy_error_bps": float(round((proxy / values[-1] - 1) * 10000, 2)) if flat and proxy else None,
        })
    return out


def autopsy(root: Path) -> list[dict]:
    readings: dict[str, list[tuple[datetime, dict]]] = defaultdict(list)
    for row in _lines(root, "reality", 60):
        if row.get("envelope"):
            readings[row["symbol"]].append((datetime.fromisoformat(row["at"]), row["envelope"]))
    rows = []
    for symbol, series in readings.items():
        series.sort(key=lambda x: x[0])
        by_boundary: dict[str, list[tuple[datetime, dict]]] = defaultdict(list)
        for t, e in series:
            if e.get("regime") == "FROZEN_REFERENCE" and e.get("reference_time"):
                by_boundary[e["reference_time"]].append((t, e))
        for ref_time, frozen in by_boundary.items():
            reopen = next_reopen(datetime.fromisoformat(ref_time))
            after = [(t, e) for t, e in series if t >= reopen and e.get("regime") == "LIVE_REFERENCE" and e.get("underlying_last")]
            if not after:
                continue
            t_f, f = frozen[-1]
            t_r, r = after[0]
            ref = Decimal(str(f["reference_price"]))
            realized = Decimal(str(r["underlying_last"])) / ref - 1
            forecast = {"A": Decimal(0), "B": Decimal(str(f["move"])) if f.get("move") is not None else None,
                        "P": Decimal(str(f["reality_center"])) if f.get("reality_center") is not None else None}
            rows.append({
                "symbol": symbol, "reference_time": ref_time, "forecast_at": t_f.isoformat(), "realized_at": t_r.isoformat(),
                "market_state": f.get("market_state"), "evidence_mode": f.get("evidence_mode"),
                "realized_move": float(realized),
                **{f"error_{k}_pp": (float(abs(v - realized) * 100) if v is not None else None) for k, v in forecast.items()},
                **{f"direction_{k}": (None if v is None or v == 0 or realized == 0 else (v > 0) == (realized > 0)) for k, v in forecast.items()},
            })
    return sorted(rows, key=lambda r: r["reference_time"])


def summarize(rows: list[dict]) -> dict:
    out = {}
    for k in ("A", "B", "P"):
        errs = [r[f"error_{k}_pp"] for r in rows if r[f"error_{k}_pp"] is not None]
        dirs = [r[f"direction_{k}"] for r in rows if r[f"direction_{k}"] is not None]
        out[k] = {"n": len(errs), "mae_pp": round(statistics.mean(errs), 3) if errs else None,
                  "median_ae_pp": round(statistics.median(errs), 3) if errs else None,
                  "directional_accuracy": round(sum(dirs) / len(dirs), 3) if dirs else None}
    return out


def render(report: dict) -> str:
    s, p, a = report["summary"], report["proxy_check"], report["autopsy"]
    lines = [START, f"_Generated by `scripts/validate_reality.py` at {report['generated_at']}. Actual results, including negative ones (§49)._", ""]
    lines += ["### Reopen autopsy", ""]
    if not a:
        lines += ["No closure window with a captured reopen yet. Results appear here after the first reopen (04:00 New York) following a captured closure.", ""]
    else:
        lines += ["| Method | Windows | MAE (pp) | Median AE (pp) | Direction hit rate |", "| --- | --- | --- | --- | --- |"]
        for k, name in (("A", "A · frozen reference"), ("B", "B · live rToken"), ("P", "PRISM centre")):
            m = s[k]
            lines.append(f"| {name} | {m['n']} | {m['mae_pp']} | {m['median_ae_pp']} | {m['directional_accuracy']} |")
        lines += ["", "Baseline C and interval coverage: not available until a stress band is calibrated (§26).", ""]
        if s["P"]["mae_pp"] is not None and s["B"]["mae_pp"] is not None and s["P"]["mae_pp"] >= s["B"]["mae_pp"]:
            lines += ["**PRISM does not beat the live-rToken baseline on this sample.** Per §49 this is reported, not hidden.", ""]
    lines += ["### Frozen-reference proxy vs stock-perp index", ""]
    if not p:
        lines += ["No closure captured yet.", ""]
    else:
        lines += ["| Perp | Closure from (NY) | Obs | Index range (bp) | Flat? | Plateau | PRISM proxy | Proxy error (bp) |", "| --- | --- | --- | --- | --- | --- | --- | --- |"]
        for r in p[-20:]:
            lines.append(f"| {r['perp']} | {r['closure_from'][:16]} | {r['observations']} | {r['index_range_bps']} | {'yes' if r['index_flat'] else 'no'} | {r['plateau'] or '—'} | {r['prism_proxy'] or '—'} | {r['proxy_error_bps'] if r['proxy_error_bps'] is not None else '—'} |")
        lines += ["", "A non-flat index means Bitget's stock-perp index keeps moving during closures, so it cannot reveal the frozen collateral reference.", ""]
    lines.append(END)
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=REPO_ROOT / "data")
    args = parser.parse_args()
    root = args.data_dir / "capture"
    rows = autopsy(root)
    report = {"generated_at": utc_now().isoformat(), "proxy_check": proxy_check(root), "autopsy": rows, "summary": summarize(rows)}
    out = args.data_dir / "research" / "validation.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    text = DOC.read_text(encoding="utf-8") if DOC.exists() else "# Validation\n\nHow PRISM's Reality Engine is checked against reality. See `scripts/validate_reality.py` for the method.\n\n" + START + "\n" + END + "\n"
    head, _, rest = text.partition(START)
    _, _, tail = rest.partition(END)
    DOC.write_text(head + render(report) + tail, encoding="utf-8")
    print(f"autopsy windows: {len(rows)}; proxy-check groups: {len(report['proxy_check'])}; wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
