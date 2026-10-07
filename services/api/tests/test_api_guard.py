import base64

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from prism.api import app as api


def make_request(headers: dict[str, str] | None = None, client: str = "127.0.0.1") -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return Request({"type": "http", "method": "GET", "path": "/", "headers": raw, "client": (client, 1234), "query_string": b""})


def test_local_requests_are_not_restricted(monkeypatch):
    monkeypatch.setattr(api, "_ACCOUNT_PASSWORD", "")
    api.guard_account(make_request(), "bitget")  # no exception


def test_public_account_view_is_open_without_a_configured_password(monkeypatch):
    monkeypatch.setattr(api, "_ACCOUNT_PASSWORD", "")
    api.guard_account(make_request({"X-Forwarded-For": "1.2.3.4"}), "bitget")  # no exception


def test_public_account_view_requires_the_password(monkeypatch):
    monkeypatch.setattr(api, "_ACCOUNT_PASSWORD", "s3cret")
    with pytest.raises(HTTPException) as err:
        api.guard_account(make_request({"X-Forwarded-For": "1.2.3.4"}), "bitget")
    assert err.value.status_code == 401 and "Basic" in err.value.headers["WWW-Authenticate"]
    good = "Basic " + base64.b64encode(b"anyone:s3cret").decode()
    api.guard_account(make_request({"X-Forwarded-For": "1.2.3.4", "Authorization": good}), "bitget")


def test_judge_mode_is_always_public(monkeypatch):
    monkeypatch.setattr(api, "_ACCOUNT_PASSWORD", "")
    api.guard_account(make_request({"X-Forwarded-For": "1.2.3.4"}), "judge")


def test_public_pretrade_is_rate_limited_per_visitor(monkeypatch):
    monkeypatch.setattr(api, "_hits", {})
    visitor = make_request({"X-Forwarded-For": "9.9.9.9"})
    count, _ = api.LIMITS["pretrade"]
    for _ in range(count):
        api.rate_limit(visitor, "pretrade")
    with pytest.raises(HTTPException) as err:
        api.rate_limit(visitor, "pretrade")
    assert err.value.status_code == 429
    api.rate_limit(make_request({"X-Forwarded-For": "8.8.8.8"}), "pretrade")  # other visitors unaffected
    for _ in range(50):
        api.rate_limit(make_request(), "pretrade")  # local requests are never limited
