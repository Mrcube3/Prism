"""Raw provider payload storage (AGENTS.md §13, §60).

Payloads are stored before any transformation, keyed by a SHA-256 of their
canonical JSON. Stored files live under data/raw/, which is git-ignored because
account payloads are private. `sanitize` produces a copy safe for public replay
bundles and documentation.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from .models import utc_now

# Keys whose values identify a person/account or are credentials. Matched case-insensitively.
SENSITIVE_KEYS = frozenset(
    {
        "uid", "userid", "user_id", "parentid", "inviterid", "channelcode", "channel",
        "apikey", "api_key", "passphrase", "secret", "sign", "signature", "token",
        "ips", "ip", "address", "email", "mobile", "phone", "accountid", "orderid", "clientoid",
        "tradeid", "label", "remark",
    }
)
REDACTED = "[REDACTED]"


def canonical_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def payload_hash(payload: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def sanitize(payload: Any) -> Any:
    """Return a copy with identifying/credential values replaced by REDACTED."""
    if isinstance(payload, dict):
        return {
            key: (REDACTED if key.lower().replace("-", "") in SENSITIVE_KEYS else sanitize(value))
            for key, value in payload.items()
        }
    if isinstance(payload, list):
        return [sanitize(item) for item in payload]
    return payload


def key_paths(payload: Any, prefix: str = "") -> list[str]:
    """Field inventory without values, e.g. ['data.assets[].coin', ...]."""
    paths: set[str] = set()
    if isinstance(payload, dict):
        for key, value in payload.items():
            path = f"{prefix}.{key}" if prefix else key
            paths.add(path)
            paths.update(key_paths(value, path))
    elif isinstance(payload, list):
        for item in payload:
            paths.update(key_paths(item, f"{prefix}[]"))
    return sorted(paths)


class RawStore:
    """Append-only JSONL store of raw provider responses."""

    def __init__(self, root: Path):
        self.root = root

    def write(
        self,
        provider: str,
        endpoint: str,
        payload: Any,
        *,
        request_ts: datetime,
        provider_ts: datetime | None,
        http_status: int | None,
        parser_version: str,
    ) -> str:
        digest = payload_hash(payload)
        stamp = utc_now()
        slug = re.sub(r"[^a-zA-Z0-9]+", "_", endpoint.split("?")[0]).strip("_") or "root"
        directory = self.root / stamp.strftime("%Y-%m-%d") / provider
        directory.mkdir(parents=True, exist_ok=True)
        record = {
            "provider": provider,
            "endpoint": endpoint,
            "request_ts": request_ts.isoformat(),
            "provider_ts": provider_ts.isoformat() if provider_ts else None,
            "stored_at": stamp.isoformat(),
            "http_status": http_status,
            "parser_version": parser_version,
            "raw_payload_hash": digest,
            "payload": payload,
        }
        with (directory / f"{slug}.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(canonical_json(record) + "\n")
        return digest
