"""Read-only Bitget UTA REST client (AGENTS.md §4–§8, §60–§61).

Signing follows Bitget's UTA quick start: base64(HMAC_SHA256(secret,
timestamp_ms + METHOD + requestPath + ["?" + query] + body)) with ACCESS-KEY,
ACCESS-SIGN, ACCESS-TIMESTAMP and ACCESS-PASSPHRASE headers.

This client can only issue GET requests. PRISM never needs write access, so
there is deliberately no code path that sends POST/PUT/DELETE.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode

import httpx

from ...config import Settings
from ...provenance import RawStore, payload_hash, utc_now

PARSER_VERSION = "bitget-rest-v0.1"
SUCCESS_CODE = "00000"


def sign(secret: str, timestamp_ms: str, method: str, request_path: str, query: str = "", body: str = "") -> str:
    prehash = timestamp_ms + method.upper() + request_path + (f"?{query}" if query else "") + body
    digest = hmac.new(secret.encode("utf-8"), prehash.encode("utf-8"), hashlib.sha256).digest()
    return base64.b64encode(digest).decode("ascii")


@dataclass(frozen=True)
class BitgetResponse:
    endpoint: str
    authenticated: bool
    request_ts: datetime
    http_status: int | None
    code: str | None
    msg: str | None
    data: Any
    provider_ts: datetime | None
    raw_payload_hash: str | None
    error: str | None = None
    raw: Any = None

    @property
    def ok(self) -> bool:
        return self.http_status == 200 and self.code == SUCCESS_CODE


def _provider_ts(body: Any) -> datetime | None:
    if isinstance(body, dict) and body.get("requestTime"):
        try:
            return datetime.fromtimestamp(int(body["requestTime"]) / 1000, tz=timezone.utc)
        except (TypeError, ValueError):
            return None
    return None


class BitgetClient:
    def __init__(self, settings: Settings, raw_store: RawStore | None = None, timeout: float = 10.0):
        self.settings = settings
        self.raw_store = raw_store
        self._http = httpx.Client(base_url=settings.bitget_base_url, timeout=timeout)

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "BitgetClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def get(self, path: str, params: dict[str, Any] | None = None, *, authenticated: bool = False, demo_env: bool = False) -> BitgetResponse:
        """`demo_env` routes an unsigned market call to Bitget's paper environment, whose tiers differ from live (verified 2026-10-07)."""
        query = urlencode({k: v for k, v in (params or {}).items() if v is not None})
        endpoint = f"{path}?{query}" if query else path
        request_ts = utc_now()
        headers = {"accept": "application/json", "locale": "en-US"}
        if authenticated:
            if not self.settings.has_bitget_credentials:
                return BitgetResponse(endpoint, True, request_ts, None, None, None, None, None, None, error="AUTH_NOT_CONFIGURED")
            timestamp_ms = str(int(time.time() * 1000))
            headers.update(
                {
                    "ACCESS-KEY": self.settings.bitget_api_key.reveal(),  # type: ignore[union-attr]
                    "ACCESS-SIGN": sign(self.settings.bitget_api_secret.reveal(), timestamp_ms, "GET", path, query),  # type: ignore[union-attr]
                    "ACCESS-TIMESTAMP": timestamp_ms,
                    "ACCESS-PASSPHRASE": self.settings.bitget_passphrase.reveal(),  # type: ignore[union-attr]
                    "Content-Type": "application/json",
                }
            )
            if self.settings.bitget_demo:
                headers["paptrading"] = "1"
        elif demo_env:
            headers["paptrading"] = "1"
        try:
            response = self._http.get(endpoint, headers=headers)
        except httpx.HTTPError as exc:
            return BitgetResponse(endpoint, authenticated, request_ts, None, None, None, None, None, None, error=type(exc).__name__)
        try:
            body: Any = response.json()
        except ValueError:
            body = {"_non_json_body": response.text[:500]}
        provider_ts = _provider_ts(body)
        digest = payload_hash(body)
        if self.raw_store is not None:
            self.raw_store.write(
                "bitget", endpoint, body,
                request_ts=request_ts, provider_ts=provider_ts,
                http_status=response.status_code, parser_version=PARSER_VERSION,
            )
        code = body.get("code") if isinstance(body, dict) else None
        msg = body.get("msg") if isinstance(body, dict) else None
        data = body.get("data") if isinstance(body, dict) else None
        return BitgetResponse(
            endpoint, authenticated, request_ts, response.status_code,
            str(code) if code is not None else None, msg, data, provider_ts, digest, raw=body,
        )
