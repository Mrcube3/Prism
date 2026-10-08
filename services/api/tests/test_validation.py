import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from prism.reality.engine import NY

spec = importlib.util.spec_from_file_location("validate_reality", Path(__file__).resolve().parents[3] / "scripts" / "validate_reality.py")
vr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vr)


def ny(y, m, d, h, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=NY).astimezone(timezone.utc)


def write(root: Path, stream: str, rows: list[dict]) -> None:
    day = root / datetime.now(timezone.utc).strftime("%Y-%m-%d")
    day.mkdir(parents=True, exist_ok=True)
    with (day / f"{stream}.jsonl").open("a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def env(regime, move=None, center=None, under=None):
    return {"regime": regime, "underlying": "NVDA", "reference_price": "100", "reference_time": ny(2026, 10, 6, 20).isoformat(),
            "move": move, "reality_center": center, "underlying_last": under, "market_state": "DISCOVERY", "evidence_mode": "OBSERVED"}


def test_autopsy_scores_methods_against_the_realized_reopen(tmp_path):
    write(tmp_path, "reality", [
        {"at": ny(2026, 10, 6, 23).isoformat(), "symbol": "RNVDAUSDT", "envelope": env("FROZEN_REFERENCE", "0.05", "0.04")},
        {"at": ny(2026, 10, 7, 3, 55).isoformat(), "symbol": "RNVDAUSDT", "envelope": env("FROZEN_REFERENCE", "0.06", "0.05")},
        {"at": ny(2026, 10, 7, 4, 5).isoformat(), "symbol": "RNVDAUSDT", "envelope": env("LIVE_REFERENCE", under="104")},
    ])
    rows = vr.autopsy(tmp_path)
    assert len(rows) == 1
    r = rows[0]
    assert abs(r["realized_move"] - 0.04) < 1e-9
    assert abs(r["error_A_pp"] - 4) < 1e-9 and abs(r["error_B_pp"] - 2) < 1e-9 and abs(r["error_P_pp"] - 1) < 1e-9
    assert r["direction_B"] is True and r["direction_A"] is None
    assert vr.summarize(rows)["P"]["mae_pp"] == 1.0


def test_unfinished_closure_is_not_scored(tmp_path):
    write(tmp_path, "reality", [{"at": ny(2026, 10, 6, 23).isoformat(), "symbol": "RNVDAUSDT", "envelope": env("FROZEN_REFERENCE", "0.05", "0.04")}])
    assert vr.autopsy(tmp_path) == []


def test_flat_perp_index_is_detected_and_compared_with_the_proxy(tmp_path):
    base = ny(2026, 10, 6, 21)
    write(tmp_path, "tickers", [{"category": "USDT-FUTURES", "symbol": "NVDAUSDT", "index_price": "100.5",
                                 "meta": {"ok": True, "retrieved_at": (base + timedelta(minutes=i)).isoformat()}} for i in range(40)]
          + [{"category": "SPOT", "symbol": "RNVDAUSDT", "meta": {"ok": True, "retrieved_at": base.isoformat()}},
             {"category": "USDT-FUTURES", "symbol": "BTCUSDT", "index_price": "1", "meta": {"ok": True, "retrieved_at": base.isoformat()}}])
    write(tmp_path, "reality", [{"at": base.isoformat(), "symbol": "RNVDAUSDT", "envelope": env("FROZEN_REFERENCE", "0", "0")}])
    (row,) = vr.proxy_check(tmp_path)
    assert row["index_flat"] and row["plateau"] == "100.5" and row["proxy_error_bps"] == round((100 / 100.5 - 1) * 10000, 2)
