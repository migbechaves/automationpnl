import os
import shutil
from datetime import datetime
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from app.ocr import read_watermark_details
from app.watermark_ocr import OCRConfidence, _confidence_state, _named_regions, _white_text_mask


def _find_tesseract() -> str | None:
    configured = os.getenv("TESSERACT_CMD")
    if configured and Path(configured).exists():
        return configured
    found = shutil.which("tesseract")
    if found:
        return found
    default = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
    return default if Path(default).exists() else None


TESSERACT_CMD = _find_tesseract()


def _load_test_font(size: int):
    from PIL import ImageFont

    for candidate in ("arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return None


def test_confidence_high_when_three_or_more_agree():
    candidates = [datetime(2026, 8, 28, 10, 47)] * 3
    confidence, agreement = _confidence_state(candidates)
    assert confidence == OCRConfidence.HIGH
    assert agreement == 3


def test_confidence_medium_when_fewer_than_three_agree():
    candidates = [datetime(2026, 8, 28, 10, 47), datetime(2026, 8, 28, 10, 47)]
    confidence, agreement = _confidence_state(candidates)
    assert confidence == OCRConfidence.MEDIUM
    assert agreement == 2


def test_confidence_medium_on_a_single_lonely_reading():
    # A single successful read among many failed passes should still be usable
    # (MEDIUM), not silently dropped -- OCR_UNCERTAIN is reserved for zero
    # candidates, so a hard image that only one pass manages to read isn't
    # thrown away just because nothing else agreed with it.
    confidence, agreement = _confidence_state([datetime(2026, 8, 28, 10, 47)])
    assert confidence == OCRConfidence.MEDIUM
    assert agreement == 1


def test_confidence_uncertain_when_no_candidates():
    confidence, agreement = _confidence_state([])
    assert confidence == OCRConfidence.UNCERTAIN
    assert agreement == 0


def test_confidence_picks_majority_over_a_minority_disagreement():
    candidates = [
        datetime(2026, 8, 28, 9, 54), datetime(2026, 8, 28, 9, 54),
        datetime(2026, 8, 28, 9, 34), datetime(2026, 8, 28, 9, 54),
    ]
    confidence, agreement = _confidence_state(candidates)
    assert confidence == OCRConfidence.HIGH
    assert agreement == 3


def test_white_text_mask_keeps_white_and_drops_bright_colour():
    # White watermark pixel over a bright-but-coloured scene: the white must
    # survive as black text, the coloured clutter must wash out to white.
    image = Image.new("RGB", (4, 1), (40, 160, 255))  # bright blue background
    image.putpixel((0, 0), (250, 250, 250))           # a watermark pixel
    mask = _white_text_mask(image)
    assert mask.getpixel((0, 0)) == 0     # white text -> black
    assert mask.getpixel((2, 0)) == 255   # bright blue -> white


def test_named_regions_include_all_corners_and_badge_when_present():
    image = Image.new("RGB", (400, 600), (230, 225, 210))
    from PIL import ImageDraw
    ImageDraw.Draw(image).rectangle([0, 500, 200, 600], fill=(20, 40, 120))
    names = [name for name, _region in _named_regions(image)]
    for expected in ("bottom_left", "bottom_right", "top_left", "top_right", "bottom_center", "full", "badge_color"):
        assert expected in names
    # A detected badge is prioritized to the front over the fixed-percentage guesses.
    assert names[0] == "badge_color"


def test_named_regions_without_a_badge_omits_it():
    image = Image.new("RGB", (400, 600), (230, 225, 210))
    names = [name for name, _region in _named_regions(image)]
    assert "badge_color" not in names
    assert names[0] == "bottom_left"


def test_named_regions_honors_preferred_override():
    image = Image.new("RGB", (400, 600), (230, 225, 210))
    names = [name for name, _region in _named_regions(image, preferred="top_right")]
    assert names[0] == "top_right"


@pytest.mark.skipif(TESSERACT_CMD is None, reason="Tesseract is not installed in this environment")
def test_pipeline_reads_clean_badge_watermark_end_to_end(tmp_path: Path):
    # Regression test for a real bug found while building this pipeline:
    # tessedit_char_whitelist silently drops inter-word spaces under the LSTM
    # engine ("10:47 AM Fri" -> "10:47AMFri"), which breaks every
    # word-boundary-anchored part of extract_timestamp()'s time regex even
    # though the raw image is perfectly clean and legible. That whitelist
    # config option must stay removed (see the comment in watermark_ocr.py's
    # run_pass) -- this test fails loudly if it ever comes back.
    font = _load_test_font(40)
    if font is None:
        pytest.skip("no truetype font available to render a legible test image")

    image = Image.new("RGB", (900, 1200), (230, 225, 210))
    draw = ImageDraw.Draw(image)
    draw.rectangle([0, 1050, 700, 1200], fill=(20, 40, 120))
    draw.text((20, 1070), "10:47 AM Fri", fill="white", font=font)
    draw.text((20, 1120), "Aug 28, 2026", fill="white", font=font)
    image_path = tmp_path / "clean_badge.jpg"
    image.save(image_path)

    timestamp, _site, confidence = read_watermark_details(image_path, tesseract_cmd=TESSERACT_CMD)

    assert timestamp == datetime(2026, 8, 28, 10, 47)
    assert confidence == OCRConfidence.HIGH
