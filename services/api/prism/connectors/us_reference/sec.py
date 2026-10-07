"""Company event evidence from SEC EDGAR (official, primary source, free, read-only).

8-K filings are a company's "current report" of material events; the item codes say what kind
(e.g. 2.02 Results of Operations = earnings). This gives structured, timestamped, primary-source
event evidence (AGENTS.md §27) with no LLM classification needed. ETFs (e.g. SPY) do not file
8-Ks; that is reported as NOT_APPLICABLE, never as "no news".

SEC requires a descriptive User-Agent with a contact address: PRISM_SEC_CONTACT in .env.local
(defaults to an address on the PRISM domain, never a personal address).
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx

from ...config import REPO_ROOT, load_env_file

SOURCE = "SEC EDGAR submissions (official)"
ITEMS = {
    "1.01": "Material agreement", "1.02": "Agreement terminated", "1.03": "Bankruptcy",
    "2.01": "Acquisition or disposition", "2.02": "Results of operations (earnings)", "2.03": "New financial obligation",
    "2.05": "Exit or restructuring costs", "2.06": "Material impairment", "3.01": "Listing notice",
    "4.01": "Auditor change", "4.02": "Non-reliance on prior financials", "5.02": "Officer or director change",
    "5.07": "Shareholder vote", "7.01": "Regulation FD disclosure", "8.01": "Other material event",
    "9.01": "Financial statements and exhibits",
}
_TICKERS: tuple[float, dict[str, int]] | None = None
_SUBS: dict[int, tuple[float, dict]] = {}


@dataclass(frozen=True)
class Filing:
    form: str
    accepted_at: datetime
    items: tuple[str, ...]
    accession: str

    @property
    def description(self) -> str:
        named = [f"{i} {ITEMS[i]}" for i in self.items if i in ITEMS and i != "9.01"]
        return f"{self.form}: " + ("; ".join(named) if named else "material event")


def _user_agent() -> str:
    contact = (os.environ.get("PRISM_SEC_CONTACT") or load_env_file(REPO_ROOT / ".env.local").get("PRISM_SEC_CONTACT")
               or "ops@getprismpulse.xyz")
    return f"PRISM read-only research workbench {contact}"


def _get(client: httpx.Client, url: str) -> dict:
    r = client.get(url, headers={"User-Agent": _user_agent(), "Accept-Encoding": "gzip, deflate"})
    r.raise_for_status()
    return r.json()


def cik_for(ticker: str, client: httpx.Client) -> int | None:
    global _TICKERS
    if _TICKERS is None or time.monotonic() - _TICKERS[0] > 86400:
        data = _get(client, "https://www.sec.gov/files/company_tickers.json")
        _TICKERS = (time.monotonic(), {v["ticker"].upper(): int(v["cik_str"]) for v in data.values()})
    return _TICKERS[1].get(ticker.upper())


def recent_8k(ticker: str, since: datetime, client: httpx.Client | None = None) -> tuple[str, list[Filing]]:
    """("OK" | "NOT_APPLICABLE" | "UNAVAILABLE: ...", 8-K filings accepted at or after `since`)."""
    own = client is None
    http = client or httpx.Client(timeout=15)
    try:
        cik = cik_for(ticker, http)
        if cik is None:
            return "NOT_APPLICABLE", []  # e.g. ETFs: no 8-K filer
        hit = _SUBS.get(cik)
        if not hit or time.monotonic() - hit[0] > 900:
            hit = (time.monotonic(), _get(http, f"https://data.sec.gov/submissions/CIK{cik:010d}.json"))
            _SUBS[cik] = hit
        recent = hit[1]["filings"]["recent"]
        out = []
        for i, form in enumerate(recent["form"]):
            if form not in ("8-K", "8-K/A"):
                continue
            accepted = datetime.fromisoformat(recent["acceptanceDateTime"][i].replace("Z", "+00:00"))
            if accepted < since:
                continue
            items = tuple(x.strip() for x in (recent.get("items", [""] * len(recent["form"]))[i] or "").split(",") if x.strip())
            out.append(Filing(form, accepted, items, recent["accessionNumber"][i]))
        return "OK", sorted(out, key=lambda f: f.accepted_at, reverse=True)
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        return f"UNAVAILABLE: SEC EDGAR {type(exc).__name__}", []
    finally:
        if own:
            http.close()


def event_window_start(reference_time: datetime) -> datetime:
    """Events that can explain an off-hours move: from one trading day before the frozen reference."""
    return reference_time - timedelta(days=3)
