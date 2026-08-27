from pathlib import Path

from .models import AttendanceRecord, RecordType
from .ocr import build_filename, read_watermark


class AttendanceService:
    def __init__(self, ocr_reader, storage, repository):
        self.ocr_reader = ocr_reader
        self.storage = storage
        self.repository = repository

    def process(self, image_path: Path, record_type: RecordType, employee: str, telegram_user_id: str, telegram_message_id: str) -> AttendanceRecord:
        timestamp = self.ocr_reader(image_path)
        filename = build_filename(timestamp, record_type, employee)
        if self.repository.existing(timestamp.strftime("%Y-%m-%d"), timestamp.strftime("%H:%M:%S"), str(record_type), employee):
            raise ValueError("This attendance record already exists.")
        image_url = self.storage.upload(image_path, timestamp, record_type, filename)
        record = AttendanceRecord(
            record_id=self.repository.next_id(), timestamp=timestamp, record_type=record_type,
            employee=employee, image_url=image_url, telegram_user_id=telegram_user_id,
            telegram_message_id=telegram_message_id,
        )
        try:
            self.repository.add(record)
        except Exception as error:
            raise RuntimeError("Image uploaded, but the Google Sheets record could not be created.") from error
        return record


def configured_service(settings):
    from .drive import DriveStorage
    from .sheets import SheetsRepository
    return AttendanceService(lambda path: read_watermark(path, settings.tesseract_cmd), DriveStorage(settings), SheetsRepository(settings))
