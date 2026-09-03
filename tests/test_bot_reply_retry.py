import asyncio

import app.bot as bot
from telegram.error import NetworkError, RetryAfter


class _Msg:
    """reply_text fails with the queued errors, then succeeds."""

    def __init__(self, errors):
        self._errors = list(errors)
        self.sent = []

    async def reply_text(self, text):
        if self._errors:
            raise self._errors.pop(0)
        self.sent.append(text)


def _run(coro, monkeypatch):
    slept = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(bot.asyncio, "sleep", fake_sleep)
    asyncio.run(coro)
    return slept


def test_retry_after_waits_the_time_telegram_gave(monkeypatch):
    message = _Msg([RetryAfter(30)])
    slept = _run(bot._reply_with_retry(message, "done", attempts=3), monkeypatch)
    assert message.sent == ["done"]
    assert slept == [31]  # retry_after + 1, not the 3s exponential backoff


def test_gives_up_after_attempts_without_raising(monkeypatch):
    message = _Msg([NetworkError("boom")] * 5)
    slept = _run(bot._reply_with_retry(message, "done", attempts=3), monkeypatch)
    assert message.sent == []          # never delivered
    assert len(slept) == 2             # attempts - 1 backoffs, then log and return


def test_no_sleep_when_first_send_works(monkeypatch):
    message = _Msg([])
    slept = _run(bot._reply_with_retry(message, "done"), monkeypatch)
    assert message.sent == ["done"] and slept == []
