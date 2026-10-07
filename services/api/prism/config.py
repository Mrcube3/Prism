"""Environment handling (AGENTS.md §60).

Reads the repo-root .env.local without adding a dependency. Secrets are held in
SecretStr-like wrappers whose repr never shows the value.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


class Secret:
    __slots__ = ("_value",)

    def __init__(self, value: str):
        self._value = value

    def reveal(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "Secret('***')"

    __str__ = __repr__


def load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _first(env: dict[str, str], *names: str) -> str | None:
    for name in names:
        value = (os.environ.get(name) or env.get(name) or "").strip()
        if value:
            return value
    return None


@dataclass(frozen=True)
class Settings:
    bitget_base_url: str
    bitget_ws_private_url: str
    bitget_ws_public_url: str
    bitget_api_key: Secret | None = field(repr=False)
    bitget_api_secret: Secret | None = field(repr=False)
    bitget_passphrase: Secret | None = field(repr=False)
    # Demo-trading keys only work against Bitget's paper environment ("paptrading: 1").
    # Demo account data is never real account state and must not be labelled LIVE.
    bitget_demo: bool = False
    raw_store_root: Path = REPO_ROOT / "data" / "raw"

    @property
    def has_bitget_credentials(self) -> bool:
        return bool(self.bitget_api_key and self.bitget_api_secret and self.bitget_passphrase)


def load_settings(env_path: Path | None = None) -> Settings:
    env = load_env_file(env_path or REPO_ROOT / ".env.local")
    key = _first(env, "BITGET_API_KEY")
    secret = _first(env, "BITGET_API_SECRET")
    # .env.example historically used BITGET_PASSPHRASE; .env.local uses BITGET_API_PASSPHRASE.
    passphrase = _first(env, "BITGET_API_PASSPHRASE", "BITGET_PASSPHRASE")
    demo = (_first(env, "BITGET_DEMO_TRADING") or "").lower() in ("1", "true", "yes")
    return Settings(
        bitget_base_url=_first(env, "BITGET_API_BASE_URL") or "https://api.bitget.com",
        bitget_ws_private_url=_first(env, "BITGET_WS_PRIVATE_URL")
        or ("wss://wspap.bitget.com/v3/ws/private" if demo else "wss://ws.bitget.com/v3/ws/private"),
        bitget_ws_public_url=_first(env, "BITGET_WS_PUBLIC_URL") or "wss://ws.bitget.com/v3/ws/public",
        bitget_api_key=Secret(key) if key else None,
        bitget_api_secret=Secret(secret) if secret else None,
        bitget_passphrase=Secret(passphrase) if passphrase else None,
        bitget_demo=demo,
    )
