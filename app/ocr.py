import re
from collections import Counter
from datetime import datetime
from pathlib import Path

from .models import RecordType


TIMESTAMP_PATTERNS = (
    re.compile(r"(?P<date>\d{1,2}[/-]\d{1,2}[/-]\d{4})\s+(?P<time>\d{1,2}[:;]\d{2}(?:[:;]\d{2})?\s*(?:A\.?M\.?|P\.?M\.?)?)", re.IGNORECASE),
    re.compile(r"(?P<date>\d{4}[/-]\d{1,2}[/-]\d{1,2})\s+(?P<time>\d{1,2}[:;]\d{2}(?:[:;]\d{2})?\s*(?:A\.?M\.?|P\.?M\.?)?)", re.IGNORECASE),
)

TIME_FIRST_PATTERN = re.compile(
    r"(?P<time>\d{1,2}[:;\-]\d{2}(?:[:;\-]\d{2})?\s*(?:A\.?M\.?|P\.?M\.?)?)"
    r"\s+(?:[A-Za-z]{3,9}\s+)?"
    r"(?P<date>(?:[A-Za-z]{3,9})\s*\d{1,2},?\s*\d{4})",
    re.IGNORECASE,
)

TIME_ONLY_PATTERN = re.compile(r"\b(?P<time>\d{1,2}[:;\-]\d{2}(?:[:;\-]\d{2})?\s*(?:A\.?M\.?|P\.?M\.?)?)\b", re.IGNORECASE)
MONTH_DATE_PATTERN = re.compile(r"\b(?P<date>[A-Za-z]{3,9}\s*\d{1,2},?\s*\d{4})\b", re.IGNORECASE)
NUMERIC_DATE_PATTERN = re.compile(r"\b(?P<date>\d{1,4}[/-]\d{1,2}[/-]\d{2,4})\b")


class WatermarkError(ValueError):
    pass


def _clean_title_line(line: str) -> str:
    line = re.sub(r"([a-z])([A-Z])", r"\1 \2", line)
    line = re.sub(r"\s+", " ", line).strip(" -_:|\t")
    return line


def _extract_scan_title(text: str) -> str:
    blocked_terms = ("address", "photo", "timemark", "boulevard", "pier", "thurs", "monday", "tuesday", "wednesday", "friday", "saturday", "sunday", "am", "pm")
    preferred_terms = ("marine", "mall", "plaza", "campus", "office", "building", "site")
    lines = [_clean_title_line(item) for item in text.splitlines()]
    candidates: list[str] = []
    for line in lines:
        if len(line) < 4:
            continue
        if re.search(r"\d", line):
            continue
        lowered = line.lower()
        if any(term in lowered for term in blocked_terms):
            continue
        if line.lower() == "security patrol":
            continue
        if not re.search(r"[a-zA-Z]", line):
            continue
        candidates.append(line)
    if not candidates:
        return "Unknown"
    for line in candidates:
        if any(term in line.lower() for term in preferred_terms):
            return line
    return candidates[0]


def _pick_most_likely_timestamp(candidates: list[datetime]) -> datetime:
    if not candidates:
        raise WatermarkError("Unable to read a valid date and time watermark.")
    # Prefer the value that appears most often across OCR passes.
    return Counter(candidates).most_common(1)[0][0]


def _pick_most_likely_title(candidates: list[str]) -> str:
    cleaned = [item for item in candidates if item and item != "Unknown"]
    if not cleaned:
        return "Unknown"
    return Counter(cleaned).most_common(1)[0][0]


def _normalize_ocr_text(text: str) -> str:
    table = str.maketrans({
        "O": "0",
        "o": "0",
        "I": "1",
        "l": "1",
        "|": "1",
    })
    normalized = text.translate(table)
    normalized = normalized.replace(";", ":")
    normalized = re.sub(r"\b([AP])\W*M(\W*)", r"\1M\2", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\b(AM|PM)([A-Za-z]{3,9})\b", r"\1 \2", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\b([A-Za-z]{3,9})(\d{1,2})(,?)(\d{4})\b", r"\1 \2\3 \4", normalized)
    normalized = re.sub(r"(\d{1,2}:\d{2}(?::\d{2})?)\s*([AP])\s*M\b", r"\1 \2M", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def extract_timestamp(text: str) -> datetime:
    normalized = _normalize_ocr_text(text)

    time_first_match = TIME_FIRST_PATTERN.search(normalized)
    if time_first_match:
        date_text = re.sub(r"\s+", " ", time_first_match.group("date").replace(",", "")).strip()
        time_text = time_first_match.group("time").replace(";", ":").replace("-", ":").upper().replace(" ", "").replace(".", "")
        for date_format in (
            "%I:%M:%S%p %b %d %Y",
            "%I:%M%p %b %d %Y",
            "%I:%M:%S %b %d %Y",
            "%I:%M %b %d %Y",
            "%H:%M:%S %b %d %Y",
            "%H:%M %b %d %Y",
            "%I:%M:%S%p %B %d %Y",
            "%I:%M%p %B %d %Y",
            "%I:%M:%S %B %d %Y",
            "%I:%M %B %d %Y",
            "%H:%M:%S %B %d %Y",
            "%H:%M %B %d %Y",
        ):
            try:
                return datetime.strptime(f"{time_text} {date_text}", date_format)
            except ValueError:
                pass

    for pattern in TIMESTAMP_PATTERNS:
        match = pattern.search(normalized)
        if not match:
            continue
        date_text = match.group("date").replace("-", "/")
        time_text = match.group("time").replace(";", ":").upper().replace(" ", "").replace(".", "")
        formats = (
            "%m/%d/%Y %I:%M:%S%p",
            "%m/%d/%Y %I:%M%p",
            "%Y/%m/%d %I:%M:%S%p",
            "%Y/%m/%d %I:%M%p",
            "%Y/%m/%d %H:%M:%S",
            "%Y/%m/%d %H:%M",
        )
        for date_format in formats:
            try:
                return datetime.strptime(f"{date_text} {time_text}", date_format)
            except ValueError:
                pass

    # Fallback for overlays that place time and date on separate lines.
    time_match = TIME_ONLY_PATTERN.search(normalized)
    date_match = MONTH_DATE_PATTERN.search(normalized) or NUMERIC_DATE_PATTERN.search(normalized)
    if time_match and date_match:
        time_text = time_match.group("time").replace(";", ":").replace("-", ":").upper().replace(" ", "").replace(".", "")
        date_text = date_match.group("date").replace("-", "/").replace(",", "")
        formats = (
            "%I:%M:%S%p %b %d %Y",
            "%I:%M%p %b %d %Y",
            "%I:%M:%S %b %d %Y",
            "%I:%M %b %d %Y",
            "%H:%M:%S %b %d %Y",
            "%H:%M %b %d %Y",
            "%I:%M:%S%p %B %d %Y",
            "%I:%M%p %B %d %Y",
            "%I:%M:%S %B %d %Y",
            "%I:%M %B %d %Y",
            "%H:%M:%S %B %d %Y",
            "%H:%M %B %d %Y",
            "%m/%d/%Y %H:%M:%S",
            "%m/%d/%Y %H:%M",
            "%Y/%m/%d %H:%M:%S",
            "%Y/%m/%d %H:%M",
            "%m/%d/%Y %I:%M:%S%p",
            "%m/%d/%Y %I:%M%p",
            "%Y/%m/%d %I:%M:%S%p",
            "%Y/%m/%d %I:%M%p",
        )
        for date_format in formats:
            try:
                return datetime.strptime(f"{date_text} {time_text}", date_format)
            except ValueError:
                pass

    raise WatermarkError("Unable to read a valid date and time watermark.")


def read_watermark(image_path: Path, tesseract_cmd: str | None = None) -> datetime:
    timestamp, _ = read_watermark_details(image_path, tesseract_cmd)
    return timestamp


def read_watermark_details(image_path: Path, tesseract_cmd: str | None = None) -> tuple[datetime, str]:
    try:
        import pytesseract
        from PIL import Image, ImageEnhance, ImageOps
    except ImportError as error:
        raise WatermarkError("OCR dependencies are not installed.") from error
    if tesseract_cmd:
        pytesseract.pytesseract.tesseract_cmd = tesseract_cmd
    try:
        image = Image.open(image_path)
        width, height = image.size
        regions = (
            image,
            image.crop((0, int(height * 0.45), width, height)),
            image.crop((0, int(height * 0.40), int(width * 0.72), height)),
        )
        parsed_candidates: list[datetime] = []
        site_candidates: list[str] = []
        for region in regions:
            gray = ImageOps.grayscale(region)
            high_contrast = ImageEnhance.Contrast(gray).enhance(2)
            threshold = gray.point(lambda value: 255 if value > 165 else 0)
            prepared_candidates = (high_contrast, threshold)
            for prepared in prepared_candidates:
                for psm in (6, 7, 11):
                    config = (
                        f"--oem 3 --psm {psm} "
                        "-c preserve_interword_spaces=1 "
                        "-c tessedit_char_whitelist=0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz:.,/- "
                    )
                    text = pytesseract.image_to_string(prepared, config=config)
                    site_candidates.append(_extract_scan_title(text))
                    try:
                        parsed_candidates.append(extract_timestamp(text))
                    except WatermarkError:
                        continue
    except (OSError, pytesseract.TesseractNotFoundError) as error:
        raise WatermarkError("The image could not be processed by OCR.") from error
    return _pick_most_likely_timestamp(parsed_candidates), _pick_most_likely_title(site_candidates)


def build_filename(timestamp: datetime, record_type: RecordType, employee: str = "") -> str:
    prefix = f"{employee}_" if employee else ""
    return f"{prefix}{timestamp:%Y-%m-%d_%H%M%S}_{record_type}.jpg"
