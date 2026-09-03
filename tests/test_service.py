from datetime import datetime
from pathlib import Path

import pytest

from app import net
from app.models import RecordType
from app.service import AttendanceService


@pytest.fixture(autouse=True)
def _no_backoff_sleep(monkeypatch):
    monkeypatch.setattr(net.time, "sleep", lambda *_: None)


class FakeRepository:
    def __init__(self):
        self.records = []

    def existing(self, *values):
        return False

    def next_id(self):
        return "0001"

    def add(self, record):
        self.records.append(record)


class FakeStorage:
    def __init__(self):
        self.uploads = []

    def upload(self, *values):
        self.uploads.append(values)
        return "https://drive.example/image"


def test_uploads_before_writing_sheet_row(tmp_path: Path):
    repository = FakeRepository()
    storage = FakeStorage()
    service = AttendanceService(
        lambda path, caption=None: datetime(2026, 8, 26, 8, 15, 32), storage, repository
    )
    records, skipped = service.process(tmp_path / "image.jpg", RecordType.TIME_IN, "Employee01", "123", "456")
    assert [record.image_url for record in records] == ["https://drive.example/image"]
    assert skipped == []
    assert len(storage.uploads) == 1
    assert repository.records == records


def test_album_uploads_every_photo_once_into_one_folder(tmp_path: Path):
    repository = FakeRepository()
    storage = FakeStorage()
    service = AttendanceService(
        lambda path, caption=None: datetime(2026, 8, 26, 8, 15, 32), storage, repository
    )
    records, skipped = service.process(
        [tmp_path / "a.jpg", tmp_path / "b.jpg"], RecordType.TIME_OUT,
        ["Employee01", "Employee02"], "123", "456",
    )
    assert len(storage.uploads) == 1
    assert storage.uploads[0][0] == [tmp_path / "a.jpg", tmp_path / "b.jpg"]
    assert [record.employee for record in records] == ["Employee01", "Employee02"]
    assert {record.image_url for record in records} == {"https://drive.example/image"}
    assert skipped == []


def test_process_reports_already_recorded_employees_as_skipped(tmp_path: Path):
    repository = FakeRepository()
    repository.existing = lambda date, time, record_type, employee: employee == "Employee02"
    storage = FakeStorage()
    service = AttendanceService(lambda path, caption=None: datetime(2026, 8, 26, 8, 15, 32), storage, repository)
    records, skipped = service.process(
        tmp_path / "image.jpg", RecordType.TIME_IN, ["Employee01", "Employee02"], "123", "456",
    )
    assert [record.employee for record in records] == ["Employee01"]
    assert skipped == ["Employee02"]


def test_does_not_upload_duplicate(tmp_path: Path):
    repository = FakeRepository()
    repository.existing = lambda *values: True
    storage = FakeStorage()
    service = AttendanceService(lambda path, caption=None: datetime(2026, 8, 26, 8, 15, 32), storage, repository)
    with pytest.raises(ValueError, match="already exists"):
        service.process(tmp_path / "image.jpg", RecordType.TIME_IN, "Employee01", "123", "456")
    assert storage.uploads == []


def test_forwards_caption_to_ocr_reader(tmp_path: Path):
    received = {}

    def reader(path, caption=None):
        received["caption"] = caption
        return datetime(2026, 8, 26, 8, 15, 32)

    service = AttendanceService(reader, FakeStorage(), FakeRepository())
    service.process(
        tmp_path / "image.jpg", RecordType.TIME_IN, ["Employee01"], "123", "456",
        caption="Date: August17-2026\nTime in:1119H",
    )
    assert received["caption"] == "Date: August17-2026\nTime in:1119H"


def _service(repository, storage):
    return AttendanceService(
        lambda path, caption=None: datetime(2026, 8, 26, 8, 15, 32), storage, repository
    )


def test_retries_transient_google_error_then_succeeds(tmp_path: Path):
    repository = FakeRepository()
    storage = FakeStorage()
    calls = {"n": 0}
    real_upload = storage.upload

    def flaky_upload(*values):
        calls["n"] += 1
        if calls["n"] == 1:
            raise TimeoutError("[WinError 10060] connection timed out")
        return real_upload(*values)

    storage.upload = flaky_upload
    records, _skipped = _service(repository, storage).process(
        tmp_path / "image.jpg", RecordType.TIME_IN, "Employee01", "123", "456"
    )
    assert calls["n"] == 2
    assert repository.records == records


def test_persistent_network_error_becomes_runtime_error(tmp_path: Path):
    repository = FakeRepository()
    repository.existing = lambda *values: (_ for _ in ()).throw(OSError("network down"))
    with pytest.raises(RuntimeError, match="network"):
        _service(repository, FakeStorage()).process(
            tmp_path / "image.jpg", RecordType.TIME_IN, "Employee01", "123", "456"
        )


def test_lost_append_response_does_not_double_write(tmp_path: Path):
    repository = FakeRepository()
    storage = FakeStorage()
    attempts = {"n": 0}

    def add_then_lose_response(record):
        attempts["n"] += 1
        repository.records.append(record)
        raise OSError("response never arrived")

    repository.add = add_then_lose_response
    # After the first (actually successful) append, existing() reports the row is
    # there, so retry_network must stop rather than append a second copy.
    repository.existing = lambda *values: len(repository.records) > 0

    _service(repository, storage).process(
        tmp_path / "image.jpg", RecordType.TIME_IN, "Employee01", "123", "456"
    )
    assert attempts["n"] == 1
    assert len(repository.records) == 1
