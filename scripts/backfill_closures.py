"""Rebuild past closure windows from real data and calibrate the stress band (see prism/reality/backfill.py).

    uv run --project services/api python scripts/backfill_closures.py

Writes data/research/closure_history.jsonl and data/research/band_calibration.json.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "services" / "api"))

from prism.capture.collector import DEFAULT_RTOKENS  # noqa: E402
from prism.config import load_settings  # noqa: E402
from prism.connectors.bitget import BitgetClient  # noqa: E402
from prism.reality.backfill import run  # noqa: E402
from prism.reality.live import perp_for, stock_info  # noqa: E402


def main() -> int:
    with BitgetClient(load_settings()) as client:
        info = stock_info(client)
        watch = []
        for symbol in DEFAULT_RTOKENS:
            underlying = info.get(symbol, {}).get("code") or symbol[1:-4]
            watch.append((symbol, underlying, perp_for(client, underlying)))
        cal = run(client, watch, REPO_ROOT / "data" / "research")
    print(json.dumps({k: cal[k] for k in ("windows", "by_kind", "pooled", "walk_forward", "methods")}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
