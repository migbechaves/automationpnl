import threading
from datetime import datetime

from .config import Settings
from .models import AttendanceRecord

HEADERS = ["Record ID", "Date", "Time", "Type", "Employee", "Image", "Telegram ID", "Message ID", "Status"]


class SheetsRepository:
    def __init__(self, settings: Settings):
        try:
            import gspread
            from google.oauth2.service_account import Credentials
        except ImportError as error:
            raise RuntimeError("Install Google API dependencies first.") from error
        credentials = Credentials.from_service_account_file(
            settings.google_service_account_file,
            scopes=["https://www.googleapis.com/auth/spreadsheets"],
        )
        self.sheet = gspread.authorize(credentials).open_by_key(settings.google_sheet_id).worksheet(settings.google_worksheet)
        if not self.sheet.row_values(1):
            self.sheet.append_row(HEADERS)
        # The underlying HTTP session isn't safe to call from multiple threads at
        # once, and multiple employees can now be processed concurrently (see
        # app/bot.py). `lock` is exposed so callers can also hold it across a whole
        # read-then-write sequence (e.g. existing()+next_id()+add()), not just one
        # call at a time, to avoid two concurrent records computing the same ID.
        self.lock = threading.RLock()

    def existing(self, date: str, time: str, record_type: str, employee: str) -> bool:
        with self.lock:
            rows = self.sheet.get_all_records()
            return any(
                row.get("Date") == date and row.get("Time") == time
                and row.get("Type") == record_type and row.get("Employee") == employee
                for row in rows
            )

    def next_id(self) -> str:
        with self.lock:
            return f"{len(self.sheet.get_all_values()):04d}"

    def add(self, record: AttendanceRecord) -> None:
        with self.lock:
            self.sheet.append_row([
                record.record_id, record.date, record.time, str(record.record_type), record.employee,
                f'=HYPERLINK("{record.image_url}","View")', record.telegram_user_id,
                record.telegram_message_id, record.status,
            ], value_input_option="USER_ENTERED")

    def rows(self) -> list[dict]:
        with self.lock:
            return self.sheet.get_all_records()
