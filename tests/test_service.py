from datetime import datetime
from pathlib import Path

import pytest

from app.models import RecordType
from app.service import AttendanceService


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
        lambda path: datetime(2026, 8, 26, 8, 15, 32), storage, repository
    )
    record = service.process(tmp_path / "image.jpg", RecordType.TIME_IN, "Employee01", "123", "456")
    assert record.image_url == "https://drive.example/image"
    assert len(storage.uploads) == 1
    assert repository.records == [record]


def test_does_not_upload_duplicate(tmp_path: Path):
    repository = FakeRepository()
    repository.existing = lambda *values: True
    storage = FakeStorage()
    service = AttendanceService(lambda path: datetime(2026, 8, 26, 8, 15, 32), storage, repository)
    with pytest.raises(ValueError, match="already exists"):
        service.process(tmp_path / "image.jpg", RecordType.TIME_IN, "Employee01", "123", "456")
    assert storage.uploads == []
