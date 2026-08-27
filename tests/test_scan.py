from datetime import datetime
from pathlib import Path

from app.models import RecordType
from app.scan import image_files, scan_images


def test_scan_images_reports_success_and_failure(tmp_path: Path):
    first = tmp_path / "first.jpg"
    second = tmp_path / "second.jpg"
    first.write_bytes(b"x")
    second.write_bytes(b"x")

    def fake_reader(path: Path, tesseract_cmd: str | None):
        if path.name == "first.jpg":
            return datetime(2026, 8, 26, 8, 15, 32)
        raise ValueError("bad watermark")

    results = scan_images([first, second], RecordType.TIME_IN, reader=fake_reader)
    assert len(results) == 2
    assert results[0].ok is True
    assert results[0].timestamp == datetime(2026, 8, 26, 8, 15, 32)
    assert results[1].ok is False
    assert "bad watermark" in results[1].error


def test_image_files_filters_extensions(tmp_path: Path):
    (tmp_path / "a.jpg").write_bytes(b"x")
    (tmp_path / "b.jpeg").write_bytes(b"x")
    (tmp_path / "c.png").write_bytes(b"x")
    (tmp_path / "d.txt").write_text("x", encoding="utf-8")
    files = image_files(tmp_path)
    assert [path.name for path in files] == ["a.jpg", "b.jpeg", "c.png"]
