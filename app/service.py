from contextlib import nullcontext
from pathlib import Path

from .models import AttendanceRecord, RecordType
from .net import retry_network
from .ocr import WatermarkError, read_watermark


class AttendanceService:
    def __init__(self, ocr_reader, storage, repository):
        self.ocr_reader = ocr_reader
        self.storage = storage
        self.repository = repository

    def process(
        self, image_paths, record_type: RecordType, employees, telegram_user_id: str,
        telegram_message_id: str, caption: str | None = None,
    ) -> tuple[list[AttendanceRecord], list[str]]:
        """Record one time-in/out submission.

        `image_paths` is one or more photos (an album), `employees` one or more
        people the submission is for. All photos go into a single Drive folder
        and every employee's Sheet row links to that folder. Returns
        ``(records, skipped)`` -- skipped lists employees whose row already
        existed.

        `caption` is the text sent with the photo, used as the date/time fallback
        when no watermark reads (see app/ocr.py).
        """
        if isinstance(image_paths, (str, Path)):
            image_paths = [image_paths]
        image_paths = list(image_paths)
        if isinstance(employees, str):
            employees = [employees]

        # OCR: the first photo that yields a valid timestamp wins; the caption is
        # the fallback (handled inside ocr_reader). An album's photos can be of
        # different people, but the time-in/out is one moment.
        timestamp, last_error = None, None
        for image_path in image_paths:
            try:
                timestamp = self.ocr_reader(image_path, caption)
                break
            except (WatermarkError, ValueError) as error:
                last_error = error
        if timestamp is None:
            raise last_error

        date_text = timestamp.strftime("%Y-%m-%d")
        time_text = timestamp.strftime("%H:%M:%S")
        folder_label = f"{timestamp:%Y-%m-%d_%H%M%S}_{telegram_message_id}"

        def exists(employee: str):
            # ponytail: one full-sheet read per employee (13-name album = 13 reads);
            # add a batch existing() on the repository if that shows up in latency.
            return lambda: self.repository.existing(date_text, time_text, str(record_type), employee)

        records: list[AttendanceRecord] = []
        skipped: list[str] = []
        try:
            with getattr(self.repository, "lock", None) or nullcontext():
                # Skip the upload entirely if the whole submission is a re-send.
                if all(
                    retry_network(exists(employee), description="Google Sheets duplicate check")
                    for employee in employees
                ):
                    raise ValueError("This attendance record already exists.")

                folder_url = retry_network(
                    self.storage.upload, image_paths, timestamp, record_type, folder_label,
                    description="Google Drive image upload",
                )
                for employee in employees:
                    if retry_network(exists(employee), description="Google Sheets duplicate check"):
                        skipped.append(employee)
                        continue
                    record = AttendanceRecord(
                        record_id=retry_network(self.repository.next_id, description="Google Sheets ID lookup"),
                        timestamp=timestamp, record_type=record_type,
                        employee=employee, image_url=folder_url, telegram_user_id=telegram_user_id,
                        telegram_message_id=telegram_message_id,
                    )
                    try:
                        # already_done guards against a double row if a first append
                        # actually reached the Sheet but the response was lost.
                        retry_network(
                            self.repository.add, record,
                            description="Google Sheets row append", already_done=exists(employee),
                        )
                    except OSError:
                        raise
                    except Exception as error:
                        raise RuntimeError("Image uploaded, but the Google Sheets record could not be created.") from error
                    records.append(record)
        except ValueError:
            raise
        except OSError as error:
            raise RuntimeError(
                "Couldn't reach Google (network problem) after several tries. "
                "Nothing was recorded -- please try again."
            ) from error
        return records, skipped


def configured_service(settings):
    from .drive import DriveStorage
    from .sheets import SheetsRepository

    def tesseract_reader(path, caption=None):
        return read_watermark(path, settings.tesseract_cmd, caption)

    ocr_reader = tesseract_reader
    if getattr(settings, "fast_ocr", False):
        from .fast_ocr import paddle_first_reader
        ocr_reader = paddle_first_reader(tesseract_reader)

    return AttendanceService(ocr_reader, DriveStorage(settings), SheetsRepository(settings))
