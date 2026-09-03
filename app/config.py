import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    telegram_token: str
    google_service_account_file: Path
    google_sheet_id: str
    google_worksheet: str
    google_drive_root_folder_id: str
    google_drive_root_folder_name: str
    google_drive_oauth_credentials_file: Path
    google_drive_oauth_token_file: Path
    tesseract_cmd: str | None
    timezone: str
    dryrun_storage_dir: str
    app_mode: str
    employee_roster_file: Path
    ocr_debug: bool
    ocr_debug_log_file: Path
    fast_ocr: bool

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        return cls(
            telegram_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
            google_service_account_file=Path(os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "service-account.json")),
            google_sheet_id=os.getenv("GOOGLE_SHEET_ID", ""),
            google_worksheet=os.getenv("GOOGLE_WORKSHEET", "Records"),
            google_drive_root_folder_id=os.getenv("GOOGLE_DRIVE_ROOT_FOLDER_ID", ""),
            google_drive_root_folder_name=os.getenv("GOOGLE_DRIVE_ROOT_FOLDER_NAME", "Attendance Records"),
            google_drive_oauth_credentials_file=Path(os.getenv("GOOGLE_DRIVE_OAUTH_CREDENTIALS_FILE", "oauth_credentials.json")),
            google_drive_oauth_token_file=Path(os.getenv("GOOGLE_DRIVE_OAUTH_TOKEN_FILE", "token.json")),
            tesseract_cmd=os.getenv("TESSERACT_CMD") or None,
            timezone=os.getenv("TIMEZONE", "UTC"),
            dryrun_storage_dir=os.getenv("DRYRUN_STORAGE_DIR", "samples"),
            app_mode=os.getenv("APP_MODE", "dryrun").strip().lower(),
            employee_roster_file=Path(os.getenv("EMPLOYEE_ROSTER_FILE", "employees.txt")),
            ocr_debug=os.getenv("OCR_DEBUG", "false").strip().lower() in ("1", "true", "yes"),
            ocr_debug_log_file=Path(os.getenv("OCR_DEBUG_LOG_FILE", "ocr_debug.log")),
            # Try PaddleOCR first, fall back to Tesseract (see app/fast_ocr.py).
            fast_ocr=os.getenv("FAST_OCR", "false").strip().lower() in ("1", "true", "yes"),
        )
