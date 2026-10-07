"""Continuous closure-window capture (AGENTS.md §47, §77). Read-only.

    uv run --project services/api python scripts/capture_weekend.py          # run forever
    uv run --project services/api python scripts/capture_weekend.py --once   # one cycle, then exit

Writes data/capture/<UTC date>/*.jsonl and raw payloads to data/raw/ (both git-ignored).
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "services" / "api"))

from prism.capture import Collector  # noqa: E402
from prism.config import load_settings  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--once", action="store_true", help="run every job once and exit")
    parser.add_argument("--data-dir", type=Path, default=REPO_ROOT / "data")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # one line per request would flood the journal
    collector = Collector(load_settings(), root=args.data_dir)
    if args.once:
        collector.run_once()
        return 0
    collector.run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
