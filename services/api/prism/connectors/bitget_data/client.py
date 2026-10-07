"""Bitget US-stock data MCP (`bitget-mcp-server`, https://agent.bitget.com/mcp). Read-only, no credentials.

Verified 2026-10-07: initialize and tools/list work (server 4.0.5; tools `guide`, `do_query`). The
catalog lists equity quotes/history/profile/earnings calendar, news and sentiment entries. Every
`do_query` returned HTTP 503 from both the workstation and the VPS that day, so callers must handle
UNAVAILABLE results; nothing is substituted.

Roles in PRISM (AGENTS.md §9): reference consensus (underlying quote vs rToken), event evidence
(earnings calendar, news) for the Reality Engine.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx

from ...provenance import payload_hash, utc_now

MCP_URL = "https://agent.bitget.com/mcp"
PROTOCOL = "2025-06-18"


@dataclass(frozen=True)
class DataResult:
    entry_id: str
    params: dict[str, Any]
    ok: bool
    data: Any
    status: str  # AVAILABLE / UNAVAILABLE
    detail: str
    retrieved_at: str
    raw_payload_hash: str | None


class BitgetDataMCP:
    def __init__(self, url: str = MCP_URL, timeout: float = 30.0, transport: httpx.BaseTransport | None = None):
        self.url = url
        self._http = httpx.Client(timeout=timeout, transport=transport)
        self._session: str | None = None
        self._id = 0

    def _rpc(self, method: str, params: dict[str, Any] | None = None, notify: bool = False) -> Any:
        headers = {"content-type": "application/json", "accept": "application/json, text/event-stream"}
        if self._session:
            headers["mcp-session-id"] = self._session
        body: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            body["params"] = params
        if not notify:
            self._id += 1
            body["id"] = self._id
        response = self._http.post(self.url, json=body, headers=headers)
        response.raise_for_status()
        self._session = response.headers.get("mcp-session-id", self._session)
        if notify:
            return None
        text = response.text
        if "event-stream" in response.headers.get("content-type", ""):
            text = "\n".join(line[5:].strip() for line in text.splitlines() if line.startswith("data:"))
        message = json.loads(text)
        if "error" in message:
            raise RuntimeError(f"MCP error: {message['error']}")
        return message["result"]

    def _ensure(self) -> None:
        if self._session is None:
            self._rpc("initialize", {"protocolVersion": PROTOCOL, "capabilities": {}, "clientInfo": {"name": "prism", "version": "0.1"}})
            self._rpc("notifications/initialized", notify=True)

    def _tool(self, name: str, arguments: dict[str, Any]) -> Any:
        self._ensure()
        result = self._rpc("tools/call", {"name": name, "arguments": arguments})
        text = "".join(c.get("text", "") for c in result.get("content", []) if c.get("type") == "text")
        try:
            return json.loads(text)
        except ValueError:
            return text

    def catalog(self, category: str | None = None) -> Any:
        return self._tool("guide", {"category": category} if category else {})

    def query(self, entry_id: str, params: dict[str, Any]) -> DataResult:
        at = utc_now().isoformat()
        try:
            out = self._tool("do_query", {"entry_id": entry_id, "params": params})
        except (httpx.HTTPError, RuntimeError, ValueError) as exc:
            return DataResult(entry_id, params, False, None, "UNAVAILABLE", f"{type(exc).__name__}: {exc}"[:200], at, None)
        if isinstance(out, dict) and out.get("success") is True:
            return DataResult(entry_id, params, True, out.get("data"), "AVAILABLE", "ok", at, payload_hash(out))
        status = out.get("status_code") if isinstance(out, dict) else None
        return DataResult(entry_id, params, False, None, "UNAVAILABLE", f"upstream status {status}", at, payload_hash(out))

    def underlying_quote(self, symbol: str) -> DataResult:
        return self.query("equity_price_quote", {"symbol": symbol})

    def earnings_calendar(self, symbol: str) -> DataResult:
        return self.query("equity_calendar", {"symbol": symbol})
