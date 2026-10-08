"""Qwen connector (AGENTS.md §27, §35, §41–§42).

Qwen parses and explains; it never computes. Two guarded entry points:

- `parse_trade(text)`: natural language → `ProposedTrade` (schema-validated, one retry on invalid JSON).
  Leverage is never invented: if the user did not state it, it stays null and is listed as missing.
- `explain(question, tool_output)`: plain-language explanation of deterministic results. Every number
  in the answer must appear in `tool_output`; otherwise the answer is rejected (`NumericClaimError`).

Every call records model, prompt version, input hash and timestamp for the Evidence Lock (§13).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field, ValidationError

from ...config import REPO_ROOT, load_env_file
from ...provenance import payload_hash, utc_now

PARSER_PROMPT_VERSION = "prism-trade-parser-v1"
EXPLAIN_PROMPT_VERSION = "prism-explainer-v1"


class QwenUnavailable(Exception):
    pass


class QwenInvalidOutput(Exception):
    pass


class NumericClaimError(Exception):
    def __init__(self, unsupported: list[str]):
        super().__init__(f"answer contains numbers not present in tool output: {unsupported}")
        self.unsupported = unsupported


class ProposedTrade(BaseModel):
    """§35 schema. Numbers here are the user's own stated quantities, extracted, not calculated."""

    instrument: str | None = Field(description="Bitget symbol such as BTCUSDT")
    category: Literal["USDT-FUTURES", "SPOT"] | None
    direction: Literal["long", "short"] | None
    notional_usd: float | None = Field(ge=0)
    quantity: float | None = Field(ge=0)
    leverage: float | None = Field(ge=1)
    timing: str | None
    confidence: Literal["high", "medium", "low"]
    missing_fields: list[str]


@dataclass(frozen=True)
class QwenCall:
    model: str
    prompt_version: str
    input_hash: str
    called_at: str
    raw_text: str


@dataclass(frozen=True)
class QwenConfig:
    api_key: str | None
    base_url: str
    model: str

    @classmethod
    def load(cls) -> "QwenConfig":
        env = load_env_file(REPO_ROOT / ".env.local")
        import os

        def get(name: str, default: str | None = None) -> str | None:
            return (os.environ.get(name) or env.get(name) or default or "").strip() or None

        return cls(get("BITGET_QWEN_API_KEY"), (get("BITGET_QWEN_BASE_URL", "https://hackathon.bitgetops.com/v1") or "").rstrip("/"),
                   get("BITGET_QWEN_MODEL", "qwen3.8-max") or "qwen3.8-max")


PARSER_INSTRUCTIONS = """You convert a trader's sentence into JSON for a read-only risk tool. Output ONLY a JSON object with keys:
instrument (Bitget symbol like BTCUSDT or null), category ("USDT-FUTURES" for perps/longs/shorts on crypto, "SPOT" for rTokens like RNVDAUSDT, or null),
direction ("long"/"short"/null), notional_usd (number the user stated in USD, or null), quantity (base units the user stated, or null),
leverage (only if the user stated it, else null), timing (short string or null), confidence ("high"/"medium"/"low"),
missing_fields (list of keys you could not fill from the sentence).
Never invent a number the user did not say. Never compute anything."""

CLASSIFY_PROMPT_VERSION = "prism-event-classifier-v1"
CLASSIFY_INSTRUCTIONS = """You classify news headlines about one U.S. stock for a read-only risk tool. Output ONLY a JSON object
{"items": [...]} with one entry per headline id you were given, each with keys: id, relevant (true only if the headline is
mainly about this company), event_type ("company_specific", "sector", "macro" or "other"), direction ("positive", "negative",
"neutral" or "unclear" for the stock), materiality ("high", "medium" or "low"), summary (at most 20 words, no numbers that
are not in the headline). Never predict a price, a percentage move or a probability."""


class EventClass(BaseModel):
    id: str
    relevant: bool
    event_type: Literal["company_specific", "sector", "macro", "other"]
    direction: Literal["positive", "negative", "neutral", "unclear"]
    materiality: Literal["high", "medium", "low"]
    summary: str = Field(max_length=240)


EXPLAIN_INSTRUCTIONS = """You explain results from PRISM, a read-only risk tool, to a trader.
Use ONLY numbers that appear verbatim in TOOL_OUTPUT. Do not compute, round differently, or add any new number, percentage or price.
If something the trader asks is not in TOOL_OUTPUT, say it is not available. Keep it under 120 words. PRISM never places trades."""


class QwenClient:
    def __init__(self, config: QwenConfig | None = None, transport: httpx.BaseTransport | None = None, timeout: float = 60.0):
        self.config = config or QwenConfig.load()
        self._http = httpx.Client(timeout=timeout, transport=transport)

    def _respond(self, instructions: str, user_input: str, prompt_version: str) -> QwenCall:
        if not self.config.api_key:
            raise QwenUnavailable("BITGET_QWEN_API_KEY is not configured")
        body = {"model": self.config.model, "instructions": instructions, "input": user_input}
        try:
            response = self._http.post(f"{self.config.base_url}/responses", json=body,
                                       headers={"authorization": f"Bearer {self.config.api_key}"})
        except httpx.HTTPError as exc:
            raise QwenUnavailable(f"Qwen request failed: {type(exc).__name__}") from exc
        if response.status_code != 200:
            raise QwenUnavailable(f"Qwen returned HTTP {response.status_code}")
        data = response.json()
        text = data.get("output_text") or "".join(
            c.get("text", "") for item in data.get("output", []) for c in (item.get("content") or []) if c.get("type") == "output_text"
        )
        return QwenCall(data.get("model") or self.config.model, prompt_version,
                        payload_hash({"instructions": instructions, "input": user_input}), utc_now().isoformat(), text.strip())

    def parse_trade(self, text: str) -> tuple[ProposedTrade, QwenCall]:
        last_error = ""
        for attempt in range(2):
            prompt = text if attempt == 0 else f"{text}\n\nYour previous output was invalid ({last_error}). Return only the JSON object."
            call = self._respond(PARSER_INSTRUCTIONS, prompt, PARSER_PROMPT_VERSION)
            try:
                trade = ProposedTrade.model_validate(_extract_json(call.raw_text))
            except (ValueError, ValidationError) as exc:
                last_error = str(exc)[:200]
                continue
            _check_parsed_numbers(trade, text)
            return trade, call
        raise QwenInvalidOutput(f"Qwen did not return a valid trade after a retry: {last_error}")

    def classify_headlines(self, ticker: str, items: list) -> dict[str, dict]:
        """§27 classification of headlines; ids must match, summaries may not introduce numbers."""
        listing = "\n".join(f"{h.id}: {h.title} ({h.publisher})" for h in items)
        source_text = " ".join(h.title for h in items)
        last_error = ""
        for attempt in range(2):
            prompt = f"STOCK: {ticker}\nHEADLINES:\n{listing}" + (f"\n\nPrevious output invalid ({last_error}). Return only the JSON." if attempt else "")
            call = self._respond(CLASSIFY_INSTRUCTIONS, prompt, CLASSIFY_PROMPT_VERSION)
            try:
                raw = _extract_json(call.raw_text).get("items", [])
                parsed = [EventClass.model_validate(x) for x in raw]
            except (ValueError, ValidationError) as exc:
                last_error = str(exc)[:200]
                continue
            known = {h.id for h in items}
            out = {}
            for e in parsed:
                if e.id not in known:
                    continue
                if unsupported_numbers(e.summary, source_text):
                    e = e.model_copy(update={"summary": "(summary withheld: contained a number not in the headline)"})
                out[e.id] = e.model_dump() | {"model": call.model, "prompt_version": call.prompt_version, "input_hash": call.input_hash}
            return out
        raise QwenInvalidOutput(f"Qwen did not return valid classifications: {last_error}")

    def explain(self, question: str, tool_output: dict[str, Any]) -> tuple[str, QwenCall]:
        payload = json.dumps(tool_output, ensure_ascii=False, default=str)
        call = self._respond(EXPLAIN_INSTRUCTIONS, f"QUESTION: {question}\n\nTOOL_OUTPUT: {payload}", EXPLAIN_PROMPT_VERSION)
        unsupported = unsupported_numbers(call.raw_text, payload + " " + question)
        if unsupported:
            raise NumericClaimError(unsupported)
        return call.raw_text, call


def _extract_json(text: str) -> dict[str, Any]:
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in output")
    value = json.loads(text[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("JSON is not an object")
    return value


_NUMBER = re.compile(r"(?<![A-Za-z])[-+]?\$?\d[\d,]*(?:\.\d+)?\s*(?:[kKmM](?![A-Za-z]))?")


def _to_decimal(token: str) -> Decimal | None:
    t = token.strip().replace("$", "").replace(",", "").replace(" ", "")
    mult = Decimal(1)
    if t[-1:] in "kK":
        mult, t = Decimal(1000), t[:-1]
    elif t[-1:] in "mM":
        mult, t = Decimal(1_000_000), t[:-1]
    try:
        return Decimal(t) * mult
    except InvalidOperation:
        return None


def numbers_in(text: str) -> set[Decimal]:
    return {d.copy_abs().normalize() for d in (_to_decimal(m.group()) for m in _NUMBER.finditer(text)) if d is not None}


_NUMBER_WORDS = re.compile(
    r"\b(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|fifteen|twenty|thirty|forty|fifty|"
    r"sixty|seventy|eighty|ninety|hundred|thousand|million|billion|half|quarter|double|triple)\b"
    r"(?=[\s-]*(?:percent|per cent|times|x\b|bps|basis|dollars|usd|usdt|btc|%|hundred|thousand|million|billion))",
    re.IGNORECASE,
)


def unsupported_numbers(answer: str, source: str) -> list[str]:
    """Numbers in `answer` absent from `source` (compared by value, sign-insensitive; percent/fraction forms allowed).

    Spelled-out quantities ("five percent", "two million dollars") are always rejected: the explainer
    must quote PRISM's figures verbatim, never paraphrase them into words."""
    allowed = numbers_in(source)
    allowed |= {(a * 100).normalize() for a in allowed} | {(a / 100).normalize() for a in allowed}
    out = [m.group() for m in _NUMBER_WORDS.finditer(answer)]
    for m in _NUMBER.finditer(answer):
        value = _to_decimal(m.group())
        if value is not None and value.copy_abs().normalize() not in allowed:
            out.append(m.group().strip())
    return out


def _check_parsed_numbers(trade: ProposedTrade, text: str) -> None:
    """The parser may only echo numbers the user typed (e.g. "$10k" -> 10000)."""
    stated = numbers_in(text)
    for name in ("notional_usd", "quantity", "leverage"):
        value = getattr(trade, name)
        if value is not None and Decimal(str(value)).normalize() not in stated:
            raise QwenInvalidOutput(f"{name}={value} was not stated by the user")
