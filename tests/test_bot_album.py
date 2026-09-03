import asyncio

import app.bot as bot


class _Msg:
    def __init__(self, message_id, media_group_id=None, caption=None):
        self.id = message_id
        self.media_group_id = media_group_id
        self.caption = caption


class _Ctx:
    def __init__(self):
        self.user_data = {"record_type": bot.RecordType.TIME_IN}


def _update(message):
    update = type("U", (), {})()
    update.message = message
    return update


def _drive(monkeypatch):
    """Replace _process_submission with a recorder and speed up the debounce."""
    calls = []

    async def fake_submission(messages, record_type, update, context):
        calls.append([message.id for message in messages])

    monkeypatch.setattr(bot, "_process_submission", fake_submission)
    monkeypatch.setattr(bot, "_ALBUM_SETTLE_SECONDS", 0.02)
    bot._pending_albums.clear()
    return calls


def test_album_photos_are_processed_once_as_one_submission(monkeypatch):
    calls = _drive(monkeypatch)

    async def run():
        for message_id in (1, 2, 3):
            await bot.receive_image(_update(_Msg(message_id, media_group_id="G1")), _Ctx())
        await asyncio.sleep(0.1)

    asyncio.run(run())
    assert calls == [[1, 2, 3]]


def test_single_photo_is_processed_immediately(monkeypatch):
    calls = _drive(monkeypatch)
    asyncio.run(bot.receive_image(_update(_Msg(7)), _Ctx()))
    assert calls == [[7]]
