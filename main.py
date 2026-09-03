import logging
import socket
import sys
from pathlib import Path

from app.bot import create_application
from app.config import Settings
from app.dryrun_bot import create_dryrun_application
from app.service import configured_service


if __name__ == "__main__":
    # Single-instance guard. Two pollers on the same bot token make Telegram
    # return 409 Conflict to one of them and both behave erratically -- exactly
    # what happens after `run-bot.bat` (Ctrl+C, "Y") leaves its child python
    # running and you then start `python main.py` by hand. Holding a localhost
    # port for the process lifetime is the cheapest cross-process lock on Windows.
    _instance_lock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        _instance_lock.bind(("127.0.0.1", 47921))
    except OSError:
        sys.exit("Another bot instance is already running (127.0.0.1:47921 in use). Exiting.")

    # Safety net for the blocking Google API clients (gspread / googleapiclient):
    # they have no timeout by default, so a stalled connection would hang a worker
    # thread forever and the retry logic in app/net.py would never get a chance to
    # run. A default socket timeout turns that stall into a retryable OSError.
    # 30s is well above a healthy call; higher just lengthens "analyzing..." on a
    # dead link before the retry/backoff can start. httpx (telegram) ignores this.
    socket.setdefaulttimeout(30)

    settings = Settings.from_env()
    if settings.ocr_debug:
        # Per-attempt OCR debug logging (region/variant/psm/raw text/parsed
        # result) -- see app/watermark_ocr.py. Enable with OCR_DEBUG=true in
        # .env, then check ocr_debug.log after uploading a problem image.
        logging.basicConfig(
            filename=settings.ocr_debug_log_file, level=logging.DEBUG, encoding="utf-8",
            format="%(asctime)s %(name)s %(levelname)s %(message)s",
        )
        logging.getLogger("app.watermark_ocr").setLevel(logging.DEBUG)
    if settings.app_mode == "dryrun":
        application = create_dryrun_application(settings, Path(settings.dryrun_storage_dir))
    elif settings.app_mode == "production":
        application = create_application(settings, configured_service(settings))
    else:
        raise RuntimeError("APP_MODE must be either 'dryrun' or 'production'.")
    # drop_pending_updates: don't replay updates buffered while the bot was down.
    # Those messages are often already deleted by the time we restart, which is
    # what surfaced as "Message to be replied not found" on startup.
    application.run_polling(allowed_updates=["message"], drop_pending_updates=True)
