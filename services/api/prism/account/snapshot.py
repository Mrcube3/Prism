"""Bitget-observed account state (AGENTS.md §1.2, §5, §6, §12).

Values here are what Bitget reports; PRISM never edits them. Field names come from the
verified `account/assets` top level (2026-10-06). Per-coin and per-position field names are
not yet observed (demo account was empty, Q-WS-SCHEMA), so parsing is defensive: known
aliases are tried, and anything missing is listed in `unverified_fields` rather than guessed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from ..provenance import DataMode, Provenance


def dec(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def _first(row: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in row and row[name] not in (None, ""):
            return row[name]
    return None


@dataclass(frozen=True)
class AssetBalance:
    coin: str
    balance: Decimal | None
    equity: Decimal | None
    usd_value: Decimal | None
    available: Decimal | None
    debts: Decimal | None
    raw: dict[str, Any]


@dataclass(frozen=True)
class Position:
    symbol: str
    category: str | None
    side: str | None  # "long" / "short" as reported
    size: Decimal | None
    entry_price: Decimal | None
    mark_price: Decimal | None
    leverage: Decimal | None
    unrealised_pnl: Decimal | None
    liq_price: Decimal | None
    mmr_rate: Decimal | None
    margin_mode: str | None
    raw: dict[str, Any]

    @property
    def direction(self) -> int | None:
        if self.side is None:
            return None
        side = self.side.lower()
        return 1 if side in ("long", "buy") else -1 if side in ("short", "sell") else None


@dataclass(frozen=True)
class AccountSnapshot:
    provenance: Provenance
    account_environment: str  # LIVE / DEMO_PAPTRADING / HYPOTHETICAL
    total_equity: Decimal | None
    effective_equity: Decimal | None
    maintenance_margin: Decimal | None
    initial_margin: Decimal | None
    margin_ratio_raw: Decimal | None  # unit (fraction vs percent) unverified: Q-RATIO-UNIT
    unrealised_pnl: Decimal | None
    assets: tuple[AssetBalance, ...]
    positions: tuple[Position, ...]
    # Unfilled non-reduce-only orders. Bitget's imr/mmr/positionValue include them (observed 2026-10-07).
    open_orders: tuple[dict[str, Any], ...] = ()
    hold_mode: str | None = None
    collateral_type: str | None = None
    collateral_coins: tuple[str, ...] | None = None
    unverified_fields: tuple[str, ...] = field(default=())

    @property
    def is_empty(self) -> bool:
        return not self.assets and not self.positions


def parse_assets(row: dict[str, Any]) -> tuple[AssetBalance, list[str]]:
    missing = [name for name, aliases in (
        ("usdValue", ("usdValue",)), ("equity", ("equity",)), ("balance", ("balance", "balanceOriginal")),
    ) if _first(row, *aliases) is None]
    asset = AssetBalance(
        coin=str(row.get("coin", "")).upper(),
        balance=dec(_first(row, "balance", "balanceOriginal")),
        equity=dec(row.get("equity")),
        usd_value=dec(row.get("usdValue")),
        available=dec(row.get("available")),
        debts=dec(_first(row, "debt", "debts", "borrow")),  # REST: "debt"; WS: "debts"/"borrow" (verified 2026-10-07)
        raw=row,
    )
    return asset, [f"assets[{asset.coin}].{m}" for m in missing]


def parse_position(row: dict[str, Any]) -> tuple[Position, list[str]]:
    position = Position(
        symbol=str(row.get("symbol", "")),
        category=row.get("category"),
        side=_first(row, "posSide", "holdSide", "side"),
        size=dec(_first(row, "size", "total", "qty")),
        entry_price=dec(_first(row, "avgPrice", "openPriceAvg", "entryPrice")),
        mark_price=dec(row.get("markPrice")),
        leverage=dec(row.get("leverage")),
        unrealised_pnl=dec(_first(row, "unrealisedPnl", "unrealizedPL", "unrealisedPnL")),
        liq_price=dec(_first(row, "liqPrice", "liquidationPrice")),
        mmr_rate=dec(_first(row, "mmr", "keepMarginRate")),
        margin_mode=row.get("marginMode"),
        raw=row,
    )
    needed = {"side": position.side, "size": position.size, "entry_price": position.entry_price, "mark_price": position.mark_price}
    return position, [f"positions[{position.symbol}].{k}" for k, v in needed.items() if v is None]


def snapshot_from_bitget(
    assets_payload: dict[str, Any],
    positions_payload: Any,
    provenance: Provenance,
    account_environment: str,
    collateral_payload: dict[str, Any] | None = None,
    open_orders: list[dict[str, Any]] | None = None,
) -> AccountSnapshot:
    unverified: list[str] = []
    assets = []
    for row in assets_payload.get("assets") or []:
        asset, missing = parse_assets(row)
        assets.append(asset)
        unverified += missing
    rows = positions_payload.get("list") if isinstance(positions_payload, dict) else positions_payload
    positions = []
    for row in rows or []:
        position, missing = parse_position(row)
        positions.append(position)
        unverified += missing
    coins = None
    if collateral_payload and collateral_payload.get("collateralCoins"):
        coins = tuple(c.strip().upper() for c in str(collateral_payload["collateralCoins"]).split(",") if c.strip())
    if account_environment == "DEMO_PAPTRADING" and provenance.data_mode == DataMode.LIVE:
        raise ValueError("demo-environment data must not be labelled LIVE")
    return AccountSnapshot(
        provenance=provenance,
        account_environment=account_environment,
        total_equity=dec(assets_payload.get("accountEquity")),
        effective_equity=dec(assets_payload.get("effEquity")),
        maintenance_margin=dec(assets_payload.get("mmr")),
        initial_margin=dec(assets_payload.get("imr")),
        margin_ratio_raw=dec(assets_payload.get("mgnRatio")),
        unrealised_pnl=dec(assets_payload.get("unrealisedPnl")),
        assets=tuple(assets),
        positions=tuple(positions),
        open_orders=tuple(
            {k: o.get(k) for k in ("symbol", "side", "posSide", "orderType", "price", "qty", "reduceOnly")}
            for o in (open_orders or []) if str(o.get("reduceOnly", "NO")).upper() != "YES"
        ),
        hold_mode=next((p.raw.get("holdMode") for p in positions if p.raw.get("holdMode")), None),
        collateral_type=(collateral_payload or {}).get("collateralType"),
        collateral_coins=coins,
        unverified_fields=tuple(unverified),
    )
