"""Persistent history (AGENTS.md §16, §59): reconciliation runs, account snapshots and pre-trade analyses.

SQLite (standard library) at data/prism.db. Rows are append-only; nothing is updated or deleted, so
history can be audited. Stored payloads never include credentials or account identifiers.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterator

from .config import REPO_ROOT
from .provenance import utc_now

DB_PATH = REPO_ROOT / "data" / "prism.db"
_LOCK = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS reconciliation_run (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  at TEXT NOT NULL,
  environment TEXT NOT NULL,
  mode TEXT NOT NULL,
  observed_effective_equity TEXT,
  reconstructed_effective_equity TEXT,
  equity_error TEXT,
  best_hypothesis TEXT,
  observed_maintenance_margin TEXT,
  reconstructed_maintenance_margin TEXT,
  margin_ratio_unit TEXT,
  reasons TEXT NOT NULL,
  snapshot_hash TEXT,
  source TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS account_snapshot (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  at TEXT NOT NULL,
  environment TEXT NOT NULL,
  raw_payload_hash TEXT,
  summary TEXT NOT NULL,
  source TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pretrade_run (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  at TEXT NOT NULL,
  account TEXT NOT NULL,
  input_hash TEXT NOT NULL,
  question TEXT NOT NULL,
  parsed_trade TEXT,
  verdict TEXT,
  result TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_recon_at ON reconciliation_run(at);
CREATE INDEX IF NOT EXISTS ix_snap_at ON account_snapshot(at);
CREATE INDEX IF NOT EXISTS ix_pretrade_at ON pretrade_run(at);
"""
TABLES = ("reconciliation_run", "account_snapshot", "pretrade_run")


def _default(value: Any) -> Any:
    return str(value) if isinstance(value, Decimal) else value.__dict__ if hasattr(value, "__dict__") else str(value)


@contextmanager
def connect(path: Path | None = None) -> Iterator[sqlite3.Connection]:
    path = path or DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        conn = sqlite3.connect(path, timeout=10)
        try:
            conn.executescript(SCHEMA)
            yield conn
            conn.commit()
        finally:
            conn.close()


def _s(value: Any) -> str | None:
    return None if value is None else str(value)


def record_reconciliation(snapshot: Any, result: Any, source: str, path: Path | None = None) -> None:
    best = result.best_equity_hypothesis
    with connect(path) as c:
        c.execute(
            "INSERT INTO reconciliation_run (at, environment, mode, observed_effective_equity, reconstructed_effective_equity,"
            " equity_error, best_hypothesis, observed_maintenance_margin, reconstructed_maintenance_margin, margin_ratio_unit,"
            " reasons, snapshot_hash, source) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (utc_now().isoformat(), result.account_environment, result.mode.value, _s(result.observed_effective_equity),
             _s(best.reconstructed if best else None), _s(best.error if best else None),
             f"{best.tier_mode}/{best.upnl}" if best else None, _s(result.observed_maintenance_margin),
             _s(result.reconstructed_maintenance_margin), result.margin_ratio_unit, json.dumps(list(result.reasons)),
             snapshot.provenance.raw_payload_hash, source),
        )
        c.execute(
            "INSERT INTO account_snapshot (at, environment, raw_payload_hash, summary, source) VALUES (?,?,?,?,?)",
            (utc_now().isoformat(), snapshot.account_environment, snapshot.provenance.raw_payload_hash,
             json.dumps({
                 "effective_equity": _s(snapshot.effective_equity), "total_equity": _s(snapshot.total_equity),
                 "maintenance_margin": _s(snapshot.maintenance_margin), "initial_margin": _s(snapshot.initial_margin),
                 "margin_ratio_raw": _s(snapshot.margin_ratio_raw), "assets": len(snapshot.assets),
                 "positions": [{"symbol": p.symbol, "side": p.side, "size": _s(p.size), "mark": _s(p.mark_price)} for p in snapshot.positions],
                 "open_orders": len(snapshot.open_orders), "hold_mode": snapshot.hold_mode,
             }), source),
        )


def record_pretrade(account: str, input_hash: str, question: str, body: dict, path: Path | None = None) -> None:
    with connect(path) as c:
        c.execute(
            "INSERT INTO pretrade_run (at, account, input_hash, question, parsed_trade, verdict, result) VALUES (?,?,?,?,?,?,?)",
            (utc_now().isoformat(), account, input_hash, question, json.dumps(body.get("parsed_trade"), default=_default),
             body.get("verdict"), json.dumps(body, default=_default)),
        )


def history(table: str, limit: int = 50, path: Path | None = None) -> list[dict]:
    if table not in TABLES:
        raise ValueError("unknown table")
    with connect(path) as c:
        c.row_factory = sqlite3.Row
        rows = c.execute(f"SELECT * FROM {table} ORDER BY id DESC LIMIT ?", (max(1, min(limit, 500)),)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        for k in ("reasons", "summary", "parsed_trade", "result"):
            if d.get(k):
                d[k] = json.loads(d[k])
        out.append(d)
    return out
