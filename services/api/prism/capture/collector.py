"""Continuous closure-window capture (AGENTS.md §47, §48, §77).

Polls read-only Bitget endpoints on fixed cadences, stores every raw response
(RawStore) and appends normalized observations to data/capture/<date>/*.jsonl.
Only information knowable at retrieval time is written; nothing is backfilled
or interpolated. Failures are recorded as observations, never filled in.
"""

from __future__ import annotations

import gzip
import json
import logging
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from ..config import Settings
from ..connectors.bitget import BitgetClient, BitgetResponse
from ..provenance import RawStore, utc_now
from .session import session_context

log = logging.getLogger("prism.capture")

CAPTURE_VERSION = "prism-capture-v0.1"

# Weekend-tradable rTokens from stock-info (2026-10-06) relevant to the target user (AGENTS.md §2, docs/PRODUCT_VISION.md).
DEFAULT_RTOKENS = (
    "RNVDAUSDT", "RTSLAUSDT", "RCOINUSDT", "RMSTRUSDT", "RSPYUSDT", "RQQQUSDT", "RAAPLUSDT",
    "RMSFTUSDT", "RAMZNUSDT", "RMETAUSDT", "RGOOGLUSDT", "RAMDUSDT", "RPLTRUSDT", "RHOODUSDT",
)
# Crypto factors plus stock perps whose index/mark give an independent reference for the same underlyings.
DEFAULT_PERPS = ("BTCUSDT", "ETHUSDT", "NVDAUSDT", "TSLAUSDT", "COINUSDT", "MSTRUSDT")


@dataclass
class Cadence:
    """Seconds between polls for each job."""

    tickers: int = 60
    orderbook: int = 60
    candles: int = 900
    session: int = 900
    account: int = 300
    collateral_tiers: int = 86_400
    reality: int = 300


@dataclass
class Collector:
    settings: Settings
    root: Path
    rtokens: tuple[str, ...] = DEFAULT_RTOKENS
    perps: tuple[str, ...] = DEFAULT_PERPS
    cadence: Cadence = field(default_factory=Cadence)
    orderbook_levels: int = 15
    _last_run: dict[str, float] = field(default_factory=dict)
    _states: dict[str, Any] | None = None
    _calendar: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        self.capture_root = self.root / "capture"
        self.client = BitgetClient(self.settings, raw_store=RawStore(self.root / "raw"))

    # ---- storage -------------------------------------------------------------------------

    def _append(self, stream: str, record: dict[str, Any]) -> None:
        day = utc_now().strftime("%Y-%m-%d")
        directory = self.capture_root / day
        directory.mkdir(parents=True, exist_ok=True)
        with (directory / f"{stream}.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

    def _meta(self, response: BitgetResponse) -> dict[str, Any]:
        return {
            "source": f"bitget:{response.endpoint}",
            "retrieved_at": response.request_ts.isoformat(),
            "provider_ts": response.provider_ts.isoformat() if response.provider_ts else None,
            "raw_payload_hash": response.raw_payload_hash,
            "capture_version": CAPTURE_VERSION,
            "ok": response.ok,
            "error": None if response.ok else (response.error or f"{response.http_status} {response.code} {response.msg}"),
        }

    def compress_old_days(self) -> None:
        """Gzip finished days so long-running capture stays small. Today's files are never touched."""
        today = utc_now().strftime("%Y-%m-%d")
        for base in (self.capture_root, self.root / "raw"):
            if not base.exists():
                continue
            for path in base.rglob("*.jsonl"):
                day = next((part for part in path.parts if len(part) == 10 and part[4] == "-"), None)
                if day and day < today:
                    with path.open("rb") as src, gzip.open(f"{path}.gz", "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    path.unlink()

    # ---- jobs ----------------------------------------------------------------------------

    def _due(self, job: str, every: int, now: float) -> bool:
        if now - self._last_run.get(job, 0.0) >= every:
            self._last_run[job] = now
            return True
        return False

    def poll_session(self) -> None:
        states = self.client.get("/api/v3/reality/market/states")
        calendar = self.client.get("/api/v3/reality/market/calendar")
        # Observed as a single object (2026-10-07); accept a one-element list as well.
        if states.ok and isinstance(states.data, dict):
            self._states = states.data
        elif states.ok and isinstance(states.data, list) and states.data:
            self._states = states.data[0]
        if calendar.ok and isinstance(calendar.data, dict):
            self._calendar = calendar.data
        self._append("session", {"meta": [self._meta(states), self._meta(calendar)], "states": self._states, "calendar": self._calendar})

    def session_now(self) -> dict[str, Any]:
        return session_context(utc_now(), self._states, self._calendar)

    def poll_tickers(self) -> None:
        session = self.session_now()
        for category, symbols in (("SPOT", self.rtokens), ("USDT-FUTURES", self.perps)):
            for symbol in symbols:
                response = self.client.get("/api/v3/market/tickers", {"category": category, "symbol": symbol})
                row = response.data[0] if response.ok and isinstance(response.data, list) and response.data else {}
                self._append("tickers", {
                    "meta": self._meta(response),
                    "symbol": symbol,
                    "category": category,
                    "ts": row.get("ts"),
                    "last": row.get("lastPrice"),
                    "bid1": row.get("bid1Price"),
                    "ask1": row.get("ask1Price"),
                    "bid1_size": row.get("bid1Size"),
                    "ask1_size": row.get("ask1Size"),
                    "volume24h": row.get("volume24h"),
                    # turnover24h appears to describe the underlying market for rTokens (Q-TURNOVER); both are kept.
                    "turnover24h": row.get("turnover24h"),
                    "platform_turnover24h": row.get("platformTurnover24h"),
                    "index_price": row.get("indexPrice"),
                    "mark_price": row.get("markPrice"),
                    "funding_rate": row.get("fundingRate"),
                    "session": session,
                })

    def poll_orderbooks(self) -> None:
        for symbol in self.rtokens:
            response = self.client.get("/api/v3/market/orderbook", {"category": "SPOT", "symbol": symbol, "limit": str(self.orderbook_levels)})
            book = response.data if response.ok and isinstance(response.data, dict) else {}
            self._append("orderbooks", {
                "meta": self._meta(response),
                "symbol": symbol,
                "source_book": "public_market_orderbook",  # not the whitelisted Reality book (Q-DEPTH)
                "ts": book.get("ts"),
                "bids": book.get("b"),
                "asks": book.get("a"),
            })

    def poll_candles(self) -> None:
        for category, symbols in (("SPOT", self.rtokens), ("USDT-FUTURES", self.perps)):
            for symbol in symbols:
                response = self.client.get("/api/v3/market/candles", {"category": category, "symbol": symbol, "interval": "1m", "limit": "20"})
                self._append("candles_1m", {"meta": self._meta(response), "symbol": symbol, "category": category, "rows": response.data if response.ok else None})

    def poll_account(self) -> None:
        if not self.settings.has_bitget_credentials:
            return
        environment = "DEMO_PAPTRADING" if self.settings.bitget_demo else "LIVE"
        response = self.client.get("/api/v3/account/assets", authenticated=True)
        # Only the fields needed for the thaw experiment; identifiers are not copied.
        data = response.data if response.ok and isinstance(response.data, dict) else {}
        self._append("account", {
            "meta": self._meta(response),
            "account_environment": environment,
            "summary": {k: data.get(k) for k in ("accountEquity", "effEquity", "mmr", "imr", "mgnRatio", "unrealisedPnl")},
            "assets": data.get("assets"),
        })

    def poll_reality(self) -> None:
        """Reality Envelopes for the watchlist: the history calibration.py and validate_reality.py learn from."""
        import dataclasses

        from ..reality.live import envelope

        for symbol in self.rtokens:
            try:
                env = dataclasses.asdict(envelope(self.client, symbol, self.capture_root))
                self._append("reality", {"at": utc_now().isoformat(), "symbol": symbol, "envelope": env})
            except Exception as exc:  # recorded as a failure, never filled in
                self._append("reality", {"at": utc_now().isoformat(), "symbol": symbol, "error": f"{type(exc).__name__}: {exc}"[:200]})

    def poll_collateral_tiers(self) -> None:
        response = self.client.get("/api/v3/market/discount-rate")
        self._append("collateral_tiers", {"meta": self._meta(response), "tiers": response.data if response.ok else None})

    # ---- loop ----------------------------------------------------------------------------

    def run_once(self) -> None:
        now = time.monotonic()
        jobs = (
            ("session", self.cadence.session, self.poll_session),
            ("tickers", self.cadence.tickers, self.poll_tickers),
            ("orderbook", self.cadence.orderbook, self.poll_orderbooks),
            ("candles", self.cadence.candles, self.poll_candles),
            ("account", self.cadence.account, self.poll_account),
            ("collateral_tiers", self.cadence.collateral_tiers, self.poll_collateral_tiers),
            ("reality", self.cadence.reality, self.poll_reality),
        )
        for name, every, job in jobs:
            if self._due(name, every, now):
                try:
                    job()
                except Exception:  # one failing job must never stop the capture
                    log.exception("capture job %s failed", name)
        self._write_heartbeat()

    def _write_heartbeat(self) -> None:
        self.capture_root.mkdir(parents=True, exist_ok=True)
        heartbeat = {"at": utc_now().isoformat(), "capture_version": CAPTURE_VERSION, "last_run_monotonic": self._last_run}
        (self.capture_root / "heartbeat.json").write_text(json.dumps(heartbeat), encoding="utf-8")

    def run_forever(self, tick_seconds: float = 5.0) -> None:
        log.info("capture started: %d rTokens, %d perps", len(self.rtokens), len(self.perps))
        last_day = None
        while True:
            day = utc_now().strftime("%Y-%m-%d")
            if day != last_day:
                try:
                    self.compress_old_days()
                except Exception:
                    log.exception("compression failed")
                last_day = day
            self.run_once()
            time.sleep(tick_seconds)
