"""Read-only Bitget UTA private WebSocket probe (AGENTS.md §5–§6).

Login signs timestamp + "GET" + "/user/verify" per Bitget's UTA quick start.
The docs do not state the timestamp unit for WebSocket login, so the probe
tries seconds first and milliseconds second, and reports which one worked.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any

import websockets

from ...config import Settings
from .client import sign


@dataclass
class WsProbeResult:
    login_ok: bool = False
    login_timestamp_unit: str | None = None
    login_response: Any = None
    subscribe_responses: list[Any] = field(default_factory=list)
    pushes: dict[str, list[Any]] = field(default_factory=dict)
    error: str | None = None


async def _try_login(ws: Any, settings: Settings, unit: str) -> Any:
    ts = str(int(time.time())) if unit == "s" else str(int(time.time() * 1000))
    await ws.send(json.dumps({
        "op": "login",
        "args": [{
            "apiKey": settings.bitget_api_key.reveal(),  # type: ignore[union-attr]
            "passphrase": settings.bitget_passphrase.reveal(),  # type: ignore[union-attr]
            "timestamp": ts,
            "sign": sign(settings.bitget_api_secret.reveal(), ts, "GET", "/user/verify"),  # type: ignore[union-attr]
        }],
    }))
    return json.loads(await asyncio.wait_for(ws.recv(), timeout=10))


async def probe_private_ws(settings: Settings, topics: list[str], listen_seconds: float = 20.0) -> WsProbeResult:
    result = WsProbeResult()
    if not settings.has_bitget_credentials:
        result.error = "AUTH_NOT_CONFIGURED"
        return result
    try:
        for unit in ("s", "ms"):
            async with websockets.connect(settings.bitget_ws_private_url, open_timeout=10) as ws:
                response = await _try_login(ws, settings, unit)
                result.login_response = response
                if response.get("event") == "login" and str(response.get("code")) in ("0", "00000"):
                    result.login_ok = True
                    result.login_timestamp_unit = unit
                    await ws.send(json.dumps({"op": "subscribe", "args": [{"instType": "UTA", "topic": t} for t in topics]}))
                    deadline = time.monotonic() + listen_seconds
                    while time.monotonic() < deadline:
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=max(0.1, deadline - time.monotonic()))
                        except asyncio.TimeoutError:
                            break
                        if raw == "pong":
                            continue
                        message = json.loads(raw)
                        if message.get("event") in ("subscribe", "error"):
                            result.subscribe_responses.append(message)
                        elif "data" in message:
                            topic = (message.get("arg") or {}).get("topic", "unknown")
                            result.pushes.setdefault(topic, []).append(message)
                            if all(result.pushes.get(t) for t in topics):
                                break
                    return result
    except Exception as exc:  # network/protocol failures are reported, never raised into callers
        result.error = f"{type(exc).__name__}: {exc}"[:300]
    return result
