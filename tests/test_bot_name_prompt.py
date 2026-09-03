import asyncio
from pathlib import Path

import app.bot as bot


class _Msg:
    def __init__(self, message_id, caption=None, text=None):
        self.id = message_id
        self.media_group_id = None
        self.caption = caption
        self.text = text
        self.replies = []

    async def reply_text(self, text):
        self.replies.append(text)


class _Ctx:
    def __init__(self):
        self.user_data = {"record_type": bot.RecordType.TIME_IN}
        settings = type("S", (), {"employee_roster_file": Path("employees.txt")})()
        self.application = type("A", (), {"bot_data": {"service": None, "settings": settings}})()


def _update(message):
    update = type("U", (), {})()
    update.message = message
    update.effective_user = type("EU", (), {"full_name": "Sender", "id": 42})()
    return update


def _capture_resume(monkeypatch):
    captured = {}

    async def fake_submission(messages, record_type, update, context, employees=None):
        captured["employees"] = employees
        captured["ids"] = [m.id for m in messages]

    monkeypatch.setattr(bot, "load_roster", lambda path: ())
    monkeypatch.setattr(bot, "correct_employee_name", lambda name, roster: name)
    monkeypatch.setattr(bot, "_process_submission", fake_submission)
    return captured


def _pending(ctx, *message_ids):
    ctx.user_data["pending_name"] = {
        "messages": [_Msg(mid) for mid in message_ids], "record_type": bot.RecordType.TIME_IN,
    }


def test_no_caption_name_asks_the_bilingual_question(monkeypatch):
    monkeypatch.setattr(bot, "load_roster", lambda path: ())
    ctx = _Ctx()
    photo = _Msg(5, caption="September 2, 2026 2:23 PM")  # timestamp, no name
    asyncio.run(bot._process_submission([photo], bot.RecordType.TIME_IN, _update(photo), ctx))
    assert photo.replies == ["What is the name of the person in the image? / Sino po ang nasa larawan?"]
    assert ctx.user_data["pending_name"]["messages"] == [photo]


def test_labeled_vertical_reply_becomes_one_record_per_name(monkeypatch):
    captured = _capture_resume(monkeypatch)
    ctx = _Ctx()
    _pending(ctx, 5)
    asyncio.run(bot.receive_name(_update(_Msg(6, text="Name: Juan Dela Cruz\nName: Pedro Reyes")), ctx))
    assert captured == {"employees": ["Juan Dela Cruz", "Pedro Reyes"], "ids": [5]}
    assert "pending_name" not in ctx.user_data


def test_bare_vertical_list_reply_becomes_one_record_per_name(monkeypatch):
    captured = _capture_resume(monkeypatch)
    ctx = _Ctx()
    _pending(ctx, 5)
    asyncio.run(bot.receive_name(_update(_Msg(6, text="Juan Dela Cruz\nPedro Reyes")), ctx))
    assert captured["employees"] == ["Juan Dela Cruz", "Pedro Reyes"]


def test_single_first_last_reply_is_one_record(monkeypatch):
    captured = _capture_resume(monkeypatch)
    ctx = _Ctx()
    _pending(ctx, 5)
    asyncio.run(bot.receive_name(_update(_Msg(6, text="  Juan Dela Cruz  ")), ctx))
    assert captured["employees"] == ["Juan Dela Cruz"]


def test_unparseable_multiline_reply_still_logs_one_record_per_line(monkeypatch):
    captured = _capture_resume(monkeypatch)
    ctx = _Ctx()
    _pending(ctx, 5)
    # Both lines carry digits, so the name parser rejects them -- the per-line
    # fallback must still keep each rather than joining them into one name.
    asyncio.run(bot.receive_name(_update(_Msg(6, text="Team 1 lead\nTeam 2 lead")), ctx))
    assert captured["employees"] == ["Team 1 lead", "Team 2 lead"]


def test_stray_text_without_pending_is_ignored():
    ctx = _Ctx()
    asyncio.run(bot.receive_name(_update(_Msg(9, text="hello")), ctx))  # must not raise
