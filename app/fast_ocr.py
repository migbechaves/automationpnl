"""Optional PaddleOCR front-end for watermark reading.

Add-on, not a rewrite: PaddleOCR's detector+recogniser usually reads a
phone-camera timestamp in one shot, where the Tesseract pipeline
(app/watermark_ocr.py) can grind through dozens of preprocessing passes and hit
its 40s budget on a hard image -- the "stuck on analyzing" case. So when
FAST_OCR=true we try Paddle first and fall back to the *unchanged* Tesseract
pipeline (which keeps its own caption fallback) on any miss.

Paddle is a heavy, optional dependency (see README "Faster OCR"). If it isn't
installed or fails to start, this degrades to "always fall back" and logs once --
the bot keeps working exactly as before.

ponytail: parses paddleocr 2.7-2.8's `ocr()` result shape. If you install 3.x,
only `_extract_lines` needs adjusting (rec_texts/rec_scores dict).
"""
import logging
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from .ocr import WatermarkError, extract_timestamp

logger = logging.getLogger(__name__)

_MIN_CONFIDENCE = 0.5


@lru_cache(maxsize=1)
def _engine():
    """One shared PaddleOCR instance -- model load is slow, inference is fine to
    reuse across threads. Returns None (not raise) when Paddle can't be used, so
    every caller simply falls back to Tesseract.
    """
    try:
        from paddleocr import PaddleOCR
    except ImportError:
        logger.warning("FAST_OCR is on but 'paddleocr' is not installed -- using Tesseract only.")
        return None
    try:
        return PaddleOCR(use_angle_cls=True, lang="en", show_log=False)
    except Exception:
        logger.exception("PaddleOCR failed to start -- using Tesseract only.")
        return None


def _extract_lines(paddle_result) -> list[str]:
    """paddleocr 2.7-2.8: ``ocr.ocr(path, cls=True)`` -> ``[[ [box, (text, conf)], ... ]]``
    (one inner list per image; we pass one image)."""
    lines: list[str] = []
    for page in paddle_result or []:
        for entry in page or []:
            payload = entry[1] if entry and len(entry) > 1 else None
            if payload and payload[1] >= _MIN_CONFIDENCE:
                lines.append(payload[0])
    return lines


def _paddle_text(image_path: Path) -> str:
    engine = _engine()
    if engine is None:
        raise WatermarkError("PaddleOCR unavailable.")
    lines = _extract_lines(engine.ocr(str(image_path), cls=True))
    if not lines:
        raise WatermarkError("PaddleOCR read no text.")
    return "\n".join(lines)


def paddle_first_reader(tesseract_reader):
    """Wrap an existing ``(path, caption) -> datetime`` reader so PaddleOCR gets
    first crack and the Tesseract pipeline is the fallback. The Tesseract path is
    called exactly as before -- unchanged, caption fallback and all.
    """
    def read(image_path, caption: str | None = None) -> datetime:
        try:
            return extract_timestamp(_paddle_text(Path(image_path)))
        except (WatermarkError, ValueError):
            pass  # Paddle read something, it just wasn't a timestamp -- let Tesseract try
        except Exception:
            # A bug or crash inside Paddle must never be worse than not having it.
            logger.exception("PaddleOCR raised unexpectedly -- falling back to Tesseract.")
        return tesseract_reader(image_path, caption)

    return read
