"""Provenance primitives (AGENTS.md §1.1, §13, §57).

Every number PRISM shows must be traceable to one of these records.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

T = TypeVar("T")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class DataMode(StrEnum):
    """Data-quality states every module must support (§57)."""

    LIVE = "LIVE"
    DELAYED = "DELAYED"
    STALE = "STALE"
    LIMITED = "LIMITED"
    UNAVAILABLE = "UNAVAILABLE"
    REPLAY = "REPLAY"
    HYPOTHETICAL = "HYPOTHETICAL"


class CapabilityStatus(StrEnum):
    """M1 classification for every Bitget dependency (§65)."""

    VERIFIED = "VERIFIED"
    UNAVAILABLE = "UNAVAILABLE"
    WHITELIST_REQUIRED = "WHITELIST_REQUIRED"
    UNVERIFIED = "UNVERIFIED"


class Provenance(BaseModel):
    """Where a value came from. Required on every visible number (§1.1)."""

    model_config = ConfigDict(frozen=True)

    source: str = Field(description="Provider and endpoint/tool, e.g. 'bitget:/api/v3/market/tickers'.")
    source_timestamp: datetime | None = Field(description="Timestamp reported by the provider, if any.")
    retrieved_at: datetime
    data_mode: DataMode
    raw_payload_hash: str | None = None
    parser_version: str | None = None

    @property
    def is_live(self) -> bool:
        return self.data_mode == DataMode.LIVE

    @property
    def is_hypothetical(self) -> bool:
        return self.data_mode == DataMode.HYPOTHETICAL

    @property
    def is_replay(self) -> bool:
        return self.data_mode == DataMode.REPLAY

    @model_validator(mode="after")
    def _timestamps_are_aware(self) -> "Provenance":
        for name in ("source_timestamp", "retrieved_at"):
            value = getattr(self, name)
            if value is not None and value.tzinfo is None:
                raise ValueError(f"{name} must be timezone-aware")
        return self


class Observed(BaseModel, Generic[T]):
    """A value paired with its provenance. `value` is None only when unavailable."""

    model_config = ConfigDict(frozen=True)

    value: T | None
    provenance: Provenance
    unavailable_reason: str | None = None

    @model_validator(mode="after")
    def _unavailable_has_reason(self) -> "Observed[T]":
        if self.value is None and not self.unavailable_reason:
            raise ValueError("an unavailable value must state why")
        if self.value is None and self.provenance.data_mode not in (DataMode.UNAVAILABLE, DataMode.LIMITED):
            raise ValueError("a missing value must use UNAVAILABLE or LIMITED data mode")
        return self

    @classmethod
    def unavailable(cls, source: str, reason: str, mode: DataMode = DataMode.UNAVAILABLE) -> "Observed[T]":
        return cls(
            value=None,
            provenance=Provenance(source=source, source_timestamp=None, retrieved_at=utc_now(), data_mode=mode),
            unavailable_reason=reason,
        )
