from datetime import datetime

import app.fast_ocr as fast_ocr
from app.ocr import WatermarkError


def test_extract_lines_parses_paddle_2x_shape():
    result = [[
        [[[0, 0], [1, 0], [1, 1], [0, 1]], ("SEP 04, 2026", 0.99)],
        [[[0, 2], [1, 2], [1, 3], [0, 3]], ("14:04 PM", 0.97)],
        [[[0, 4], [1, 4], [1, 5], [0, 5]], ("blurry junk", 0.20)],  # below _MIN_CONFIDENCE
    ]]
    assert fast_ocr._extract_lines(result) == ["SEP 04, 2026", "14:04 PM"]


def test_paddle_hit_is_used_without_touching_tesseract(monkeypatch):
    monkeypatch.setattr(fast_ocr, "_paddle_text", lambda path: "SEP 04, 2026\n14:04 PM")

    def tesseract_reader(path, caption=None):
        raise AssertionError("Tesseract must not be called when Paddle reads a valid timestamp")

    reader = fast_ocr.paddle_first_reader(tesseract_reader)
    assert reader("photo.jpg") == datetime(2026, 9, 4, 14, 4, 0)


def test_falls_back_to_tesseract_when_paddle_misses(monkeypatch):
    def paddle_miss(path):
        raise WatermarkError("PaddleOCR read no text.")

    monkeypatch.setattr(fast_ocr, "_paddle_text", paddle_miss)
    calls = []

    def tesseract_reader(path, caption=None):
        calls.append((path, caption))
        return datetime(2026, 7, 3, 16, 44, 0)

    reader = fast_ocr.paddle_first_reader(tesseract_reader)
    assert reader("photo.jpg", "some caption") == datetime(2026, 7, 3, 16, 44, 0)
    assert calls == [("photo.jpg", "some caption")]


def test_falls_back_when_paddle_crashes_unexpectedly(monkeypatch):
    def paddle_boom(path):
        raise RuntimeError("paddle segfault-ish")

    monkeypatch.setattr(fast_ocr, "_paddle_text", paddle_boom)
    reader = fast_ocr.paddle_first_reader(lambda path, caption=None: datetime(2026, 1, 2))
    assert reader("photo.jpg") == datetime(2026, 1, 2)


def test_missing_paddle_package_degrades_to_fallback(monkeypatch):
    fast_ocr._engine.cache_clear()
    monkeypatch.setitem(__import__("sys").modules, "paddleocr", None)  # force ImportError
    reader = fast_ocr.paddle_first_reader(lambda path, caption=None: datetime(2026, 1, 1))
    assert reader("photo.jpg") == datetime(2026, 1, 1)
    fast_ocr._engine.cache_clear()
