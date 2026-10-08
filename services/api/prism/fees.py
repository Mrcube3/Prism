"""Trading fee rates with provenance (AGENTS.md §18, §38–§39). Read-only.

Source order (each result says which one was used):
  1. ACCOUNT        signed `account/all-fee-rate` for the configured key (verified 2026-10-08: returns
                    makerFeeRate / takerFeeRate per symbol, demo environment included).
  2. DERIVED        an open position's `openFeeTotal` / entry notional (observed 2026-10-07).
  3. UNAVAILABLE    nothing is invented; costs are reported as excluding fees.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from decimal import Decimal

from .connectors.bitget import BitgetClient

_CACHE: dict[tuple[str, bool], tuple[float, dict[str, tuple[Decimal, Decimal]]]] = {}
TTL = 3600


@dataclass(frozen=True)
class FeeRate:
    symbol: str
    taker: Decimal | None
    maker: Decimal | None
    source: str  # ACCOUNT / DERIVED / UNAVAILABLE plus detail

    @property
    def known(self) -> bool:
        return self.taker is not None


def _account_rates(client: BitgetClient, category: str) -> dict[str, tuple[Decimal, Decimal]]:
    key = (category, client.settings.bitget_demo)
    hit = _CACHE.get(key)
    if hit and time.monotonic() - hit[0] < TTL:
        return hit[1]
    rates: dict[str, tuple[Decimal, Decimal]] = {}
    if client.settings.has_bitget_credentials:
        r = client.get("/api/v3/account/all-fee-rate", {"category": category}, authenticated=True)
        if r.ok and isinstance(r.data, list):
            for row in r.data:
                try:
                    rates[row["symbol"]] = (Decimal(row["takerFeeRate"]), Decimal(row["makerFeeRate"]))
                except (KeyError, ValueError, ArithmeticError):
                    continue
    _CACHE[key] = (time.monotonic(), rates)
    return rates


def fee_rate(client: BitgetClient, symbol: str, category: str = "USDT-FUTURES", position_raw: dict | None = None) -> FeeRate:
    env = "demo" if client.settings.bitget_demo else "live"
    rates = _account_rates(client, category)
    if symbol in rates:
        taker, maker = rates[symbol]
        return FeeRate(symbol, taker, maker, f"ACCOUNT: bitget account/all-fee-rate ({env} key)")
    if position_raw:
        try:
            fee = abs(Decimal(str(position_raw["openFeeTotal"])))
            notional = Decimal(str(position_raw["total"])) * Decimal(str(position_raw["avgPrice"]))
            if fee > 0 and notional > 0:
                return FeeRate(symbol, fee / notional, None, "DERIVED: position openFeeTotal / entry notional")
        except (KeyError, ValueError, ArithmeticError):
            pass
    return FeeRate(symbol, None, None, "UNAVAILABLE: no account fee rate; costs exclude fees")
