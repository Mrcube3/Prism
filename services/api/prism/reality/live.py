"""Gather Reality Engine inputs from Bitget's public market data (read-only)."""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from ..connectors.bitget import BitgetClient
from ..connectors.bitget_data import BitgetDataMCP
from ..connectors.qwen import QwenClient
from ..connectors.us_reference import news, sec, yahoo
from ..provenance import utc_now
from .engine import BookStats, RealityEnvelope, RealityInputs, book_stats, build_envelope, last_extended_close
from .history import closure_windows, liquidity_history

_CACHE: dict[str, tuple[float, Any]] = {}


def _cached(key: str, ttl: float, build):
    hit = _CACHE.get(key)
    if hit and time.monotonic() - hit[0] < ttl:
        return hit[1]
    value = build()
    _CACHE[key] = (time.monotonic(), value)
    return value


def stock_info(client: BitgetClient) -> dict[str, dict[str, Any]]:
    def build():
        r = client.get("/api/v3/reality/market/stock-info")
        return {row["symbol"]: row for row in (r.data or [])} if r.ok else {}
    return _cached("stock-info", 3600, build)


def session(client: BitgetClient) -> tuple[dict | None, dict | None]:
    def build():
        s = client.get("/api/v3/reality/market/states")
        c = client.get("/api/v3/reality/market/calendar")
        states = s.data if isinstance(s.data, dict) else (s.data[0] if s.ok and s.data else None)
        return states, (c.data if c.ok and isinstance(c.data, dict) else None)
    return _cached("session", 900, build)


def perp_for(client: BitgetClient, underlying: str) -> str | None:
    def build():
        symbol = f"{underlying}USDT"
        r = client.get("/api/v3/market/instruments", {"category": "USDT-FUTURES", "symbol": symbol})
        row = r.data[0] if r.ok and r.data else {}
        return symbol if row.get("symbolType") == "stock" else None
    return _cached(f"perp:{underlying}", 3600, build)


def close_at(client: BitgetClient, category: str, symbol: str, boundary: datetime) -> tuple[Decimal | None, str]:
    """Close of the 1H candle ending at `boundary` (its open time is boundary − 1h)."""
    def build():
        r = client.get("/api/v3/market/candles", {"category": category, "symbol": symbol, "interval": "1H", "limit": "200"})
        if not r.ok or not r.data:
            return None, f"unavailable: {r.error or r.msg}"
        target = int((boundary - timedelta(hours=1)).timestamp() * 1000)
        row = next((c for c in r.data if int(c[0]) == target), None)
        return (Decimal(row[4]) if row else None), f"bitget:{r.endpoint} candle open {target}"
    return _cached(f"close:{category}:{symbol}:{boundary.isoformat()}", 600, build)


def _phase(states: dict | None, now: datetime) -> str | None:
    from ..capture.session import phase_at, NY

    return phase_at(now.astimezone(NY), states.get("stateList") or []) if states else None


def bitget_us_data_status() -> str:
    def build():
        result = BitgetDataMCP(timeout=10).query("equity_calendar", {"symbol": "NVDA"})
        return "AVAILABLE" if result.ok else f"UNAVAILABLE ({result.detail})"
    return _cached("bitget-us-data", 900, build)


NEWS_SUPPORT_MATERIALITY = ("high", "medium")


def events_for(underlying: str, reference_time: datetime, name: str | None = None,
               allow_qwen: bool = False) -> tuple[str, tuple[str, ...], str]:
    """Event evidence since the regular close: SEC 8-K filings (primary) and Qwen-classified headlines (secondary).

    Status: PRIMARY_EVENT (8-K) > NEWS_EVENT (a relevant, company-specific headline of high/medium
    materiality published inside the window) > NO_FILING / NOT_APPLICABLE / UNAVAILABLE.
    """
    def build():
        since = sec.event_window_start(reference_time)
        status, filings = sec.recent_8k(underlying, since)
        lines = [f"SEC {f.description} · accepted {f.accepted_at.strftime('%Y-%m-%d %H:%M')} UTC" for f in filings[:3]]
        items = news.headlines(underlying, name)
        classifier = QwenClient(timeout=180).classify_headlines if allow_qwen else None
        classes, news_status = news.classified(underlying, items, classifier)
        supported = False
        for h in items:
            c = classes.get(h.id)
            tag = (f"{c['event_type']}, {c['direction']}, {c['materiality']}" + ("" if c["relevant"] else ", not about this company")
                   if c else "classification pending")
            lines.append(f"NEWS {h.title} · {h.publisher} · {h.published_at.strftime('%Y-%m-%d %H:%M')} UTC · {tag}")
            if c and c["relevant"] and c["event_type"] == "company_specific" and c["materiality"] in NEWS_SUPPORT_MATERIALITY and h.published_at >= since:
                supported = True
        sources = f"{sec.SOURCE}; {news.SOURCE}, classified by Qwen ({news_status})"
        if status == "OK" and filings:
            return "PRIMARY_EVENT", tuple(lines), sources
        if supported:
            return "NEWS_EVENT", tuple(lines), sources
        if status != "OK":
            return status, tuple(lines), sources
        return "NO_FILING", tuple(lines), sources
    return _cached(f"events:{underlying}:{reference_time.isoformat()}:{allow_qwen}", 900, build)


def underlying_reference(underlying: str, boundary: datetime) -> tuple[Decimal | None, datetime | None, Decimal | None, datetime | None, str]:
    """(extended close at/before boundary, its time, latest trade, its time, source) from Yahoo; Nones if unavailable."""
    try:
        data = yahoo.series(underlying)
    except yahoo.UnderlyingUnavailable as exc:
        return None, None, None, None, str(exc)
    at = data.at_or_before(boundary)
    # Only accept a trade from the session that ended at the boundary (within its 16-hour trading day).
    if at and boundary - at[0] > timedelta(hours=16):
        at = None
    last = data.last()
    return (at[1] if at else None), (at[0] if at else None), (last[1] if last else None), (last[0] if last else None), data.source


def envelope(client: BitgetClient, symbol: str, capture_root: Path, allow_qwen: bool = False) -> RealityEnvelope:
    now = utc_now()
    info = stock_info(client).get(symbol, {})
    underlying = info.get("code") or symbol[1:-4]
    states, calendar = session(client)
    boundary = last_extended_close(now, calendar)

    t = client.get("/api/v3/market/tickers", {"category": "SPOT", "symbol": symbol})
    row = t.data[0] if t.ok and t.data else {}
    live = Decimal(row["lastPrice"]) if row.get("lastPrice") else None
    live_ts = datetime.fromtimestamp(int(row["ts"]) / 1000, tz=timezone.utc) if row.get("ts") else None

    # Same depth (15 levels) as the captured history, so percentiles compare like with like.
    b = client.get("/api/v3/market/orderbook", {"category": "SPOT", "symbol": symbol, "limit": "15"})
    stats = book_stats(b.data.get("b") or [], b.data.get("a") or []) if b.ok and isinstance(b.data, dict) else BookStats(None, None, None, None)

    u_ref, u_ref_t, u_last, u_last_t, u_src = underlying_reference(underlying, boundary.astimezone(timezone.utc))
    if u_ref is not None:
        ref, ref_src, ref_kind = u_ref, f"{u_src}: {underlying} last trade {u_ref_t.isoformat()}", f"{underlying} extended-hours close (Yahoo)"
    else:
        ref, ref_src = close_at(client, "SPOT", symbol, boundary)
        ref_kind = "rToken close at boundary (underlying unavailable)"
    perp = perp_for(client, underlying)
    perp_now = perp_ref = None
    perp_src = "no stock perp for this underlying"
    if perp:
        pt = client.get("/api/v3/market/tickers", {"category": "USDT-FUTURES", "symbol": perp})
        prow = pt.data[0] if pt.ok and pt.data else {}
        perp_now = Decimal(prow.get("indexPrice") or prow.get("markPrice")) if (prow.get("indexPrice") or prow.get("markPrice")) else None
        perp_ref, perp_src = close_at(client, "USDT-FUTURES", perp, boundary)

    liq = liquidity_history(capture_root).get(symbol, {"spread": [], "depth": []})
    ev_status, ev_items, ev_src = events_for(underlying, boundary.astimezone(timezone.utc), info.get("name"), allow_qwen)
    from .backfill import load_band
    from .calibration import thresholds
    th = thresholds(capture_root)
    q05, q95, band_source, _ = load_band(capture_root.parent / "research", symbol)
    weekend = info.get("weekendTradable")
    return build_envelope(RealityInputs(
        symbol=symbol, underlying=underlying, weekend_tradable=None if weekend is None else weekend == "yes",
        now=now, phase=_phase(states, now), calendar=calendar, live_price=live, live_ts=live_ts,
        reference_price=ref, reference_time=boundary.astimezone(timezone.utc), perp_symbol=perp,
        perp_now=perp_now, perp_at_reference=perp_ref, book=stats,
        spread_history=liq["spread"], depth_history=liq["depth"],
        closure_windows_captured=closure_windows(capture_root).get(symbol, 0),
        event_status=ev_status, events=ev_items, reference_source=ref_kind,
        underlying_last=u_last, underlying_last_ts=u_last_t,
        agree_bps=th.agree_bps, conflict_bps=th.conflict_bps, threshold_source=th.source,
        band_q05=q05, band_q95=q95, band_source=band_source,
        sources={"live": f"bitget:{t.endpoint}", "book": f"bitget:{b.endpoint}", "reference_proxy": ref_src,
                 "underlying": u_src, "perp_reference": perp_src, "events": ev_src,
                 "bitget_us_data": bitget_us_data_status(),
                 "liquidity_history": "PRISM capture (public order book, 15 levels)"},
    ))


def series(client: BitgetClient, symbol: str, minutes: int = 240) -> dict[str, Any]:
    """Recent 1m closes for the rToken and its stock perp (real Bitget candles)."""
    out: dict[str, Any] = {}
    info = stock_info(client).get(symbol, {})
    underlying = info.get("code") or symbol[1:-4]
    for key, category, sym in (("rtoken", "SPOT", symbol), ("perp", "USDT-FUTURES", perp_for(client, underlying))):
        if not sym:
            out[key] = None
            continue
        r = client.get("/api/v3/market/candles", {"category": category, "symbol": sym, "interval": "1m", "limit": str(minutes)})
        out[key] = {"symbol": sym, "source": f"bitget:{r.endpoint}", "retrieved_at": r.request_ts.isoformat(),
                    "points": [[int(c[0]), c[4]] for c in (r.data or [])] if r.ok else []}
    return out
