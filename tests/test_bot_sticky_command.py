import asyncio
from pathlib import Path

import app.bot as bot


async def _noop(*args, **kwargs):
    pass


class _Msg:
    def __init__(self, message_id, caption=None):
        self.id = message_id
        self.media_group_id = None
        self.caption = caption

    async def reply_text(self, text):
        pass


class _Ctx:
    def __init__(self):
        self.user_data = {"record_type": bot.RecordType.TIME_IN}
        settings = type("S", (), {"employee_roster_file": Path("employees.txt")})()
        service = type("Svc", (), {"process": staticmethod(lambda *a, **k: ([], []))})()
        self.application = type("A", (), {"bot_data": {"service": service, "settings": settings}})()


def _update(message):
    update = type("U", (), {})()
    update.message = message
    update.effective_user = type("EU", (), {"full_name": "Sender", "id": 42})()
    return update


def test_record_type_stays_active_for_the_next_upload(monkeypatch):
    # The sender should not have to re-type /in between consecutive Time-In
    # photos: record_type must survive a completed submission.
    monkeypatch.setattr(bot, "load_roster", lambda path: ())
    monkeypatch.setattr(bot, "_roster_corrected_names", lambda text, roster: ["Juan Dela Cruz"])
    monkeypatch.setattr(bot, "_download_photo_with_retry", _noop)
    monkeypatch.setattr(bot, "_reply_with_retry", _noop)

    ctx = _Ctx()
    photo = _Msg(1)
    asyncio.run(bot._process_submission([photo], bot.RecordType.TIME_IN, _update(photo), ctx))

    assert ctx.user_data.get("record_type") == bot.RecordType.TIME_IN
