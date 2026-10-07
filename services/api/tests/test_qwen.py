import json

import httpx
import pytest

from prism.connectors.qwen import NumericClaimError, QwenClient, QwenConfig, QwenInvalidOutput, QwenUnavailable, unsupported_numbers

CONFIG = QwenConfig(api_key="test", base_url="https://qwen.test/v1", model="qwen-test")


def client_returning(*texts: str) -> tuple[QwenClient, list[dict]]:
    seen: list[dict] = []
    replies = iter(texts)

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"model": "qwen-test", "output_text": next(replies)})

    return QwenClient(CONFIG, transport=httpx.MockTransport(handler)), seen


TRADE = {"instrument": "BTCUSDT", "category": "USDT-FUTURES", "direction": "long", "notional_usd": 10000, "quantity": None,
         "leverage": None, "timing": "tonight", "confidence": "high", "missing_fields": ["leverage", "quantity"]}


def test_parse_trade_accepts_schema_valid_json():
    client, _ = client_returning("Sure: " + json.dumps(TRADE))
    trade, call = client.parse_trade("Can I add another $10k BTC long tonight?")
    assert trade.notional_usd == 10000 and trade.leverage is None and "leverage" in trade.missing_fields
    assert call.prompt_version == "prism-trade-parser-v1" and call.input_hash.startswith("sha256:")


def test_parse_trade_retries_once_then_fails():
    client, seen = client_returning("not json", "still not json")
    with pytest.raises(QwenInvalidOutput):
        client.parse_trade("Can I add another $10k BTC long?")
    assert len(seen) == 2


def test_parse_trade_rejects_invented_leverage():
    client, _ = client_returning(json.dumps({**TRADE, "leverage": 20}))
    with pytest.raises(QwenInvalidOutput, match="leverage"):
        client.parse_trade("Can I add another $10k BTC long?")


def test_stated_leverage_is_allowed():
    client, _ = client_returning(json.dumps({**TRADE, "leverage": 5}))
    trade, _ = client.parse_trade("Can I add another $10,000 BTC long at 5x?")
    assert trade.leverage == 5


def test_explain_rejects_numbers_not_in_tool_output():
    client, _ = client_returning("Your shadow equity falls to $27,585.74 and the ratio is 61%.")
    with pytest.raises(NumericClaimError) as err:
        client.explain("What happens?", {"shadow_effective_equity": "27585.74", "shadow_core_ratio": "0.044"})
    assert err.value.unsupported == ["61"]


def test_explain_accepts_formatted_tool_numbers():
    client, _ = client_returning("Shadow equity would be $27,585.74, a core ratio of 4.4%.")
    text, _ = client.explain("What happens?", {"shadow_effective_equity": "27585.74", "shadow_core_ratio": "0.044"})
    assert "27,585.74" in text


def test_missing_key_is_unavailable_not_silent():
    with pytest.raises(QwenUnavailable):
        QwenClient(QwenConfig(None, "https://x", "m")).parse_trade("buy")


def test_unsupported_numbers_handles_k_suffix_and_percent():
    assert unsupported_numbers("add $10k, ratio 80%", "notional 10000 boundary 0.8") == []
