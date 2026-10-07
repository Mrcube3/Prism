import base64
import hashlib
import hmac
import json
from datetime import datetime, timezone

import pytest

from prism.config import Secret, load_settings
from prism.connectors.bitget import sign
from prism.provenance import DataMode, Observed, Provenance, RawStore, key_paths, payload_hash, sanitize


def test_sign_matches_documented_prehash():
    expected = base64.b64encode(
        hmac.new(b"s3cret", b"1700000000000GET/api/v3/account/assets?coin=USDT", hashlib.sha256).digest()
    ).decode()
    assert sign("s3cret", "1700000000000", "get", "/api/v3/account/assets", "coin=USDT") == expected


def test_sign_omits_question_mark_without_query():
    expected = base64.b64encode(hmac.new(b"k", b"1GET/user/verify", hashlib.sha256).digest()).decode()
    assert sign("k", "1", "GET", "/user/verify") == expected


def test_payload_hash_is_key_order_independent():
    assert payload_hash({"a": 1, "b": [1, 2]}) == payload_hash({"b": [1, 2], "a": 1})
    assert payload_hash({"a": 1}) != payload_hash({"a": 2})


def test_sanitize_redacts_identifiers_recursively_and_keeps_values():
    raw = {"data": {"userId": "123", "assets": [{"coin": "USDT", "equity": "10"}], "ips": "1.2.3.4", "apiKey": "x"}}
    clean = sanitize(raw)
    assert clean["data"]["userId"] == "[REDACTED]"
    assert clean["data"]["ips"] == "[REDACTED]"
    assert clean["data"]["apiKey"] == "[REDACTED]"
    assert clean["data"]["assets"] == [{"coin": "USDT", "equity": "10"}]
    assert raw["data"]["userId"] == "123"  # original untouched


def test_key_paths_inventory_has_no_values():
    paths = key_paths({"data": {"assets": [{"coin": "BTC", "equity": "1"}]}})
    assert paths == ["data", "data.assets", "data.assets[].coin", "data.assets[].equity"]


def test_raw_store_appends_hashed_record(tmp_path):
    store = RawStore(tmp_path)
    now = datetime.now(timezone.utc)
    digest = store.write("bitget", "/api/v3/market/tickers?symbol=X", {"code": "00000"}, request_ts=now, provider_ts=None, http_status=200, parser_version="t")
    files = list(tmp_path.rglob("*.jsonl"))
    assert len(files) == 1
    record = json.loads(files[0].read_text(encoding="utf-8").strip())
    assert record["raw_payload_hash"] == digest
    assert record["payload"] == {"code": "00000"}


def test_provenance_requires_aware_timestamps():
    with pytest.raises(ValueError):
        Provenance(source="x", source_timestamp=None, retrieved_at=datetime(2026, 1, 1), data_mode=DataMode.LIVE)


def test_unavailable_value_must_say_why_and_cannot_claim_live():
    value = Observed[float].unavailable("bitget:/reality-orderbook", "whitelist access unavailable", DataMode.LIMITED)
    assert value.value is None and value.provenance.data_mode == DataMode.LIMITED
    live = Provenance(source="x", source_timestamp=None, retrieved_at=datetime.now(timezone.utc), data_mode=DataMode.LIVE)
    with pytest.raises(ValueError):
        Observed[float](value=None, provenance=live, unavailable_reason="missing")
    with pytest.raises(ValueError):
        Observed[float](value=None, provenance=live.model_copy(update={"data_mode": DataMode.UNAVAILABLE}))


def test_provenance_flags_follow_mode():
    p = Provenance(source="x", source_timestamp=None, retrieved_at=datetime.now(timezone.utc), data_mode=DataMode.HYPOTHETICAL)
    assert p.is_hypothetical and not p.is_live and not p.is_replay


def test_secrets_never_render(tmp_path):
    env = tmp_path / ".env.local"
    env.write_text("BITGET_API_KEY=abc\nBITGET_API_SECRET=def\nBITGET_API_PASSPHRASE=ghi\n", encoding="utf-8")
    settings = load_settings(env)
    assert settings.has_bitget_credentials
    for text in (repr(settings), str(settings.bitget_api_secret)):
        assert "abc" not in text and "def" not in text and "ghi" not in text
    assert isinstance(settings.bitget_passphrase, Secret)


def test_legacy_passphrase_name_is_accepted(tmp_path, monkeypatch):
    for name in ("BITGET_API_KEY", "BITGET_API_SECRET", "BITGET_API_PASSPHRASE", "BITGET_PASSPHRASE"):
        monkeypatch.delenv(name, raising=False)
    env = tmp_path / ".env.local"
    env.write_text("BITGET_API_KEY=a\nBITGET_API_SECRET=b\nBITGET_PASSPHRASE=c\n", encoding="utf-8")
    assert load_settings(env).has_bitget_credentials
