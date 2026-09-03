import pytest

from app import net
from app.net import retry_network


@pytest.fixture(autouse=True)
def _no_backoff_sleep(monkeypatch):
    monkeypatch.setattr(net.time, "sleep", lambda *_: None)


class _ApiError(Exception):
    """Shaped like gspread.exceptions.APIError (has .code)."""

    def __init__(self, code):
        super().__init__(f"[{code}]")
        self.code = code


class _HttpError(Exception):
    """Shaped like googleapiclient.errors.HttpError (has .resp.status)."""

    def __init__(self, status):
        super().__init__(str(status))
        self.resp = type("R", (), {"status": status})()


def _flaky(errors):
    calls = {"n": 0}

    def call():
        if calls["n"] < len(errors):
            calls["n"] += 1
            raise errors[calls["n"] - 1]
        return "ok"

    return call, calls


def test_retries_transient_gspread_503_then_succeeds():
    call, calls = _flaky([_ApiError(503), _ApiError(503)])
    assert retry_network(call, description="sheets") == "ok"
    assert calls["n"] == 2


def test_retries_transient_googleapiclient_500():
    call, calls = _flaky([_HttpError(500)])
    assert retry_network(call, description="drive") == "ok"
    assert calls["n"] == 1


def test_exhausted_transient_http_is_normalised_to_oserror():
    call, _ = _flaky([_ApiError(503)] * 9)
    with pytest.raises(OSError):  # so service.py's `except OSError` path catches it
        retry_network(call, attempts=3, description="sheets")


def test_non_transient_status_is_not_retried():
    call, calls = _flaky([_ApiError(404)] * 5)
    with pytest.raises(_ApiError):  # unchanged type, fails fast
        retry_network(call, attempts=3, description="sheets")
    assert calls["n"] == 1


def test_plain_bug_is_not_retried_or_wrapped():
    call, calls = _flaky([KeyError("boom")] * 5)
    with pytest.raises(KeyError):
        retry_network(call, attempts=3, description="sheets")
    assert calls["n"] == 1
