"""M1: verify every Bitget primitive PRISM depends on (AGENTS.md §65, §75).

Run from the repo root:

    uv run --project services/api python scripts/verify_bitget.py

Outputs
- data/research/bitget_capabilities.json  machine-readable report (no private values)
- docs/BITGET_VERIFICATION.md             the generated section between the AUTO markers
- data/raw/...                            raw payloads (git-ignored; may contain account data)

Read-only: every request is a GET or a WebSocket subscribe. Nothing is placed, set or transferred.
"""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "services" / "api"))

from prism.config import load_settings  # noqa: E402
from prism.connectors.bitget import BitgetClient, BitgetResponse  # noqa: E402
from prism.connectors.bitget.ws import probe_private_ws  # noqa: E402
from prism.provenance import CapabilityStatus, RawStore, key_paths, sanitize, utc_now  # noqa: E402

REPORT_PATH = REPO_ROOT / "data" / "research" / "bitget_capabilities.json"
DOC_PATH = REPO_ROOT / "docs" / "BITGET_VERIFICATION.md"
AUTO_START = "<!-- AUTO:verify_bitget START -->"
AUTO_END = "<!-- AUTO:verify_bitget END -->"

RTOKEN = "RNVDAUSDT"
CRYPTO_PERP = "BTCUSDT"


@dataclass
class Probe:
    id: str
    path: str
    params: dict[str, Any] = field(default_factory=dict)
    auth: bool = False
    # Documented as requiring Reality whitelist/BD approval (AGENTS.md §4).
    whitelist_documented: bool = False
    # Public reference data whose sanitized sample may be shown in the report.
    show_sample: bool = False
    expected_fields: tuple[str, ...] = ()
    note: str = ""


PROBES: list[Probe] = [
    Probe("reality_stock_info", "/api/v3/reality/market/stock-info", show_sample=True,
          expected_fields=("symbol", "code", "name", "tradingPeriod", "weekendTradable")),
    Probe("market_states", "/api/v3/reality/market/states", show_sample=True),
    Probe("market_calendar", "/api/v3/reality/market/calendar", show_sample=True),
    Probe("rtoken_instruments", "/api/v3/market/instruments", {"category": "SPOT", "symbol": RTOKEN}, show_sample=True),
    Probe("rtoken_ticker", "/api/v3/market/tickers", {"category": "SPOT", "symbol": RTOKEN}, show_sample=True),
    Probe("rtoken_candles", "/api/v3/market/candles", {"category": "SPOT", "symbol": RTOKEN, "interval": "1m", "limit": "5"}, show_sample=True),
    Probe("rtoken_public_orderbook", "/api/v3/market/orderbook", {"category": "SPOT", "symbol": RTOKEN, "limit": "5"}, show_sample=True,
          note="Public UTA order book; checked to see whether it serves rTokens at all (distinct from the whitelisted Reality book)."),
    Probe("crypto_perp_ticker", "/api/v3/market/tickers", {"category": "USDT-FUTURES", "symbol": CRYPTO_PERP}, show_sample=True),
    Probe("position_tier", "/api/v3/market/position-tier", {"category": "USDT-FUTURES", "symbol": CRYPTO_PERP}, show_sample=True),
    Probe("discount_rate", "/api/v3/market/discount-rate", show_sample=True,
          note="Candidate source of tiered collateral ratios."),
    Probe("custom_collateral_coins", "/api/v3/account/custom-collateral-coins", show_sample=True,
          note="Listed as public by CCXT; also probed authenticated below."),
    Probe("account_info", "/api/v3/account/info", auth=True, note="Used to confirm the key's permission scope."),
    Probe("account_settings", "/api/v3/account/settings", auth=True),
    Probe("account_assets", "/api/v3/account/assets", auth=True),
    Probe("positions", "/api/v3/position/current-position", {"category": "USDT-FUTURES"}, auth=True),
    Probe("collateral_type", "/api/v3/account/collateral-type", auth=True, show_sample=True),
    Probe("custom_collateral_coins_auth", "/api/v3/account/custom-collateral-coins", auth=True, show_sample=True),
    Probe("eligible_discount_rate", "/api/v3/account/eligible-discount-rate", auth=True, show_sample=True,
          note="Candidate source of account-specific tiered collateral ratios."),
    Probe("fee_rate", "/api/v3/account/fee-rate", {"category": "USDT-FUTURES", "symbol": CRYPTO_PERP}, auth=True, show_sample=True),
    Probe("reality_depth", "/api/v3/account/reality-orderbook", {"symbol": RTOKEN}, auth=True, whitelist_documented=True),
    Probe("reality_fills", "/api/v3/account/reality-fills", {"symbol": RTOKEN}, auth=True, whitelist_documented=True),
]

# Fields AGENTS.md §5–§6 says the private WebSocket exposes.
WS_ACCOUNT_FIELDS = ("totalEquity", "effEquity", "mmr", "imr", "mgnRatio", "positionMgnRatio", "unrealisedPnL")
WS_COIN_FIELDS = ("coin", "balance", "balanceOriginal", "equity", "usdValue", "available", "borrow", "debts")
WS_POSITION_FIELDS = ("leverage", "unrealisedPnl", "liqPrice", "mmr", "marginRate", "markPrice")


def sample(data: Any, limit: int = 3) -> Any:
    if isinstance(data, list):
        return sanitize(data[:limit])
    if isinstance(data, dict):
        trimmed = {k: (v[:limit] if isinstance(v, list) else v) for k, v in data.items()}
        return sanitize(trimmed)
    return sanitize(data)


def field_names(data: Any) -> set[str]:
    return {path.split(".")[-1].replace("[]", "") for path in key_paths(data)}


def classify(probe: Probe, response: BitgetResponse, auth_works: bool) -> tuple[CapabilityStatus, str]:
    if response.ok:
        return CapabilityStatus.VERIFIED, "Response returned code 00000."
    if response.error == "AUTH_NOT_CONFIGURED":
        return CapabilityStatus.UNVERIFIED, "Bitget API credentials are not configured."
    if response.error:
        return CapabilityStatus.UNAVAILABLE, f"Request failed before a response: {response.error}."
    detail = f"HTTP {response.http_status}, code {response.code}: {response.msg}"
    if response.http_status == 404:
        return CapabilityStatus.UNVERIFIED, f"Endpoint not served to this key/environment (404); this is not evidence of a whitelist refusal. {detail}"
    if probe.whitelist_documented and auth_works:
        return CapabilityStatus.WHITELIST_REQUIRED, f"Authenticated request refused while other signed calls succeed; documented whitelist requirement. {detail}"
    return CapabilityStatus.UNVERIFIED, detail


def run_rest(client: BitgetClient) -> tuple[list[dict[str, Any]], dict[str, BitgetResponse]]:
    responses = {probe.id: client.get(probe.path, probe.params, authenticated=probe.auth) for probe in PROBES}
    auth_works = any(r.ok for p, r in zip(PROBES, responses.values()) if p.auth and not p.whitelist_documented)
    rows = []
    for probe in PROBES:
        response = responses[probe.id]
        status, reason = classify(probe, response, auth_works)
        names = field_names(response.data) if response.data is not None else set()
        row: dict[str, Any] = {
            "id": probe.id,
            "method": "GET",
            "endpoint": response.endpoint,
            "authenticated": probe.auth,
            "status": status.value,
            "reason": reason,
            "http_status": response.http_status,
            "code": response.code,
            "msg": response.msg,
            "request_ts": response.request_ts.isoformat(),
            "provider_ts": response.provider_ts.isoformat() if response.provider_ts else None,
            "raw_payload_hash": response.raw_payload_hash,
            "data_field_paths": key_paths(response.data) if response.data is not None else [],
            "note": probe.note or None,
        }
        if probe.expected_fields:
            row["missing_expected_fields"] = [f for f in probe.expected_fields if f not in names] if response.ok else None
        if probe.show_sample and response.ok:
            row["sample"] = sample(response.data)
        rows.append(row)
    return rows, responses


def summarize_reality_universe(response: BitgetResponse) -> dict[str, Any] | None:
    if not response.ok or not isinstance(response.data, list):
        return None
    tradable = [row for row in response.data if str(row.get("weekendTradable", "")).lower() == "yes"]
    return {
        "rtoken_count": len(response.data),
        "weekend_tradable_yes": len(tradable),
        "weekend_tradable_values": sorted({str(row.get("weekendTradable")) for row in response.data}),
        "example_symbols": [row.get("symbol") for row in response.data[:10]],
    }


def key_permissions(response: BitgetResponse) -> dict[str, Any] | None:
    """Only permission-scope fields from account/info; identifiers are never copied."""
    if not response.ok or not isinstance(response.data, dict):
        return None
    return {k: v for k, v in response.data.items() if "perm" in k.lower() or "auth" in k.lower()}


def summarize_ws(result: Any) -> dict[str, Any]:
    out: dict[str, Any] = {
        "login_ok": result.login_ok,
        "login_timestamp_unit": result.login_timestamp_unit,
        "login_response": sanitize(result.login_response),
        "subscribe_responses": sanitize(result.subscribe_responses),
        "error": result.error,
        "topics": {},
    }
    for topic, messages in result.pushes.items():
        data = messages[0].get("data")
        names = field_names(data)
        entry: dict[str, Any] = {"push_count": len(messages), "data_field_paths": key_paths(data)}
        if topic == "account":
            entry["missing_spec_fields"] = [f for f in WS_ACCOUNT_FIELDS + WS_COIN_FIELDS if f not in names]
        if topic == "position":
            entry["missing_spec_fields"] = [f for f in WS_POSITION_FIELDS if f not in names]
        out["topics"][topic] = entry
    return out


def ws_status(summary: dict[str, Any], topic: str, has_credentials: bool) -> tuple[str, str]:
    if not has_credentials:
        return CapabilityStatus.UNVERIFIED.value, "Bitget API credentials are not configured."
    if not summary["login_ok"]:
        return CapabilityStatus.UNVERIFIED.value, f"WebSocket login failed: {summary['error'] or summary['login_response']}"
    if topic in summary["topics"]:
        return CapabilityStatus.VERIFIED.value, f"Login ok ({summary['login_timestamp_unit']} timestamp); received a '{topic}' push."
    return CapabilityStatus.UNVERIFIED.value, f"Login ok but no '{topic}' push arrived during the listen window."


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        AUTO_START,
        f"_Generated by `scripts/verify_bitget.py` at {report['timestamp']}. Re-run to refresh; edit outside the markers._",
        "",
        f"Signed-request environment: **{report['account_environment']}**.",
        "",
        "| Capability | Status | Request | Result |",
        "| --- | --- | --- | --- |",
    ]
    for row in report["rest"]:
        auth = " (signed)" if row["authenticated"] else ""
        result = (row["reason"] or "").replace("|", "\\|")
        lines.append(f"| `{row['id']}` | **{row['status']}** | `GET {row['endpoint']}`{auth} | {result} |")
    for topic in ("account", "position"):
        row = report["summary"][f"ws_{topic}"]
        lines.append(f"| `ws_{topic}` | **{row['status']}** | private WS `{topic}` topic | {row['reason']} |")
    lines += ["", "Machine-readable report: [`data/research/bitget_capabilities.json`](../data/research/bitget_capabilities.json).", AUTO_END]
    return "\n".join(lines)


def write_doc(block: str) -> None:
    text = DOC_PATH.read_text(encoding="utf-8") if DOC_PATH.exists() else f"# Bitget verification\n\n{AUTO_START}\n{AUTO_END}\n"
    if AUTO_START not in text:
        text += f"\n{AUTO_START}\n{AUTO_END}\n"
    head, _, rest = text.partition(AUTO_START)
    _, _, tail = rest.partition(AUTO_END)
    DOC_PATH.write_text(head + block + tail, encoding="utf-8")


def main() -> int:
    settings = load_settings()
    store = RawStore(settings.raw_store_root)
    with BitgetClient(settings, raw_store=store) as client:
        rows, responses = run_rest(client)
    ws = asyncio.run(probe_private_ws(settings, ["account", "position"]))
    ws_summary = summarize_ws(ws)

    by_id = {row["id"]: row for row in rows}
    summary: dict[str, Any] = {row["id"]: {"status": row["status"], "reason": row["reason"]} for row in rows}
    for topic in ("account", "position"):
        status, reason = ws_status(ws_summary, topic, settings.has_bitget_credentials)
        summary[f"ws_{topic}"] = {"status": status, "reason": reason}
    # Questions M1 cannot close on its own; see docs/OPEN_QUESTIONS.md.
    summary["collateral_ratio_tiers"] = {"status": "UNVERIFIED", "reason": "Requires manual review of discount_rate / eligible_discount_rate / custom_collateral_coins samples."}
    summary["reconciliation"] = {"status": "NOT_STARTED", "reason": "M2."}

    report = {
        "timestamp": utc_now().isoformat(),
        "base_url": settings.bitget_base_url,
        "credentials_configured": settings.has_bitget_credentials,
        # Signed results from a demo key describe Bitget's paper environment, not a real account.
        "account_environment": "DEMO_PAPTRADING" if settings.bitget_demo else "LIVE",
        "summary": summary,
        "reality_universe": summarize_reality_universe(responses["reality_stock_info"]),
        "key_permissions": key_permissions(responses["account_info"]),
        "rest": rows,
        "websocket": ws_summary,
    }
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    report["summary"] = summary
    write_doc(render_markdown({**report, "rest": rows}))

    width = max(len(k) for k in summary)
    for key, value in summary.items():
        print(f"{key:<{width}}  {value['status']:<18}  {value['reason'][:110]}")
    print(f"\nReport: {REPORT_PATH.relative_to(REPO_ROOT)}")
    return 0 if by_id["reality_stock_info"]["status"] == "VERIFIED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
