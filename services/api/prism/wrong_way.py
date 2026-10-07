"""Wrong-Way Collateral (AGENTS.md §33, M7).

For each derivative position, estimate how each collateral asset moved with that position's
risk factor historically (OLS beta and correlation of daily log returns on dates both series
traded). Collateral is wrong-way when it tends to fall in the same shock that hurts the position:
beta > 0 against a long's factor, beta < 0 against a short's.

Small-data rule (§52): fewer than MIN_OBS paired days, or |corr| below MIN_CORR, is reported as
INSUFFICIENT_DATA / NOT_SIGNIFICANT, never as wrong-way.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from decimal import Decimal

from .connectors.bitget import BitgetClient
from .shadow import Factor, Holding, PerpPosition

MIN_OBS = 30
MIN_CORR = 0.3
VERSION = "prism-wrongway-v0.1"


@dataclass(frozen=True)
class Sensitivity:
    coin: str
    factor_symbol: str
    beta: float | None
    corr: float | None
    observations: int
    status: str  # WRONG_WAY | RIGHT_WAY | NOT_SIGNIFICANT | INSUFFICIENT_DATA | UNAVAILABLE
    collateral_value: Decimal
    source: str


@dataclass(frozen=True)
class WrongWayReport:
    position: str
    factor_symbol: str
    sensitivities: tuple[Sensitivity, ...]
    wrong_way_share: Decimal  # of collateral-enabled value
    wrong_way_assets: tuple[str, ...]
    version: str = VERSION


_CLOSES: dict[tuple[str, str, int], tuple[float, dict[int, float], str]] = {}
CLOSES_TTL_SECONDS = 3600  # daily candles; refreshing hourly is ample


def daily_closes(client: BitgetClient, category: str, symbol: str, limit: int = 120) -> tuple[dict[int, float], str]:
    key = (category, symbol, limit)
    hit = _CLOSES.get(key)
    if hit and time.monotonic() - hit[0] < CLOSES_TTL_SECONDS:
        return hit[1], hit[2]
    closes, source = _fetch_daily_closes(client, category, symbol, limit)
    if closes:
        _CLOSES[key] = (time.monotonic(), closes, source)
    return closes, source


def _fetch_daily_closes(client: BitgetClient, category: str, symbol: str, limit: int) -> tuple[dict[int, float], str]:
    response = client.get("/api/v3/market/candles", {"category": category, "symbol": symbol, "interval": "1D", "limit": str(limit)})
    if not response.ok or not response.data:
        return {}, f"unavailable: {response.error or response.msg}"
    return {int(row[0]): float(row[4]) for row in response.data}, f"bitget:{response.endpoint} @ {response.request_ts.isoformat()}"


def log_returns(closes: dict[int, float]) -> dict[int, float]:
    days = sorted(closes)
    return {d: math.log(closes[d] / closes[p]) for p, d in zip(days, days[1:]) if closes[p] > 0 and closes[d] > 0}


def beta_corr(x: dict[int, float], y: dict[int, float]) -> tuple[float | None, float | None, int]:
    """Beta of y on x and their correlation, over shared dates."""
    keys = sorted(set(x) & set(y))
    n = len(keys)
    if n < 3:
        return None, None, n
    xs, ys = [x[k] for k in keys], [y[k] for k in keys]
    mx, my = sum(xs) / n, sum(ys) / n
    cov = sum((a - mx) * (b - my) for a, b in zip(xs, ys)) / (n - 1)
    vx = sum((a - mx) ** 2 for a in xs) / (n - 1)
    vy = sum((b - my) ** 2 for b in ys) / (n - 1)
    if vx == 0 or vy == 0:
        return None, None, n
    return cov / vx, cov / math.sqrt(vx * vy), n


def classify(beta: float | None, corr: float | None, n: int, direction: int) -> str:
    if beta is None or corr is None or n < MIN_OBS:
        return "INSUFFICIENT_DATA"
    if abs(corr) < MIN_CORR:
        return "NOT_SIGNIFICANT"
    return "WRONG_WAY" if beta * direction > 0 else "RIGHT_WAY"


def analyze(client: BitgetClient, holdings: list[Holding], positions: list[PerpPosition]) -> list[WrongWayReport]:
    cache: dict[str, tuple[dict[int, float], str]] = {}

    def returns(category: str, symbol: str) -> tuple[dict[int, float], str]:
        if symbol not in cache:
            closes, source = daily_closes(client, category, symbol)
            cache[symbol] = (log_returns(closes), source)
        return cache[symbol]

    reports = []
    collateral = [h for h in holdings if h.collateral_enabled and h.factor is not Factor.NONE]
    total = sum((h.quantity * h.price for h in holdings if h.collateral_enabled), Decimal(0))
    for p in positions:
        factor_returns, _ = returns("USDT-FUTURES", p.symbol)
        sens = []
        for h in collateral:
            symbol = f"{h.coin}USDT"
            asset_returns, source = returns("SPOT", symbol)
            beta, corr, n = beta_corr(factor_returns, asset_returns) if asset_returns else (None, None, 0)
            status = "UNAVAILABLE" if not asset_returns else classify(beta, corr, n, p.direction)
            sens.append(Sensitivity(h.coin, p.symbol, beta, corr, n, status, h.quantity * h.price, source))
        wrong = [s for s in sens if s.status == "WRONG_WAY"]
        share = sum((s.collateral_value for s in wrong), Decimal(0)) / total if total > 0 else Decimal(0)
        reports.append(WrongWayReport(p.symbol, p.symbol, tuple(sens), share, tuple(s.coin for s in wrong)))
    return reports


CRYPTO_FACTOR_SYMBOL = "BTCUSDT"


def crypto_betas(client: BitgetClient, holdings: list[Holding]) -> dict[str, tuple[Decimal, float, int]]:
    """Significant historical betas of non-crypto collateral to BTC (factor proxy). Insignificant ones are omitted."""
    factor, _ = daily_closes(client, "USDT-FUTURES", CRYPTO_FACTOR_SYMBOL)
    factor_returns = log_returns(factor)
    out = {}
    for h in holdings:
        if h.factor is not Factor.RTOKEN:
            continue
        closes, _ = daily_closes(client, "SPOT", f"{h.coin}USDT")
        beta, corr, n = beta_corr(factor_returns, log_returns(closes))
        if beta is not None and corr is not None and n >= MIN_OBS and abs(corr) >= MIN_CORR:
            out[h.coin] = (Decimal(str(round(beta, 4))), corr, n)
    return out
