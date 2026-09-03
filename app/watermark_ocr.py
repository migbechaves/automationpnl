"""Multi-stage watermark OCR pipeline.

Orchestrates everything downstream of "here's an image" into a staged,
early-exiting sequence of attempts, each scored and voted on, rather than
trusting a single OCR pass. Text parsing and date/time validity (calendar day,
month 1-12, hour 0-23, minute 0-59) live in app/ocr.py and are reused here
rather than duplicated -- `datetime.strptime` already rejects an impossible
date/time by construction, so there's no separate validator to write.

Sections below map onto:
  - watermark_region_detector: _detect_badge_region, _named_regions
  - image_preprocessor:        _fast_variants, _extended_variants
  - ocr_engine:                the pytesseract.image_to_string call in _Pass.run
  - confidence_scorer:         _confidence_state
  - consensus_engine:          Counter-based voting inside _confidence_state
  - ocr_retry_manager:         analyze_watermark's staged loop
"""
import logging
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path

# Hard wall-clock budget for one image. A hard, cluttered photo can otherwise
# push the staged pipeline (every region x every variant x every PSM) past a
# minute of solid Tesseract work while the sender stares at "analyzing...".
# Once this passes, no new passes start; whatever was read so far is used.
# ponytail: fixed 40s cap, make it a setting only if a slow PC needs more room.
_OCR_BUDGET_SECONDS = 40.0

from .ocr import (
    WatermarkError,
    _extract_scan_title,
    _pick_most_likely_title,
    extract_timestamp,
    extract_timestamp_from_caption,
)

logger = logging.getLogger(__name__)


class OCRConfidence(str, Enum):
    HIGH = "HIGH_CONFIDENCE"
    MEDIUM = "MEDIUM_CONFIDENCE"
    UNCERTAIN = "OCR_UNCERTAIN"


@dataclass
class WatermarkResult:
    timestamp: datetime
    site: str
    confidence: OCRConfidence
    agreement: int
    total_attempts: int


# ---------------------------------------------------------------------------
# watermark_region_detector
# ---------------------------------------------------------------------------

def _detect_badge_region(image):
    """Best-effort tight crop around a solid blue/navy overlay badge, found by
    color rather than position. See module docstring; a busy background (e.g.
    computer monitors) sharing the frame with the badge can pull enough
    competing text into a fixed-percentage crop to confuse OCR, so this finds
    the badge by what it looks like instead of guessing where it sits.
    Returns None if no such region is found.
    """
    from PIL import ImageChops

    rgb = image.convert("RGB")
    red, green, blue = rgb.split()
    blue_over_red = ImageChops.subtract(blue, red).point(lambda value: 255 if value > 40 else 0)
    blue_over_green = ImageChops.subtract(blue, green).point(lambda value: 255 if value > 40 else 0)
    mask = ImageChops.multiply(blue_over_red, blue_over_green)
    bbox = mask.getbbox()
    if not bbox:
        return None
    left, upper, right, lower = bbox
    width, height = image.size
    pad_x = int((right - left) * 0.05)
    pad_y = int((lower - upper) * 0.05)
    return image.crop((
        max(0, left - pad_x), max(0, upper - pad_y),
        min(width, right + pad_x), min(height, lower + pad_y),
    ))


def _named_regions(image, preferred: str | None = None) -> list[tuple[str, object]]:
    """All regions of interest, most-likely-first. `preferred` (a name from this
    dict) is bumped to the very front when given -- this is the "configuration
    of preferred watermark location" hook; callers that know their watermark
    app's usual placement can pass it to skip straight to that region.
    """
    width, height = image.size
    named = {
        "bottom_left": image.crop((0, int(height * 0.60), int(width * 0.55), height)),
        "bottom_strip": image.crop((0, int(height * 0.65), width, height)),
        "lower": image.crop((0, int(height * 0.45), width, height)),
        "lower_left": image.crop((0, int(height * 0.40), int(width * 0.72), height)),
        "bottom_right": image.crop((int(width * 0.45), int(height * 0.60), width, height)),
        "bottom_center": image.crop((int(width * 0.20), int(height * 0.60), int(width * 0.80), height)),
        "top_left": image.crop((0, 0, int(width * 0.55), int(height * 0.35))),
        "top_right": image.crop((int(width * 0.45), 0, width, int(height * 0.35))),
        "full": image,
    }
    badge = _detect_badge_region(image)
    if badge is not None:
        named["badge_color"] = badge

    order = list(named.keys())
    front = preferred if preferred in named else ("badge_color" if "badge_color" in named else "bottom_left")
    order.remove(front)
    order.insert(0, front)
    return [(name, named[name]) for name in order]


# ---------------------------------------------------------------------------
# image_preprocessor
# ---------------------------------------------------------------------------

def _white_text_mask(region):
    """Isolate a plain white-font watermark with no coloured badge behind it
    (e.g. GPS Map Camera's minimal "Feb 11, 2026 9:56:57AM" overlay).

    Per-pixel min of R/G/B: white and light grey survive, but bright *coloured*
    scene areas (sky, foliage, skin, a warm-lit wall or shirt) lose their
    weakest channel and drop out -- so thresholding this keeps the watermark
    and discards clutter that a plain grayscale threshold also picks up.
    Returns black text on white, the orientation Tesseract expects.
    """
    from PIL import ImageChops

    red, green, blue = region.convert("RGB").split()
    min_channel = ImageChops.darker(ImageChops.darker(red, green), blue)
    return min_channel.point(lambda value: 0 if value > 200 else 255)


def _fast_variants(region, Image, ImageEnhance, ImageOps):
    """Cheap, high-yield variants -- tried first on the priority regions."""
    gray = ImageOps.grayscale(region)
    high_contrast = ImageEnhance.Contrast(gray).enhance(2)
    threshold = gray.point(lambda value: 255 if value > 165 else 0)
    return [
        ("high_contrast", high_contrast),
        ("threshold_165", threshold),
        ("white_text", _white_text_mask(region)),
    ]


def _extended_variants(region, Image, ImageEnhance, ImageOps, ImageFilter, ImageStat):
    """Slower, broader-net variants -- only tried once the fast pass hasn't
    reached HIGH_CONFIDENCE. Covers: multiple threshold values, adaptive
    thresholding, inversion, sharpening, noise reduction, a morphological
    dilation pass, and 2x/3x upscaling.
    """
    gray = ImageOps.grayscale(region)
    high_contrast = ImageEnhance.Contrast(gray).enhance(2)
    out = [
        ("threshold_120", gray.point(lambda value: 255 if value > 120 else 0)),
        ("threshold_190", gray.point(lambda value: 255 if value > 190 else 0)),
        ("inverted", ImageOps.invert(gray)),
        ("sharpened", high_contrast.filter(ImageFilter.UnsharpMask(radius=2, percent=150, threshold=3))),
    ]
    try:
        mean_brightness = ImageStat.Stat(gray).mean[0]
        adaptive_cutoff = max(60, min(200, int(mean_brightness * 0.85)))
    except Exception:
        adaptive_cutoff = 145
    out.append(("adaptive_threshold", gray.point(lambda value, c=adaptive_cutoff: 255 if value > c else 0)))

    denoised = gray.filter(ImageFilter.MedianFilter(size=3)).point(lambda value: 255 if value > 165 else 0)
    out.append(("denoised", denoised))
    out.append(("dilated", denoised.filter(ImageFilter.MaxFilter(3))))

    for factor in (2, 3):
        out.append((
            f"upscaled_{factor}x",
            high_contrast.resize((high_contrast.width * factor, high_contrast.height * factor), Image.Resampling.LANCZOS),
        ))
    return out


# ---------------------------------------------------------------------------
# confidence_scorer / consensus_engine
# ---------------------------------------------------------------------------

def _confidence_state(parsed_candidates: list[datetime]) -> tuple[OCRConfidence, int]:
    """Vote across every valid candidate collected so far. Every candidate here
    already passed date_time_validator (extract_timestamp only returns values
    datetime accepted as a real calendar date/time) -- this stage is purely
    about agreement, not validity.
    """
    if not parsed_candidates:
        return OCRConfidence.UNCERTAIN, 0
    _, agreement = Counter(parsed_candidates).most_common(1)[0]
    if agreement >= 3:
        return OCRConfidence.HIGH, agreement
    return OCRConfidence.MEDIUM, agreement


# ---------------------------------------------------------------------------
# ocr_retry_manager
# ---------------------------------------------------------------------------

def analyze_watermark(
    image_path: Path, tesseract_cmd: str | None = None, caption: str | None = None, preferred_region: str | None = None,
) -> WatermarkResult:
    try:
        import pytesseract
        from PIL import Image, ImageEnhance, ImageFilter, ImageOps, ImageStat
    except ImportError as error:
        raise WatermarkError("OCR dependencies are not installed.") from error
    if tesseract_cmd:
        pytesseract.pytesseract.tesseract_cmd = tesseract_cmd

    attempts = 0
    parsed_candidates: list[datetime] = []
    site_candidates: list[str] = []
    deadline = time.monotonic() + _OCR_BUDGET_SECONDS

    def run_pass(region_name: str, variant_name: str, prepared, psm: int) -> None:
        nonlocal attempts
        if time.monotonic() > deadline:
            return  # out of time -- later passes are skipped, best-so-far is used
        attempts += 1
        # No tessedit_char_whitelist here on purpose: verified experimentally that
        # combining it with the LSTM engine (--oem 3) silently drops inter-word
        # spaces ("10:47 AM Fri" -> "10:47AMFri") even though the whitelist string
        # includes a space character. The squished output then fails every
        # word-boundary-anchored regex in extract_timestamp() for the time portion
        # specifically (the date survives because its pattern tolerates zero-width
        # gaps) -- this was very likely the single largest cause of "can't read the
        # watermark" failures, independent of any region/preprocessing choice.
        config = f"--oem 3 --psm {psm} -c preserve_interword_spaces=1"
        text = pytesseract.image_to_string(prepared, config=config)
        site_candidates.append(_extract_scan_title(text))
        try:
            timestamp = extract_timestamp(text)
        except WatermarkError:
            logger.debug("region=%s variant=%s psm=%s -> no match (text=%r)", region_name, variant_name, psm, text[:80])
            return
        parsed_candidates.append(timestamp)
        logger.debug("region=%s variant=%s psm=%s -> %s", region_name, variant_name, psm, timestamp)

    try:
        image = Image.open(image_path)
        # Apply the phone's EXIF rotation flag (PIL doesn't do this on open) so a
        # sideways-saved photo is read upright. Replaces the old Tesseract-OSD
        # deskew pass, which cost a full-image OCR call on every upload to handle
        # the same case less reliably.
        image = ImageOps.exif_transpose(image)
        # Cap resolution before any cropping/variant work. A 4000px phone photo
        # feeds every downstream Tesseract pass (and the 2x/3x upscaled variants)
        # far more pixels than watermark text needs; 2400px keeps the text well
        # above Tesseract's legibility floor while cutting pass time substantially.
        # ponytail: fixed 2400px cap, make it a setting if a site's watermark font is unusually small
        image.thumbnail((2400, 2400))
        named_regions = _named_regions(image, preferred_region)
        priority_regions = named_regions[:2]
        remaining_regions = named_regions[2:]

        # Stage 1: cheap variants on the highest-priority regions only.
        for region_name, region in priority_regions:
            for variant_name, prepared in _fast_variants(region, Image, ImageEnhance, ImageOps):
                for psm in (6, 7):
                    run_pass(region_name, variant_name, prepared, psm)
        confidence, agreement = _confidence_state(parsed_candidates)

        # Stage 2: same regions, broader variant set + remaining PSM modes --
        # only if stage 1 didn't already reach HIGH_CONFIDENCE.
        if confidence != OCRConfidence.HIGH:
            for region_name, region in priority_regions:
                for variant_name, prepared in _fast_variants(region, Image, ImageEnhance, ImageOps):
                    run_pass(region_name, variant_name, prepared, 11)
                for variant_name, prepared in _extended_variants(region, Image, ImageEnhance, ImageOps, ImageFilter, ImageStat):
                    for psm in (6, 7, 11):
                        run_pass(region_name, variant_name, prepared, psm)
            confidence, agreement = _confidence_state(parsed_candidates)

        # Stage 3: every remaining region (corners, full image, etc.) with the
        # full variant set and PSM 13 added -- only reached when the priority
        # regions genuinely couldn't produce a confident answer.
        if confidence != OCRConfidence.HIGH:
            for region_name, region in remaining_regions:
                variants = _fast_variants(region, Image, ImageEnhance, ImageOps) + _extended_variants(
                    region, Image, ImageEnhance, ImageOps, ImageFilter, ImageStat
                )
                for variant_name, prepared in variants:
                    for psm in (6, 7, 11, 13):
                        run_pass(region_name, variant_name, prepared, psm)
            confidence, agreement = _confidence_state(parsed_candidates)
    except (OSError, pytesseract.TesseractNotFoundError) as error:
        raise WatermarkError("The image could not be processed by OCR.") from error

    if time.monotonic() > deadline:
        logger.warning("watermark OCR hit the %.0fs budget after %d passes", _OCR_BUDGET_SECONDS, attempts)

    site = _pick_most_likely_title(site_candidates)

    if parsed_candidates:
        timestamp = Counter(parsed_candidates).most_common(1)[0][0]
        logger.info(
            "watermark result=%s confidence=%s agreement=%d/%d attempts", timestamp, confidence.value, agreement, attempts
        )
        return WatermarkResult(timestamp=timestamp, site=site, confidence=confidence, agreement=agreement, total_attempts=attempts)

    if caption:
        try:
            timestamp = extract_timestamp_from_caption(caption)
        except WatermarkError as error:
            logger.info("watermark OCR_UNCERTAIN: image and caption both unreadable (%d attempts)", attempts)
            raise WatermarkError(
                "Unable to read a valid date and time watermark, and no readable "
                "date/time was found in the attached message text."
            ) from error
        logger.info("watermark result=%s confidence=MEDIUM (from caption fallback, %d image attempts)", timestamp, attempts)
        return WatermarkResult(timestamp=timestamp, site=site, confidence=OCRConfidence.MEDIUM, agreement=1, total_attempts=attempts)

    logger.info("watermark OCR_UNCERTAIN: no valid candidates from %d attempts", attempts)
    raise WatermarkError("Unable to read a valid date and time watermark.")
