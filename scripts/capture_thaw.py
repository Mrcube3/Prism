"""Thaw experiment (AGENTS.md §47): when exactly does Bitget's rToken collateral value freeze and thaw?

    uv run --project services/api python scripts/capture_thaw.py --minutes 45     # record one window
    uv run --project services/api python scripts/capture_thaw.py --report-only    # rebuild the report

Every 10 s, for each rToken the configured account holds: the account's per-coin USD value and the
implied recognized price (usdValue / equity), next to the live rToken last price and the underlying's
latest trade (Yahoo). The implied price stays flat while the reference is frozen; the first change
after flat readings marks the thaw (and the reverse marks the freeze). Read-only.

Runs from systemd timers around 20:00 and 04:00 New York (deploy/prism-thaw-*.timer). Writes
data/capture/<date>/thaw.jsonl and the AUTO section of docs/THAW_EXPERIMENT.md. If the account holds
no rToken (e.g. a demo account, where rTokens cannot be collateral), it records that instead of guessing.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "services" / "api"))

from prism.config import load_settings  # noqa: E402
from prism.connectors.bitget import BitgetClient  # noqa: E402
from prism.connectors.us_reference import yahoo  # noqa: E402
from prism.provenance import utc_now  # noqa: E402
from prism.reality.engine import NY  # noqa: E402
from prism.reality.history import _lines  # noqa: E402
from prism.reality.live import _phase, session, stock_info  # noqa: E402

DOC = REPO_ROOT / "docs" / "THAW_EXPERIMENT.md"
START, END = "<!-- AUTO:capture_thaw START -->", "<!-- AUTO:capture_thaw END -->"
FLAT = Decimal("1e-9")


def write(row: dict) -> None:
    day = REPO_ROOT / "data" / "capture" / utc_now().strftime("%Y-%m-%d")
    day.mkdir(parents=True, exist_ok=True)
    with (day / "thaw.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, default=str) + "\n")


def observe(client: BitgetClient, rtoken_coins: dict[str, str]) -> list[dict]:
    now = utc_now()
    states, _ = session(client)
    env = "DEMO_PAPTRADING" if client.settings.bitget_demo else "LIVE"
    a = client.get("/api/v3/account/assets", authenticated=True)
    if not a.ok:
        return [{"at": now.isoformat(), "status": "ACCOUNT_UNAVAILABLE", "error": a.error or a.msg, "environment": env}]
    held = [x for x in (a.data.get("assets") or []) if str(x.get("coin", "")).upper() + "USDT" in rtoken_coins]
    if not held:
        return [{"at": now.isoformat(), "status": "NO_RTOKEN_HOLDINGS", "environment": env, "phase_ny": _phase(states, now),
                 "provider_ts": a.provider_ts.isoformat() if a.provider_ts else None}]
    rows = []
    for x in held:
        symbol = str(x["coin"]).upper() + "USDT"
        equity, usd = Decimal(str(x.get("equity") or 0)), Decimal(str(x.get("usdValue") or 0))
        t = client.get("/api/v3/market/tickers", {"category": "SPOT", "symbol": symbol})
        last = (t.data[0].get("lastPrice") if t.ok and t.data else None)
        try:
            u = yahoo.series(rtoken_coins[symbol]).last()
        except yahoo.UnderlyingUnavailable:
            u = None
        rows.append({
            "at": now.isoformat(), "status": "OBSERVED", "environment": env, "phase_ny": _phase(states, now),
            "coin": x["coin"], "symbol": symbol, "equity": str(equity), "usd_value": str(usd),
            "implied_recognized_price": str(usd / equity) if equity else None, "rtoken_last": last,
            "underlying_last": str(u[1]) if u else None, "underlying_last_at": u[0].isoformat() if u else None,
            "account_provider_ts": a.provider_ts.isoformat() if a.provider_ts else None,
        })
    return rows


def transitions(rows: list[dict]) -> list[dict]:
    """Flat→moving (thaw) and moving→flat (freeze) points of the implied recognized price, per symbol."""
    out = []
    series: dict[str, list[tuple[datetime, Decimal]]] = defaultdict(list)
    for r in rows:
        if r.get("status") == "OBSERVED" and r.get("implied_recognized_price"):
            series[r["symbol"]].append((datetime.fromisoformat(r["at"]), Decimal(r["implied_recognized_price"])))
    for symbol, pts in series.items():
        pts.sort()
        moving = [abs(b[1] / a[1] - 1) > FLAT for a, b in zip(pts, pts[1:])]
        for i in range(2, len(moving)):
            if moving[i] and not moving[i - 1] and not moving[i - 2]:
                out.append({"symbol": symbol, "event": "THAW", "first_change_at": pts[i + 1][0].isoformat(),
                            "last_flat_at": pts[i][0].isoformat(), "ny": pts[i + 1][0].astimezone(NY).strftime("%a %Y-%m-%d %H:%M:%S")})
            if not moving[i] and not moving[i - 1] and moving[i - 2]:
                out.append({"symbol": symbol, "event": "FREEZE", "flat_from": pts[i - 1][0].isoformat(),
                            "ny": pts[i - 1][0].astimezone(NY).strftime("%a %Y-%m-%d %H:%M:%S")})
    return out


def report() -> None:
    rows = list(_lines(REPO_ROOT / "data" / "capture", "thaw", 90))
    found = transitions(rows)
    observed = [r for r in rows if r.get("status") == "OBSERVED"]
    statuses = defaultdict(int)
    for r in rows:
        statuses[r.get("status")] += 1
    lines = [START, f"_Generated by `scripts/capture_thaw.py` at {utc_now().isoformat()}._", "",
             f"Polls recorded: {len(rows)} ({', '.join(f'{k}: {v}' for k, v in sorted(statuses.items()))}).", ""]
    if not observed:
        envs = sorted({r.get('environment') for r in rows if r.get('environment')})
        lines += ["**Not observable yet.** The configured account" + (f" ({', '.join(envs)})" if envs else "") +
                  " holds no rToken, so Bitget's recognized rToken collateral value cannot be watched. "
                  "Bitget's demo environment does not allow rTokens as collateral; the experiment needs a live account holding any rToken "
                  "(a small amount is enough). The recorder keeps running around every 20:00, 04:00 and 09:30 New York boundary on weekdays and will report automatically.", ""]
    elif not found:
        lines += ["rToken holdings are being observed; no freeze or thaw transition has been captured yet.", ""]
    else:
        lines += ["| rToken | Event | New York time | UTC |", "| --- | --- | --- | --- |"]
        for t in found[-40:]:
            lines.append(f"| {t['symbol']} | {t['event']} | {t['ny']} | {t.get('first_change_at') or t.get('flat_from')} |")
        lines.append("")
    lines.append(END)
    text = DOC.read_text(encoding="utf-8") if DOC.exists() else (
        "# Thaw experiment\n\nWhen does Bitget's recognized rToken collateral value freeze and resume? Method: `scripts/capture_thaw.py`.\n\n" + START + "\n" + END + "\n")
    head, _, rest = text.partition(START)
    _, _, tail = rest.partition(END)
    DOC.write_text(head + "\n".join(lines) + tail, encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=45)
    ap.add_argument("--interval", type=float, default=10)
    ap.add_argument("--report-only", action="store_true")
    args = ap.parse_args()
    if not args.report_only:
        settings = load_settings()
        with BitgetClient(settings) as client:
            if not settings.has_bitget_credentials:
                write({"at": utc_now().isoformat(), "status": "NO_CREDENTIALS"})
            else:
                coins = {s: (row.get("code") or s[1:-4]) for s, row in stock_info(client).items()}
                end = time.monotonic() + args.minutes * 60
                while True:
                    rows = observe(client, coins)
                    for r in rows:
                        write(r)
                    if rows and rows[0]["status"] == "NO_RTOKEN_HOLDINGS":
                        break  # nothing to watch in this window; one record is enough
                    if time.monotonic() >= end:
                        break
                    time.sleep(args.interval)
    report()
    print(DOC.read_text(encoding="utf-8").split(START)[1].split(END)[0].strip()[:600])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
