"""PRISM read-only HTTP API and mission-control page (AGENTS.md §55, §58).

Binds to localhost by default. Every response keeps the BITGET OBSERVED / PRISM SHADOW / HYPOTHETICAL
labels from the engines. POST analyses are cached by input hash for a minute (idempotent, §58).
"""

from __future__ import annotations

import dataclasses
import json
import re
import time
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ..config import REPO_ROOT, load_env_file, load_settings
from ..connectors.bitget import BitgetClient
from ..connectors.bitget_data import BitgetDataMCP
from ..connectors.qwen import NumericClaimError, ProposedTrade, QwenClient, QwenInvalidOutput, QwenUnavailable
from ..provenance import payload_hash, utc_now
from ..shadow import Scenario, run_frontier, run_shadow
from ..workbench import JUDGE_FIXTURE, Workbench, attach_crypto_betas, bitget_workbench, judge_workbench
from .. import pretrade, repair, wrong_way

STATIC = Path(__file__).parent / "static"
# Symbols flow into upstream URLs (e.g. Yahoo's path), so only plain tickers are accepted.
RTOKEN_SYMBOL = re.compile(r"R[A-Z0-9]{1,10}USDT")
TICKER = re.compile(r"[A-Z]{1,6}(\.[A-Z])?")
CACHE_SECONDS = 60
Account = Literal["judge", "bitget"]

app = FastAPI(title="PRISM", version="0.1", docs_url="/api/docs")
_settings = load_settings()
_client = BitgetClient(_settings)
_cache: dict[str, tuple[float, Any]] = {}


# ---- public-access guard ---------------------------------------------------------------------
# Requests arriving through the reverse proxy carry X-Forwarded-For and are treated as public.
# The Bitget account view is public unless PRISM_ACCOUNT_PASSWORD is set (then HTTP Basic, any
# username). Local requests (SSH tunnel) are never restricted.
_ACCOUNT_PASSWORD = (load_env_file(REPO_ROOT / ".env.local").get("PRISM_ACCOUNT_PASSWORD") or "").strip()
_hits: dict[str, list[float]] = {}
# Per-visitor limits on endpoints that cost Qwen credits or many Bitget calls.
LIMITS = {"pretrade": (6, 600), "repair": (20, 600)}
GLOBAL_PRETRADE_PER_HOUR = 60


def is_public(request: Request) -> bool:
    return "x-forwarded-for" in request.headers


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    return forwarded.split(",")[0].strip() or (request.client.host if request.client else "unknown")


def guard_account(request: Request, account: str) -> None:
    if account != "bitget" or not is_public(request):
        return
    import base64
    import hmac

    if not _ACCOUNT_PASSWORD:
        return  # no password configured: the account view is public (owner's choice, 2026-10-07)
    supplied = ""
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("basic "):
        try:
            supplied = base64.b64decode(auth[6:]).decode("utf-8").split(":", 1)[-1]
        except ValueError:
            supplied = ""
    if not hmac.compare_digest(supplied.encode(), _ACCOUNT_PASSWORD.encode()):
        raise HTTPException(401, "Password required for the Bitget account view.", headers={"WWW-Authenticate": 'Basic realm="PRISM account"'})


def rate_limit(request: Request, kind: str) -> None:
    if not is_public(request):
        return
    now = time.monotonic()
    count, window = LIMITS[kind]
    key = f"{kind}:{client_ip(request)}"
    recent = [t for t in _hits.get(key, []) if now - t < window]
    if len(recent) >= count:
        raise HTTPException(429, f"Rate limit: {count} {kind} requests per {window // 60} minutes. Please try again shortly.")
    if kind == "pretrade":
        total = [t for t in _hits.get("pretrade:*", []) if now - t < 3600]
        if len(total) >= GLOBAL_PRETRADE_PER_HOUR:
            raise HTTPException(429, "PRISM is busy; the hourly analysis budget is used up. Please try again later.")
        _hits["pretrade:*"] = total + [now]
    _hits[key] = recent + [now]
    if len(_hits) > 5000:  # drop visitors with no requests inside their window
        for k in [k for k, v in _hits.items() if not v or now - v[-1] > 3600]:
            del _hits[k]


def jsonable(value: Any) -> Any:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: jsonable(getattr(value, f.name)) for f in dataclasses.fields(value) if f.name not in ("raw", "schedule")}
    if isinstance(value, BaseModel):
        return value.model_dump()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items() if k not in ("schedules",)}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    return value


CACHE_MAX_ENTRIES = 500  # public traffic must not grow memory without bound


def cached(key: str, build):
    now = time.monotonic()
    hit = _cache.get(key)
    if hit and now - hit[0] < CACHE_SECONDS:
        return hit[1]
    value = build()
    if len(_cache) >= CACHE_MAX_ENTRIES:
        for stale in [k for k, (t, _) in _cache.items() if now - t >= CACHE_SECONDS]:
            del _cache[stale]
        while len(_cache) >= CACHE_MAX_ENTRIES:
            del _cache[min(_cache, key=lambda k: _cache[k][0])]
    _cache[key] = (now, value)
    return value


def workbench(account: Account) -> Workbench:
    def build() -> Workbench:
        if account == "judge":
            fixture = json.loads((REPO_ROOT / "data" / "fixtures" / JUDGE_FIXTURE).read_text(encoding="utf-8"))
            wb = judge_workbench(_client, fixture)
        else:
            wb = bitget_workbench(_client)
        return attach_crypto_betas(_client, wb) if wb.holdings else wb
    try:
        return cached(f"wb:{account}", build)
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc


def shadow_summary(wb: Workbench, scenario: Scenario) -> dict[str, Any]:
    result = run_shadow(wb.baseline, wb.holdings, wb.positions, scenario)
    return jsonable(result) | {"top_contributors": jsonable(result.top_contributors)}


@app.get("/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "at": utc_now().isoformat(), "account_environment": "DEMO_PAPTRADING" if _settings.bitget_demo else "LIVE"}


@app.get("/api/workbench")
def get_workbench(request: Request, account: Account = "judge") -> dict[str, Any]:
    guard_account(request, account)
    wb = workbench(account)
    return {
        "baseline": jsonable(wb.baseline) | {"core_ratio": jsonable(wb.baseline.core_ratio)},
        "holdings": jsonable(wb.holdings),
        "positions": jsonable(wb.positions),
        "reconciliation": jsonable(wb.reconciliation),
        "open_orders": jsonable(wb.snapshot.open_orders) if wb.snapshot else [],
        "hold_mode": wb.snapshot.hold_mode if wb.snapshot else None,
        "notes": wb.data_notes,
        "empty": not (wb.holdings or wb.positions),
        # An empty account has no meaningful shadow; never render it as a breach.
        "scenarios": {k: shadow_summary(wb, s) for k, s in pretrade.SCENARIOS.items()} if (wb.holdings or wb.positions) else {},
        "wrong_way": jsonable(cached(f"ww:{account}", lambda: wrong_way.analyze(_client, wb.holdings, wb.positions))) if wb.positions else [],
    }


@app.get("/api/frontier")
def get_frontier(request: Request, account: Account = "judge", boundary: Decimal = Decimal("0.80")) -> dict[str, Any]:
    guard_account(request, account)
    if not boundary.is_finite() or not Decimal("0.05") <= boundary <= Decimal("5"):
        raise HTTPException(400, "boundary must be a ratio between 0.05 and 5 (e.g. 0.80)")
    wb = workbench(account)
    if not (wb.holdings or wb.positions):
        return {"empty": True, "baseline_label": wb.baseline.label, "message": "Account holds nothing yet; there is no frontier to draw."}
    f = run_frontier(wb.baseline, wb.holdings, wb.positions, boundary=boundary)
    return {
        "baseline_label": f.baseline_label, "boundary": str(f.boundary), "current_state": f.current_state.value,
        "crypto_shocks": jsonable(f.crypto_shocks), "rtoken_shocks": jsonable(f.rtoken_shocks),
        "cells": [{"crypto": str(c.crypto_shock), "rtoken": str(c.rtoken_shock), "blind_zone": c.blind_zone,
                   "ratio": jsonable(c.result.shadow_core_ratio), "state": c.result.state.value,
                   "equity": str(c.result.shadow_effective_equity), "lcg": str(c.result.latent_collateral_gap),
                   "pnl": str(c.result.pnl_delta), "mm": str(c.result.shadow_maintenance_margin),
                   "top": jsonable(c.result.top_contributors)} for c in f.cells],
        "nearest_blind_zone": jsonable({"crypto": f.nearest_blind_zone_entry.crypto_shock, "rtoken": f.nearest_blind_zone_entry.rtoken_shock}) if f.nearest_blind_zone_entry else None,
        "label": "STRESS SCENARIO grid; shadow_core_ratio excludes the partial-liquidation fee and is not Bitget's mgnRatio",
    }


class PreTradeRequest(BaseModel):
    text: str = Field(min_length=3, max_length=500)  # bounds Qwen cost per request
    account: Account = "judge"
    explain: bool = True


@app.post("/api/pretrade")
def post_pretrade(req: PreTradeRequest, request: Request) -> dict[str, Any]:
    guard_account(request, req.account)
    key = "pt:" + payload_hash(req.model_dump())
    if key not in _cache:  # identical cached questions do not spend the visitor's budget
        rate_limit(request, "pretrade")
    return cached(key, lambda: _pretrade(req))


def _pretrade(req: PreTradeRequest) -> dict[str, Any]:
    wb = workbench(req.account)
    if not (wb.holdings or wb.positions):
        raise HTTPException(409, "This account holds nothing yet. Fund the demo account or use Judge Mode.")
    qwen = QwenClient()
    try:
        trade, call = qwen.parse_trade(req.text)
    except (QwenUnavailable, QwenInvalidOutput) as exc:
        raise HTTPException(503, f"Trade parser unavailable: {exc}") from exc
    try:
        result = pretrade.analyze(_client, wb, trade)
    except pretrade.TradeRejected as exc:
        return {"parsed_trade": trade.model_dump(), "rejected": str(exc), "parser": jsonable(call)}
    after_wb, *_ = pretrade.apply_trade(_client, wb, trade)
    _, candidates, repair_notes = repair.solve(_client, after_wb.baseline, after_wb.holdings, after_wb.positions,
                                               pretrade.SCENARIOS["severe"], Decimal("0.80"), wb.schedules.get("USDT"),
                                               demo_env=pretrade.is_demo(wb))
    body = {
        "parsed_trade": trade.model_dump(),
        "parser": jsonable(call),
        "trade": {"added_size": str(result.added_size), "entry_price": str(result.entry_price),
                  "entry_slippage_cost": str(result.entry_slippage_cost), "mmr_rate": jsonable(result.mmr_rate), "mmr_source": result.mmr_source},
        "before": {k: {"ratio": jsonable(v.shadow_core_ratio), "state": v.state.value, "equity": str(v.shadow_effective_equity)} for k, v in result.before.items()},
        "after": {k: {"ratio": jsonable(v.shadow_core_ratio), "state": v.state.value, "equity": str(v.shadow_effective_equity)} for k, v in result.after.items()},
        "blind_zone_cells": {"before": sum(c.blind_zone for c in result.frontier_before.cells), "after": sum(c.blind_zone for c in result.frontier_after.cells)},
        "wrong_way": jsonable(result.wrong_way),
        "verdict": result.verdict,
        "notes": result.notes,
        "repairs": {"scenario": "severe", "target_ratio": "0.80", "candidates": jsonable(candidates), "notes": repair_notes},
    }
    body["explanation"] = None
    if req.explain:
        facts = {"verdict": result.verdict, "label": wb.baseline.label, **{f"{k}_after_state": v["state"] for k, v in body["after"].items()}}
        try:
            body["explanation"], _ = qwen.explain(req.text, facts)
        except NumericClaimError as exc:
            body["explanation_rejected"] = f"Qwen's explanation contained numbers not produced by PRISM ({', '.join(exc.unsupported)}) and was discarded."
        except QwenUnavailable as exc:
            body["explanation_rejected"] = f"Qwen unavailable: {exc}"
    return body


class RepairRequest(BaseModel):
    account: Account = "judge"
    scenario: Literal["mild", "central", "severe"] = "severe"
    target: Decimal = Decimal("0.80")
    objective: Literal["min_cash", "min_exposure_change", "min_execution_cost", "max_safety"] = "min_execution_cost"


@app.post("/api/repair")
def post_repair(req: RepairRequest, request: Request) -> dict[str, Any]:
    guard_account(request, req.account)
    rate_limit(request, "repair")
    def build() -> dict[str, Any]:
        wb = workbench(req.account)
        before, candidates, notes = repair.solve(_client, wb.baseline, wb.holdings, wb.positions, pretrade.SCENARIOS[req.scenario],
                                                 req.target, wb.schedules.get("USDT"), demo_env=pretrade.is_demo(wb))
        ranked = sorted(candidates, key=repair.OBJECTIVES[req.objective])
        return {"before": jsonable(before), "candidates": jsonable(ranked), "notes": notes, "objective": req.objective}
    return cached("rp:" + payload_hash(jsonable(req.model_dump())), build)


@app.get("/api/research/{underlying}")
def get_research(underlying: str) -> dict[str, Any]:
    if not TICKER.fullmatch(underlying.upper()):
        raise HTTPException(400, "not a U.S. ticker")
    mcp = BitgetDataMCP()
    return {"quote": jsonable(mcp.underlying_quote(underlying.upper())), "earnings": jsonable(mcp.earnings_calendar(underlying.upper()))}


@app.get("/api/reality")
def get_reality(symbols: str = "RNVDAUSDT,RSPYUSDT,RCOINUSDT,RMSTRUSDT") -> dict[str, Any]:
    """Reality Envelopes (§21–§28) for rToken symbols, e.g. ?symbols=RNVDAUSDT,RCOINUSDT. Public market data only."""
    from ..reality.live import envelope

    wanted = [s.strip().upper() for s in symbols.split(",") if s.strip()][:12]
    out = {}
    for symbol in wanted:
        if not RTOKEN_SYMBOL.fullmatch(symbol):
            raise HTTPException(400, "not an rToken symbol (expected e.g. RNVDAUSDT)")
        try:
            out[symbol] = cached(f"reality:{symbol}", lambda s=symbol: jsonable(envelope(_client, s, REPO_ROOT / "data" / "capture")))
        except Exception as exc:  # one failing symbol must not hide the others
            out[symbol] = {"symbol": symbol, "error": f"{type(exc).__name__}: {exc}"[:200]}
    return {"envelopes": out, "retrieved_at": utc_now().isoformat()}


@app.get("/api/reality/{symbol}/series")
def get_reality_series(symbol: str, minutes: int = 240) -> dict[str, Any]:
    """Recent real 1m closes for the rToken and its stock perp, for the price-reality chart."""
    from ..reality.live import series

    symbol = symbol.upper()
    if not RTOKEN_SYMBOL.fullmatch(symbol):
        raise HTTPException(400, "not an rToken symbol (expected e.g. RNVDAUSDT)")
    return cached(f"series:{symbol}:{minutes}", lambda: series(_client, symbol, max(30, min(minutes, 1000))))


@app.get("/api/market/state")
def market_state() -> dict[str, Any]:
    """U.S. session under both time-zone readings of Bitget's labels (Q-TZ); see capture/session.py."""
    from ..capture.session import session_context

    def build() -> dict[str, Any]:
        states = _client.get("/api/v3/reality/market/states")
        calendar = _client.get("/api/v3/reality/market/calendar")
        s = states.data if isinstance(states.data, dict) else (states.data[0] if states.ok and states.data else None)
        c = calendar.data if calendar.ok and isinstance(calendar.data, dict) else None
        return session_context(utc_now(), s, c) | {"retrieved_at": utc_now().isoformat(), "source": f"bitget:{states.endpoint}"}
    return cached("market_state", build)


@app.get("/api/capture/status")
def capture_status() -> dict[str, Any]:
    heartbeat = REPO_ROOT / "data" / "capture" / "heartbeat.json"
    return json.loads(heartbeat.read_text(encoding="utf-8")) if heartbeat.exists() else {"status": "UNAVAILABLE", "reason": "capture not running on this host"}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store"})
