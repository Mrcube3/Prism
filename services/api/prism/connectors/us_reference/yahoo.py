"""Underlying U.S. stock reference from Yahoo Finance's public chart endpoint (unofficial).

Same backup NEXUS uses (`C:/bitgets2/src/bitget/stock_reference.py`) while Bitget's US-stock MCP
returns 503. It is NOT a Bitget price: it may be delayed and its terms are Yahoo's. Every value
carries the provider's own trade timestamp; freshness is decided by the caller.

Used for: (1) the underlying's last extended-hours trade at or before the 20:00 New York
boundary, the closest observable stand-in for the close Bitget documents freezing the collateral
index at; (2) the underlying's latest trade (incl. pre/post market) as an independent reference.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

import httpx

SOURCE = "Yahoo Finance chart (unofficial; potentially delayed)"
ENDPOINT = "https://query1.finance.yahoo.com/v8/finance/chart/"
_CACHE: dict[str, tuple[float, "UnderlyingSeries"]] = {}
_BACKOFF: dict[str, float] = {}
TTL = 60
BACKOFF = 300


@dataclass(frozen=True)
class UnderlyingSeries:
    symbol: str
    points: tuple[tuple[datetime, Decimal], ...]  # (trade minute UTC, close), regular + pre/post market
    regular_price: Decimal | None
    regular_time: datetime | None
    retrieved_at: datetime
    source: str = SOURCE

    def last(self) -> tuple[datetime, Decimal] | None:
        return self.points[-1] if self.points else None

    def at_or_before(self, moment: datetime) -> tuple[datetime, Decimal] | None:
        prior = [p for p in self.points if p[0] <= moment]
        return prior[-1] if prior else None


class UnderlyingUnavailable(Exception):
    pass


def parse(payload: dict, symbol: str) -> UnderlyingSeries:
    try:
        result = payload["chart"]["result"][0]
        meta = result["meta"]
        if meta.get("symbol") != symbol or meta.get("currency") != "USD":
            raise ValueError("symbol or currency mismatch")
        stamps = result.get("timestamp") or []
        closes = result["indicators"]["quote"][0]["close"]
        points = tuple(
            (datetime.fromtimestamp(t, timezone.utc), Decimal(str(c)))
            for t, c in zip(stamps, closes) if c is not None and c > 0
        )
        reg = meta.get("regularMarketPrice")
        reg_t = meta.get("regularMarketTime")
        return UnderlyingSeries(
            symbol, points, Decimal(str(reg)) if reg else None,
            datetime.fromtimestamp(reg_t, timezone.utc) if reg_t else None, datetime.now(timezone.utc),
        )
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise UnderlyingUnavailable(f"unexpected chart payload: {exc}") from exc


def series(symbol: str, client: httpx.Client | None = None) -> UnderlyingSeries:
    """Last 5 days of 1-minute closes including pre/post market. Cached 60 s; 5-minute backoff on failure."""
    now = time.monotonic()
    hit = _CACHE.get(symbol)
    if hit and now - hit[0] < TTL:
        return hit[1]
    if now < _BACKOFF.get(symbol, 0):
        raise UnderlyingUnavailable("Yahoo Finance in temporary backoff")
    own = client is None
    http = client or httpx.Client(timeout=15, headers={"User-Agent": "PRISM read-only research workbench"})
    try:
        r = http.get(ENDPOINT + symbol, params={"range": "5d", "interval": "1m", "includePrePost": "true"})
        r.raise_for_status()
        data = parse(r.json(), symbol)
    except (httpx.HTTPError, ValueError, UnderlyingUnavailable) as exc:
        _BACKOFF[symbol] = now + BACKOFF
        raise UnderlyingUnavailable(f"Yahoo Finance unavailable: {type(exc).__name__}") from exc
    finally:
        if own:
            http.close()
    _CACHE[symbol] = (now, data)
    return data
