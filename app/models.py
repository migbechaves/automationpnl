from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path


class RecordType(StrEnum):
    TIME_IN = "TIME-IN"
    TIME_OUT = "TIME-OUT"


@dataclass(frozen=True)
class AttendanceRecord:
    record_id: str
    timestamp: datetime
    record_type: RecordType
    employee: str
    image_url: str
    telegram_user_id: str
    telegram_message_id: str
    status: str = "Valid"

    @property
    def date(self) -> str:
        return self.timestamp.strftime("%Y-%m-%d")

    @property
    def time(self) -> str:
        return self.timestamp.strftime("%H:%M:%S")


@dataclass(frozen=True)
class ProcessedImage:
    path: Path
    timestamp: datetime
    record_type: RecordType
    filename: str
