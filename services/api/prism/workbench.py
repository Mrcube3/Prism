"""Load a shadow-engine input set from either Bitget (observed) or the Judge Mode fixture (hypothetical).

Judge Mode (AGENTS.md §46): REAL market data + a CLEARLY LABELLED HYPOTHETICAL ACCOUNT.
Quantities are invented and labelled; every price, collateral tier and MMR rate is a real
Bitget response recorded with its source and retrieval time.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from .account import AccountSnapshot, snapshot_from_bitget
from .collateral import CollateralSchedule, TierMode, schedules_from_payload, tiered_collateral_value
from .connectors.bitget import BitgetClient
from .provenance import DataMode, Provenance, utc_now
from .reconciliation import ReconciliationResult, reconcile
from .shadow import Baseline, Factor, Holding, PerpPosition

STABLES = {"USDT", "USDC", "USDE", "PYUSD", "RLUSD", "BGUSD"}
JUDGE_FIXTURE = "judge_account.json"
# Hypothetical quantities only. Chosen to resemble the target user (AGENTS.md §2): leveraged BTC
# plus rToken collateral including crypto-sensitive names.
JUDGE_HOLDINGS = {"USDT": "12000", "RNVDA": "60", "RSPY": "10", "RCOIN": "25", "RMSTR": "30"}
# ~6x BTC notional on ~$42k effective equity: a leveraged crypto trader, the spec's target user. With Bitget's
# real BTC MMR (0.4% tier 1), margin-ratio stress comes mainly from equity erosion, not from MM growth.
JUDGE_PERPS = [{"symbol": "BTCUSDT", "direction": 1, "size": "3"}]


def is_rtoken(schedule: CollateralSchedule | None) -> bool:
    """Bitget names rTokens with a lowercase "r" prefix (rNVDA, rSPY) in discount-rate; crypto coins are upper-case."""
    return schedule is not None and len(schedule.coin) > 1 and schedule.coin[0] == "r" and schedule.coin[1].isupper()


_SYMBOL_TYPES: dict[tuple[str, bool], str | None] = {}


def perp_symbol_type(client: BitgetClient, symbol: str, demo_env: bool = False) -> str | None:
    """Bitget instrument metadata `symbolType` ("stock" / "crypto"), verified on public instruments 2026-10-06."""
    key = (symbol, demo_env)
    if key not in _SYMBOL_TYPES:
        response = client.get("/api/v3/market/instruments", {"category": "USDT-FUTURES", "symbol": symbol}, demo_env=demo_env)
        row = response.data[0] if response.ok and response.data else {}
        _SYMBOL_TYPES[key] = row.get("symbolType")
    return _SYMBOL_TYPES[key]


def perp_factor(client: BitgetClient, symbol: str, schedules: dict[str, CollateralSchedule], demo_env: bool = False) -> Factor:
    """Stock perps sit on the rToken/equity axis (AGENTS.md §31); falls back to the name rule if metadata is missing."""
    kind = perp_symbol_type(client, symbol, demo_env)
    if kind == "stock":
        return Factor.RTOKEN
    if kind == "crypto":
        return Factor.CRYPTO
    return factor_for(symbol[:-4], schedules)


def factor_for(name: str, schedules: dict[str, CollateralSchedule]) -> Factor:
    """Holding coin ("RNVDA", "BTC") or perp base ("NVDA", "BTC"). Stock perps share the rToken/equity axis (AGENTS.md §31)."""
    name = name.upper()
    if name in STABLES:
        return Factor.NONE
    if is_rtoken(schedules.get(name)) or is_rtoken(schedules.get("R" + name)):
        return Factor.RTOKEN
    return Factor.CRYPTO


@dataclass
class Workbench:
    baseline: Baseline
    holdings: list[Holding]
    positions: list[PerpPosition]
    schedules: dict[str, CollateralSchedule]
    reconciliation: ReconciliationResult | None
    snapshot: AccountSnapshot | None
    data_notes: list[str]


def fetch_schedules(client: BitgetClient, demo_env: bool = False) -> dict[str, CollateralSchedule]:
    response = client.get("/api/v3/market/discount-rate", demo_env=demo_env)
    if not response.ok:
        raise RuntimeError(f"collateral tiers unavailable: {response.error or response.msg}")
    return schedules_from_payload(response.data, f"bitget:{response.endpoint}", response.request_ts.isoformat())


def fetch_mmr_rate(client: BitgetClient, symbol: str, notional: Decimal, demo_env: bool = False) -> tuple[Decimal | None, str]:
    response = client.get("/api/v3/market/position-tier", {"category": "USDT-FUTURES", "symbol": symbol}, demo_env=demo_env)
    if not response.ok or not response.data:
        return None, f"position-tier unavailable for {symbol}"
    for tier in response.data:
        if Decimal(tier["minTierValue"]) <= notional < Decimal(tier["maxTierValue"]):
            return Decimal(tier["mmr"]), f"bitget:{response.endpoint} tier {tier['tier']} @ {response.request_ts.isoformat()}"
    return None, f"{symbol}: notional outside published tiers"


def fetch_mmr_tiers(client: BitgetClient, symbol: str, demo_env: bool = False) -> tuple[tuple[Decimal, Decimal, Decimal], ...]:
    """Published position tiers (min value, max value, mmr) for a USDT perp; empty if unavailable."""
    response = client.get("/api/v3/market/position-tier", {"category": "USDT-FUTURES", "symbol": symbol}, demo_env=demo_env)
    if not response.ok or not response.data:
        return ()
    return tuple(sorted((Decimal(t["minTierValue"]), Decimal(t["maxTierValue"]), Decimal(t["mmr"])) for t in response.data))


def stock_perp_anchor(client: BitgetClient, symbol: str) -> tuple[Decimal | None, str]:
    """Frozen reference for a stock perp's underlying while the collateral reference is frozen; None when live."""
    from datetime import timezone

    from .reality.engine import Regime, last_extended_close, regime_at
    from .reality.live import _phase, session, underlying_reference
    from .provenance import utc_now

    now = utc_now()
    states, calendar = session(client)
    if regime_at(now, _phase(states, now), calendar) is not Regime.FROZEN_REFERENCE:
        return None, "reference live: stock perp shocked from its mark"
    boundary = last_extended_close(now, calendar).astimezone(timezone.utc)
    ref, _, _, _, source = underlying_reference(symbol[:-4], boundary)
    return ref, (f"frozen reference ({source})" if ref is not None else "frozen reference unavailable: shocked from mark")


def with_perp_context(client: BitgetClient, p: PerpPosition, demo_env: bool = False) -> PerpPosition:
    """Attach published tiers and, for stock perps during a closure, the frozen-reference anchor."""
    from dataclasses import replace

    tiers = fetch_mmr_tiers(client, p.symbol, demo_env)
    anchor, note = (stock_perp_anchor(client, p.symbol) if p.factor is Factor.RTOKEN else (None, ""))
    return replace(p, mmr_tiers=tiers, anchor_price=anchor, mark_source=p.mark_source + (f"; {note}" if note else ""))


def fetch_price(client: BitgetClient, category: str, symbol: str, demo_env: bool = False) -> tuple[Decimal, str]:
    response = client.get("/api/v3/market/tickers", {"category": category, "symbol": symbol}, demo_env=demo_env)
    if not response.ok or not response.data:
        raise RuntimeError(f"no ticker for {symbol}: {response.error or response.msg}")
    row = response.data[0]
    field = "markPrice" if category == "USDT-FUTURES" and row.get("markPrice") else "lastPrice"
    return Decimal(row[field]), f"bitget:{response.endpoint} {field} ts={row.get('ts')}"


# ---- Judge Mode ------------------------------------------------------------------------------

def build_judge_fixture(client: BitgetClient, path: Path) -> dict[str, Any]:
    holdings = []
    for coin, qty in JUDGE_HOLDINGS.items():
        if coin in STABLES:
            holdings.append({"coin": coin, "quantity": qty, "price": "1", "price_source": "stablecoin at par (assumption)"})
            continue
        price, source = fetch_price(client, "SPOT", f"{coin}USDT")
        holdings.append({"coin": coin, "quantity": qty, "price": str(price), "price_source": source})
    perps = []
    for p in JUDGE_PERPS:
        mark, source = fetch_price(client, "USDT-FUTURES", p["symbol"])
        perps.append({**p, "mark_price": str(mark), "mark_source": source})
    fixture = {
        "label": "HYPOTHETICAL ACCOUNT",
        "note": "Quantities are hypothetical. Prices, collateral tiers and MMR tiers are real Bitget responses.",
        "recognized_price_note": "UNVERIFIED_ASSUMPTION: rToken last price at build time stands in for Bitget's recognized collateral price.",
        "built_at": utc_now().isoformat(),
        "holdings": holdings,
        "perps": perps,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(fixture, indent=2) + "\n", encoding="utf-8")
    return fixture


def judge_workbench(client: BitgetClient, fixture: dict[str, Any], tier_mode: TierMode = TierMode.MARGINAL) -> Workbench:
    schedules = fetch_schedules(client)
    holdings = []
    for h in fixture["holdings"]:
        coin = h["coin"].upper()
        schedule = schedules.get(coin)
        holdings.append(Holding(coin, Decimal(h["quantity"]), Decimal(h["price"]), factor_for(coin, schedules), schedule is not None, schedule, h["price_source"]))
    positions = []
    for p in fixture["perps"]:
        size, mark = Decimal(p["size"]), Decimal(p["mark_price"])
        mmr, mmr_source = fetch_mmr_rate(client, p["symbol"], size * mark)
        positions.append(with_perp_context(client, PerpPosition(p["symbol"], int(p["direction"]), size, mark, perp_factor(client, p["symbol"], schedules), mmr, f"{p['mark_source']}; mmr {mmr_source}")))
    eff = sum((tiered_collateral_value(h.quantity * h.price, h.schedule, tier_mode) for h in holdings if h.schedule), Decimal(0))
    mm = sum((abs(p.size) * p.mark_price * p.mmr_rate for p in positions if p.mmr_rate), Decimal(0))
    baseline = Baseline(eff, mm, "HYPOTHETICAL ACCOUNT (real Bitget prices)", "HYPOTHETICAL")
    notes = [fixture["recognized_price_note"], "Hypothetical baseline effEquity/MM computed by PRISM; there is no Bitget account to reconcile."]
    return Workbench(baseline, holdings, positions, schedules, None, None, notes)


# ---- Bitget account (demo or live) -------------------------------------------------------------

def bitget_workbench(client: BitgetClient) -> Workbench:
    environment = "DEMO_PAPTRADING" if client.settings.bitget_demo else "LIVE"
    assets = client.get("/api/v3/account/assets", authenticated=True)
    positions = client.get("/api/v3/position/current-position", {"category": "USDT-FUTURES"}, authenticated=True)
    collateral = client.get("/api/v3/account/collateral-type", authenticated=True)
    orders = client.get("/api/v3/trade/unfilled-orders", {"category": "USDT-FUTURES"}, authenticated=True)
    demo = client.settings.bitget_demo
    if not assets.ok:
        raise RuntimeError(f"account assets unavailable: {assets.error or assets.msg}")
    provenance = Provenance(
        source=f"bitget:{assets.endpoint}", source_timestamp=assets.provider_ts, retrieved_at=assets.request_ts,
        data_mode=DataMode.LIVE if environment == "LIVE" else DataMode.LIMITED, raw_payload_hash=assets.raw_payload_hash,
    )
    order_rows = (orders.data.get("list") if isinstance(orders.data, dict) else orders.data) if orders.ok else None
    snapshot = snapshot_from_bitget(assets.data, positions.data if positions.ok else {"list": []}, provenance, environment,
                                    collateral.data if collateral.ok else None, open_orders=order_rows)
    schedules = fetch_schedules(client, demo_env=demo)
    result = reconcile(snapshot, schedules)
    try:  # history is best-effort: a storage error must never block the analysis
        from .store import record_reconciliation

        record_reconciliation(snapshot, result, source="workbench")
    except Exception:
        pass

    holdings = []
    for a in snapshot.assets:
        # usdValue includes unrealised PnL (verified 2026-10-07), so the coin's USD rate is usdValue / equity, not / balance.
        quantity = a.equity if a.equity not in (None, Decimal(0)) else a.balance
        if quantity in (None, Decimal(0)) or a.usd_value is None:
            continue
        price = a.usd_value / quantity
        eligible = a.coin in snapshot.collateral_coins if snapshot.collateral_type == "custom" and snapshot.collateral_coins else a.coin in schedules
        holdings.append(Holding(a.coin, quantity, price, factor_for(a.coin, schedules), eligible, schedules.get(a.coin), "derived: usdValue / equity"))
    perps = []
    for p in snapshot.positions:
        if p.direction is None or p.size is None or p.mark_price is None:
            continue
        mmr, source = (p.mmr_rate, "position mmr field") if p.mmr_rate is not None else fetch_mmr_rate(client, p.symbol, abs(p.size) * p.mark_price, demo_env=demo)
        perps.append(with_perp_context(client, PerpPosition(p.symbol, p.direction, abs(p.size), p.mark_price, perp_factor(client, p.symbol, schedules, demo), mmr, source), demo))
    baseline = Baseline(snapshot.effective_equity or Decimal(0), snapshot.maintenance_margin or Decimal(0),
                        f"BITGET OBSERVED ({environment})", result.mode.value)
    notes = list(result.reasons)
    if snapshot.unverified_fields:
        notes.append("Unverified fields: " + ", ".join(snapshot.unverified_fields))
    return Workbench(baseline, holdings, perps, schedules, result, snapshot, notes)


def attach_crypto_betas(client: BitgetClient, wb: Workbench) -> Workbench:
    """Link crypto shocks into rToken collateral with significant historical beta to BTC (model-based, labelled)."""
    from dataclasses import replace

    from .wrong_way import crypto_betas

    betas = crypto_betas(client, wb.holdings)
    holdings = [replace(h, crypto_beta=betas[h.coin][0]) if h.coin in betas else h for h in wb.holdings]
    notes = wb.data_notes + [f"{c}: crypto beta {b:.2f} (corr {r:.2f}, {n} days) linked into crypto shocks" for c, (b, r, n) in betas.items()]
    return replace(wb, holdings=holdings, data_notes=notes)
