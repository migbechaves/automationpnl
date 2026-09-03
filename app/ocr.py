import re
from collections import Counter
from datetime import datetime
from pathlib import Path

from .models import RecordType


TIMESTAMP_PATTERNS = (
    re.compile(r"(?P<date>\d{1,2}[/-]\d{1,2}[/-]\d{4})\s+(?P<time>\d{1,2}[:;]\d{2}(?:[:;]\d{2})?\s*(?:A\.?M\.?|P\.?M\.?)?)", re.IGNORECASE),
    re.compile(r"(?P<date>\d{4}[/-]\d{1,2}[/-]\d{1,2})\s+(?P<time>\d{1,2}[:;]\d{2}(?:[:;]\d{2})?\s*(?:A\.?M\.?|P\.?M\.?)?)", re.IGNORECASE),
)

# Real month names only (3-letter or full, "Sept" too). A loose [A-Za-z]{3,9}
# here let any word followed by "<n> <year>" -- e.g. a site line like
# "Makati 26 2026" or "Navotas 8 2026" -- get matched as a date and then block
# the real numeric date elsewhere in the text. A location is not a date.
_MONTH_NAME = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*"

TIME_FIRST_PATTERN = re.compile(
    r"(?P<time>\d{1,2}[:;\-]\d{2}(?:[:;\-]\d{2})?\s*(?:A\.?M\.?|P\.?M\.?)?)"
    r"\s+(?:[A-Za-z]{3,9}[.,]?\s+)?"
    r"(?P<date>" + _MONTH_NAME + r"\s*\d{1,2},?\s*\d{4})",
    re.IGNORECASE,
)

TIME_ONLY_PATTERN = re.compile(r"\b(?P<time>\d{1,2}[:;\-]\d{2}(?:[:;\-]\d{2})?\s*(?:A\.?M\.?|P\.?M\.?)?)\b", re.IGNORECASE)
MONTH_DATE_PATTERN = re.compile(r"\b(?P<date>" + _MONTH_NAME + r"\s*\d{1,2},?\s*\d{4})\b", re.IGNORECASE)
NUMERIC_DATE_PATTERN = re.compile(r"\b(?P<date>\d{1,4}[/-]\d{1,2}[/-]\d{2,4})\b")

# Caption fallback: when the watermark itself can't be read, look for a labeled
# "Date:" / "Time in:" / "Time out:" field in the message text sent alongside the
# photo (e.g. a duty-report caption). Supports month-name dates ("August17-2026")
# and numeric dates, plus both AM/PM and bare military time ("1119H" -> 11:19).
CAPTION_DATE_PATTERN = re.compile(
    r"date\s*[:\-]?\s*"
    r"(?P<date>[A-Za-z]{3,9}\s*\d{1,2}(?:st|nd|rd|th)?[\s,-]*\d{4}"
    r"|\d{1,2}[/-]\d{1,2}[/-]\d{2,4})",
    re.IGNORECASE,
)
CAPTION_TIME_PATTERN = re.compile(
    r"time\s*(?:[-_]?\s*(?:in|out))?\s*[:\-]?\s*"
    r"(?P<hour>\d{1,2})(?P<sep>[:.]?)(?P<minute>\d{2})?"
    r"\s*(?P<meridiem>[AaPp]\.?[Mm]\.?)?\s*(?P<military>[Hh])?\b",
    re.IGNORECASE,
)
# Same idea, for a labeled "Name:" field -- lets the caption identify who the
# record belongs to (e.g. when one Telegram account submits for several
# employees) instead of falling back to the sender's Telegram username/ID.
CAPTION_NAME_PATTERN = re.compile(r"name\s*[:\-]?\s*(?P<name>[^\n:]+)", re.IGNORECASE)


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
        # Uppercase only: "S"/"B" are common OCR misreads for "5"/"8" inside a
        # digit run, but the lowercase forms show up in ordinal date suffixes
        # ("1st", "2nd") that must NOT get mangled -- so this stays asymmetric
        # with the O/I pairs above rather than adding "s"/"b" too.
        "S": "5",
        "B": "8",
    })

    def _fix_digit_lookalikes(token: str) -> str:
        # Only fold O/I/l into digits inside tokens that already contain a real
        # digit (a mangled date/time chunk, e.g. "2O26" or "O8;15"). Applying this
        # to the whole text would also corrupt real words like weekday/month
        # names ("Mon" -> "M0n", "Oct" -> "0ct", "Nov" -> "N0v").
        if any(character.isdigit() for character in token):
            return token.translate(table)
        return token

    normalized = re.sub(r"\S+", lambda match: _fix_digit_lookalikes(match.group(0)), text)
    normalized = normalized.replace(";", ":")
    normalized = re.sub(r"\b([AP])\W*M(\W*)", r"\1M\2", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\b(AM|PM)([A-Za-z]{3,9})\b", r"\1 \2", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\b([A-Za-z]{3,9})(\d{1,2})(,?)(\d{4})\b", r"\1 \2\3 \4", normalized)
    normalized = re.sub(r"(\d{1,2}:\d{2}(?::\d{2})?)\s*([AP])\s*M\b", r"\1 \2M", normalized, flags=re.IGNORECASE)
    # "3 Jul 2026" -> "Jul 3 2026": GPS Map Camera / Timemark overlays put the day
    # before the month name. Reorder to the month-first form the patterns below
    # already parse, rather than carrying a parallel set of day-first formats.
    normalized = re.sub(
        r"\b(\d{1,2})\s+(" + _MONTH_NAME + r")\s+(\d{4})\b", r"\2 \1 \3", normalized, flags=re.IGNORECASE
    )
    # "14:04 PM" -> "14:04": some phone cameras stamp a 24-hour clock with a stray
    # AM/PM. Drop the marker on an hour >= 13 so it parses; a real 12-hour time
    # (hour 1-12) keeps its marker.
    normalized = re.sub(
        r"\b(1[3-9]|2[0-3])(:[0-5]\d(?::[0-5]\d)?)\s*[AP]\.?M\.?", r"\1\2", normalized, flags=re.IGNORECASE
    )
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
            # Day-first fallback (e.g. GPS Map Camera's DD/MM/YYYY watermark). Tried
            # last so an unambiguous MM/DD/YYYY match above always wins first.
            "%d/%m/%Y %I:%M:%S%p",
            "%d/%m/%Y %I:%M%p",
            "%d/%m/%Y %H:%M:%S",
            "%d/%m/%Y %H:%M",
        )
        for date_format in formats:
            try:
                return datetime.strptime(f"{date_text} {time_text}", date_format)
            except ValueError:
                pass

    # Fallback for overlays that place time and date on separate lines. Date is
    # located first so the time search can skip over it -- a dash-separated numeric
    # date (e.g. "2026-08-17") would otherwise also satisfy TIME_ONLY_PATTERN (its
    # separator class includes "-"), stealing a false "08:17" match out of the date
    # itself instead of finding the real time elsewhere in the text.
    date_match = MONTH_DATE_PATTERN.search(normalized) or NUMERIC_DATE_PATTERN.search(normalized)
    time_match = None
    for candidate in TIME_ONLY_PATTERN.finditer(normalized):
        if date_match and candidate.start() < date_match.end() and candidate.end() > date_match.start():
            continue
        time_match = candidate
        break
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
            # Day-first fallback (e.g. GPS Map Camera's DD/MM/YYYY watermark). Tried
            # last so an unambiguous MM/DD/YYYY match above always wins first.
            "%d/%m/%Y %H:%M:%S",
            "%d/%m/%Y %H:%M",
            "%d/%m/%Y %I:%M:%S%p",
            "%d/%m/%Y %I:%M%p",
        )
        # Time and date were found independently (no fixed relative order), and this
        # format list mixes time-first specifiers (month-name formats) with
        # date-first ones (numeric formats) -- so try both orderings against every
        # format rather than assuming the on-screen order matches the format order.
        for candidate in (f"{time_text} {date_text}", f"{date_text} {time_text}"):
            for date_format in formats:
                try:
                    return datetime.strptime(candidate, date_format)
                except ValueError:
                    pass

    raise WatermarkError("Unable to read a valid date and time watermark.")


CATEGORY_HEADERS = ("worker", "cadet")

# Words that mean a caption line is a report field / header / place, not a
# person's name. Used to skip junk when reading an unlabelled vertical list of
# names. A line is dropped only when *every* word on it is in here, so mixed
# lines like "Nestor bagayawa" still count as a name.
# Add your site / barangay / city words to the second row as they come up.
_CAPTION_NON_NAME_WORDS = {
    "name", "date", "time", "timein", "timeout", "worker", "workers", "cadet",
    "cadets", "in", "out", "inn", "am", "pm", "purpose", "destination", "location",
    "site", "remarks", "note", "notes", "duty", "shift", "status", "on", "at",
    "makati", "navotas", "naic", "vista", "marine", "vistamarine", "city",
    "barangay", "brgy", "head", "office", "manila", "metro",
}


def _looks_like_bare_name(line: str) -> bool:
    """Heuristic: a caption line that is probably just a person's name, with no
    "Name:" label and no category header above it. Rejects labelled fields (have
    ":"), anything with digits (dates/times), over-long lines, and lines built
    only from report keywords.

    ponytail: keyword + shape heuristic; a stray note like "late kami" can slip
    through as a name. The RECORDED reply echoes every name back to the sender,
    so a bad line is caught on sight -- extend _CAPTION_NON_NAME_WORDS only if
    that stops being enough.
    """
    words = line.split()
    if not 1 <= len(words) <= 5:
        return False
    if ":" in line or any(character.isdigit() for character in line):
        return False
    if not any(character.isalpha() for character in line):
        return False
    return not {word.strip(".,-").lower() for word in words} <= _CAPTION_NON_NAME_WORDS


def extract_employees_from_caption(text: str | None) -> list[tuple[str, str | None]]:
    """Every person named in the caption, as (name, category).

    Walked line by line:
      * A bare "Worker" / "Cadet" line (also "Workers:", "Cadet:") sets the
        category for the names that follow it.
      * "Name: Juan Dela Cruz" -- or, while under a category header, a bare name
        line -- is one person, tagged with the current header (or None).
      * Otherwise a bare line that looks like a name (see _looks_like_bare_name)
        is taken as a person with no category -- this covers a caption that is
        just a vertical list of names, one per line, with no "Name:" prefix.
      * Report fields (Date:, Time in:, ...) and blank lines are ignored.

    Returns [] when nobody is named.
    """
    if not text:
        return []

    people: list[tuple[str, str | None]] = []
    category: str | None = None
    for line in (raw.strip(" -\t") for raw in text.splitlines()):
        if not line:
            continue
        if line.rstrip(":").rstrip("s").lower() in CATEGORY_HEADERS:
            category = line.rstrip(":").rstrip("s").capitalize()
            continue
        match = CAPTION_NAME_PATTERN.match(line)
        if match:
            if name := match.group("name").strip():
                people.append((name, category))
        elif category and ":" not in line and re.search(r"[A-Za-z]", line):
            people.append((line, category))
        elif _looks_like_bare_name(line):
            people.append((line, None))
    return people


def extract_employee_from_caption(text: str | None) -> str | None:
    """First name in the caption, or None. See extract_employees_from_caption."""
    people = extract_employees_from_caption(text)
    return people[0][0] if people else None


def extract_timestamp_from_caption(text: str) -> datetime:
    """Parse a timestamp from message text sent alongside a photo.

    Used as a fallback when the image watermark itself is unreadable but the
    sender typed the same date/time into the caption. Tries labeled "Date:" /
    "Time in|out:" fields first (handles military time like "1119H" and
    run-together dates like "August17-2026"), then falls back to the same
    general-purpose parser used for watermark text -- so a caption that's just
    a plain timestamp (ISO, day-first, etc.) with no labels also works.
    """
    if not text:
        raise WatermarkError("Unable to read a date and time from the message text.")
    try:
        return _extract_labeled_caption_timestamp(text)
    except WatermarkError:
        return extract_timestamp(text)


def _extract_labeled_caption_timestamp(text: str) -> datetime:
    date_match = CAPTION_DATE_PATTERN.search(text)
    time_match = CAPTION_TIME_PATTERN.search(text)
    if not date_match or not time_match:
        raise WatermarkError("Unable to read a date and time from the message text.")

    date_text = re.sub(r"(?<=[A-Za-z])(?=\d)", " ", date_match.group("date"))
    date_text = re.sub(r"(?<=\d)(?:st|nd|rd|th)", "", date_text, flags=re.IGNORECASE)
    date_text = re.sub(r"[,/-]", " ", date_text)
    date_text = re.sub(r"\s+", " ", date_text).strip()

    parsed_date = None
    for date_format in ("%B %d %Y", "%b %d %Y", "%m %d %Y", "%d %m %Y"):
        try:
            parsed_date = datetime.strptime(date_text, date_format)
            break
        except ValueError:
            continue
    if parsed_date is None:
        raise WatermarkError("Unable to read a date and time from the message text.")

    hour = int(time_match.group("hour"))
    minute = int(time_match.group("minute") or 0)
    meridiem = time_match.group("meridiem")
    if meridiem:
        meridiem = meridiem.upper().replace(".", "")
        if meridiem == "PM" and hour != 12:
            hour += 12
        elif meridiem == "AM" and hour == 12:
            hour = 0
    # No AM/PM marker: either 24-hour ("13:45") or military-style ("1345H") -- both
    # already line up with datetime's 0-23 hour range, so no further adjustment.
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise WatermarkError("Unable to read a date and time from the message text.")

    return parsed_date.replace(hour=hour, minute=minute)


def read_watermark(image_path: Path, tesseract_cmd: str | None = None, caption: str | None = None) -> datetime:
    timestamp, _site, _confidence = read_watermark_details(image_path, tesseract_cmd, caption)
    return timestamp


def read_watermark_details(
    image_path: Path, tesseract_cmd: str | None = None, caption: str | None = None
):
    """Read the watermark timestamp and site name from an image.

    Returns (timestamp, site, confidence) -- confidence is an
    `watermark_ocr.OCRConfidence` (HIGH_CONFIDENCE/MEDIUM_CONFIDENCE), or raises
    WatermarkError when nothing readable was found at all (OCR_UNCERTAIN). The
    actual multi-stage pipeline (preprocessing variants, region detection,
    scoring, consensus voting, staged retries) lives in app/watermark_ocr.py --
    imported here rather than at module level to avoid a circular import, since
    that module reuses this one's text parsing.
    """
    from .watermark_ocr import analyze_watermark

    result = analyze_watermark(image_path, tesseract_cmd, caption)
    return result.timestamp, result.site, result.confidence
