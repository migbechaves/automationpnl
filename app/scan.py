from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .models import RecordType
from .ocr import WatermarkError, read_watermark


@dataclass(frozen=True)
class ScanResult:
    image_path: Path
    record_type: RecordType
    ok: bool
    timestamp: datetime | None = None
    error: str = ""


def scan_images(
    image_paths: list[Path],
    record_type: RecordType,
    tesseract_cmd: str | None = None,
    reader=read_watermark,
) -> list[ScanResult]:
    results: list[ScanResult] = []
    for image_path in image_paths:
        try:
            timestamp = reader(image_path, tesseract_cmd)
            results.append(ScanResult(image_path=image_path, record_type=record_type, ok=True, timestamp=timestamp))
        except (WatermarkError, OSError, ValueError) as error:
            results.append(ScanResult(image_path=image_path, record_type=record_type, ok=False, error=str(error)))
    return results


def image_files(folder: Path) -> list[Path]:
    if not folder.exists():
        return []
    extensions = {".jpg", ".jpeg", ".png"}
    return sorted(path for path in folder.iterdir() if path.is_file() and path.suffix.lower() in extensions)
