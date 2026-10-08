from datetime import datetime, timezone

from prism.reality import backfill as bf


def w(sym, reopen, center, realized, live=None):
    return bf.Window(sym, sym[1:-4], reopen, reopen, "OVERNIGHT", 100.0, 100.0, live if live is not None else center, None, center, realized, {})


def test_methods_and_band_are_computed_time_ordered():
    ws = [w("RNVDAUSDT", f"2026-09-{d:02d}T04:00:00-04:00", 0.01, 0.01 + (d % 5 - 2) * 0.001) for d in range(1, 31)]
    ws += [w("RCOINUSDT", f"2026-09-{d:02d}T04:00:00-04:00", -0.02, -0.02 + (d % 3 - 1) * 0.002) for d in range(1, 31)]
    cal = bf.calibrate(ws)
    assert cal["windows"] == 60 and cal["pooled"]["n"] == 60
    assert cal["methods"]["A_frozen_reference"]["mae_pp"] > cal["methods"]["P_prism_centre"]["mae_pp"]
    assert "q05" in cal["per_asset"]["RNVDAUSDT"] and cal["per_asset"]["RNVDAUSDT"]["q05"] < 0 < cal["per_asset"]["RNVDAUSDT"]["q95"]
    assert cal["walk_forward"]["scored"] > 0  # bands only from earlier windows


def test_load_band_prefers_own_history_then_pooled(tmp_path):
    import json
    (tmp_path / "band_calibration.json").write_text(json.dumps({
        "per_asset": {"RNVDAUSDT": {"n": 25, "q05": -0.01, "q95": 0.02}, "RXUSDT": {"n": 3}},
        "pooled": {"n": 400, "q05": -0.004, "q95": 0.003}}))
    assert bf.load_band(tmp_path, "RNVDAUSDT")[2] == "OWN_25_WINDOWS"
    assert bf.load_band(tmp_path, "RXUSDT")[2] == "POOLED_400_WINDOWS"
    assert bf.load_band(tmp_path / "missing", "RXUSDT")[2] == "UNCALIBRATED"
