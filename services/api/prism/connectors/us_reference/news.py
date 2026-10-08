"""Company news headlines (Yahoo Finance search, unofficial) with Qwen event classification (AGENTS.md §27).

Headlines are secondary evidence (an aggregator, not the company): SEC filings stay the primary
source. Qwen only classifies each headline into the §27 schema; it never produces a price or a
move. Classifications are cached per headline set, so Qwen runs only when new headlines appear.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import httpx

from ...config import REPO_ROOT

SOURCE = "Yahoo Finance news search (unofficial, headline aggregator)"
ENDPOINT = "https://query2.finance.yahoo.com/v1/finance/search"
_NEWS: dict[str, tuple[float, list["Headline"]]] = {}


@dataclass(frozen=True)
class Headline:
    id: str
    title: str
    publisher: str
    published_at: datetime
    link: str


def _relevant(title: str, ticker: str, name: str | None) -> bool:
    if re.search(rf"\b{re.escape(ticker)}\b", title):
        return True
    if name:
        first = re.split(r"[\s,.]", name.strip())[0]
        if len(first) >= 4 and re.search(rf"\b{re.escape(first)}\b", title, re.IGNORECASE):
            return True
    return False


def headlines(ticker: str, name: str | None = None, ttl: float = 900) -> list[Headline]:
    hit = _NEWS.get(ticker)
    if hit and time.monotonic() - hit[0] < ttl:
        return hit[1]
    try:
        r = httpx.get(ENDPOINT, params={"q": ticker, "newsCount": 12, "quotesCount": 0},
                      headers={"User-Agent": "PRISM read-only research workbench"}, timeout=15)
        r.raise_for_status()
        items = r.json().get("news", [])
    except (httpx.HTTPError, ValueError):
        return []
    out = []
    for n in items:
        title = (n.get("title") or "").strip()
        if not title or not n.get("providerPublishTime") or not _relevant(title, ticker, name):
            continue
        out.append(Headline(
            id=hashlib.sha256((n.get("uuid") or title).encode()).hexdigest()[:12], title=title,
            publisher=n.get("publisher") or "unknown",
            published_at=datetime.fromtimestamp(int(n["providerPublishTime"]), timezone.utc), link=n.get("link") or "",
        ))
    out = sorted(out, key=lambda h: h.published_at, reverse=True)[:6]
    _NEWS[ticker] = (time.monotonic(), out)
    return out


CACHE_FILE = REPO_ROOT / "data" / "research" / "news_classified.json"


def _load() -> dict:
    try:
        return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def classified(ticker: str, items: list[Headline], classify=None) -> tuple[dict[str, dict], str]:
    """({headline id: classification}, status).

    Qwen classification is slow (can exceed a minute), so only the background capture service passes
    `classify` (QwenClient.classify_headlines); web requests read the shared on-disk cache and report
    PENDING until the background run has classified the current headlines.
    """
    if not items:
        return {}, "NO_HEADLINES"
    cache = _load()
    done = {h.id: cache[h.id] for h in items if h.id in cache}
    missing = [h for h in items if h.id not in cache]
    if not missing:
        return done, "CLASSIFIED"
    if classify is None:
        return done, "PENDING_CLASSIFICATION"
    try:
        result = classify(ticker, missing)
    except Exception as exc:  # Qwen unavailable or invalid output: headlines stay unclassified, never guessed
        return done, f"UNCLASSIFIED ({type(exc).__name__})"
    cache = _load() | result
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = CACHE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(cache, indent=1, default=str), encoding="utf-8")
    tmp.replace(CACHE_FILE)
    return done | result, "CLASSIFIED"
