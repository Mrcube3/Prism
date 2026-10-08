"""Regression tests for findings from the 2026-10-07 review (docs/CODEX_QUEUE.md)."""

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from prism.api import app as api
from prism.connectors.qwen import unsupported_numbers
from prism.connectors.us_reference import sec
from prism.reality.engine import NY
from prism.reality.history import count_windows


def ny(y, m, d, h, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=NY).astimezone(timezone.utc)


def test_spelled_out_quantities_are_rejected_by_the_explainer_guard():
    assert unsupported_numbers("equity falls about five percent", "equity 1000") == ["five"]
    assert "two" in unsupported_numbers("a loss of two million dollars", "loss 3")
    assert unsupported_numbers("one scenario breaches", "scenario breach") == []  # ordinary prose is fine


def test_weekend_closure_counts_once_and_only_after_reopen():
    weekend = [ny(2026, 10, 9, 21), ny(2026, 10, 10, 12), ny(2026, 10, 11, 12)]
    assert count_windows(weekend) == 0  # reopen (Mon 04:00) not yet observed
    assert count_windows(weekend + [ny(2026, 10, 12, 5)]) == 1  # one weekend window, not three days
    overnight = [ny(2026, 10, 6, 22), ny(2026, 10, 7, 5)]
    assert count_windows(overnight) == 1


def test_sec_window_starts_at_the_regular_close():
    ref = datetime(2026, 10, 10, 0, tzinfo=timezone.utc)  # 20:00 New York (EDT)
    assert sec.event_window_start(ref) == ref - timedelta(hours=4)


@pytest.mark.parametrize("symbol,ok", [("RNVDAUSDT", True), ("RBRKBUSDT", True), ("R../XUSDT", False), ("RNVDA?USDT", False), ("BTCUSDT", False)])
def test_rtoken_symbol_validation(symbol, ok):
    assert bool(api.RTOKEN_SYMBOL.fullmatch(symbol)) is ok


def test_pretrade_text_is_bounded():
    with pytest.raises(ValidationError):
        api.PreTradeRequest(text="x" * 501)
    with pytest.raises(ValidationError):
        api.PreTradeRequest(text="")


def test_response_cache_is_bounded(monkeypatch):
    monkeypatch.setattr(api, "_cache", {})
    for i in range(api.CACHE_MAX_ENTRIES + 50):
        api.cached(f"k{i}", lambda i=i: i)
    assert len(api._cache) <= api.CACHE_MAX_ENTRIES


# ---- hedge-mode pre-trade merge ---------------------------------------------------------------

from dataclasses import replace as _replace  # noqa: E402
from decimal import Decimal as D  # noqa: E402

from prism import pretrade  # noqa: E402
from prism.connectors.qwen import ProposedTrade  # noqa: E402
from prism.shadow import Baseline, Factor, PerpPosition  # noqa: E402
from prism.workbench import Workbench  # noqa: E402


class _Snap:
    def __init__(self, mode):
        self.hold_mode, self.account_environment = mode, "DEMO_PAPTRADING"


def _wb(mode):
    short = PerpPosition("BTCUSDT", -1, D(1), D(100), Factor.CRYPTO, D("0.01"), "t")
    return Workbench(Baseline(D(1000), D(1), "B", "OBSERVED_BASELINE"), [], [short], {}, None, _Snap(mode), [])


@pytest.mark.parametrize("mode,expected", [("hedge_mode", {(-1, D(1)), (1, D(2))}), ("one_way_mode", {(1, D(1))})])
def test_long_does_not_net_against_short_in_hedge_mode(monkeypatch, mode, expected):
    monkeypatch.setattr(pretrade, "fetch_price", lambda *a, **k: (D(100), "t"))
    monkeypatch.setattr(pretrade, "fetch_book", lambda *a, **k: ([[D(100), D(50)]], [[D(100), D(50)]], "t"))
    monkeypatch.setattr(pretrade, "fetch_mmr_rate", lambda *a, **k: (D("0.01"), "t"))
    monkeypatch.setattr(pretrade, "perp_factor", lambda *a, **k: Factor.CRYPTO)
    monkeypatch.setattr(pretrade, "with_perp_context", lambda client, p, demo=False: p)
    trade = ProposedTrade(instrument="BTCUSDT", category="USDT-FUTURES", direction="long", notional_usd=200, quantity=None,
                          leverage=None, timing=None, confidence="high", missing_fields=[])
    wb, *_ = pretrade.apply_trade(None, _wb(mode), trade)
    assert {(p.direction, p.size) for p in wb.positions} == expected
