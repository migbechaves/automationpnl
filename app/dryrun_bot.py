import asyncio
import logging
from datetime import datetime
from pathlib import Path

from telegram import Update
from telegram.error import TelegramError
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from .bot import application_builder, on_error
from .models import RecordType
from .ocr import WatermarkError, read_watermark_details
from .scan import image_files, scan_images

logger = logging.getLogger(__name__)


async def _download_with_retry(getter, image_path: Path, attempts: int = 3, delay: float = 2.0) -> None:
    """Download a Telegram file, retrying transient network errors before giving
    up. `getter` is a zero-arg coroutine returning the telegram.File to fetch.
    """
    last_error: TelegramError | None = None
    wait = delay
    for attempt in range(attempts):
        try:
            await (await getter()).download_to_drive(image_path)
            return
        except TelegramError as error:
            last_error = error
            if attempt < attempts - 1:
                await asyncio.sleep(wait)
                wait *= 2
    raise last_error


def _target_folder(base_dir: Path, record_type: RecordType) -> Path:
    folder = base_dir / ("timein" if record_type == RecordType.TIME_IN else "timeout")
    folder.mkdir(parents=True, exist_ok=True)
    return folder


async def _scan_and_reply(
    update: Update, image_path: Path, record_type: RecordType, tesseract_cmd: str | None, caption: str | None = None,
) -> None:
    await update.message.reply_text("⏳ Please wait, analyzing your image... don't send anything else yet.")
    try:
        # OCR is blocking; run it on a worker thread so it doesn't freeze the bot
        # for every other user while it works.
        timestamp, site_name, confidence = await asyncio.to_thread(read_watermark_details, image_path, tesseract_cmd, caption)
        await update.message.reply_text(
            "ATTENDANCE SCAN RESULT\n"
            "Status: SUCCESS\n"
            f"Confidence: {confidence.value}\n"
            f"Type: {record_type}\n"
            f"SITE: {site_name}\n"
            f"Date: {timestamp:%Y-%m-%d}\n"
            f"Time: {timestamp:%H:%M:%S}\n"
            f"Saved: {image_path}"
        )
    except WatermarkError as error:
        await update.message.reply_text(
            "ATTENDANCE SCAN RESULT\n"
            "Status: FAILED\n"
            f"Type: {record_type}\n"
            f"Reason: {error}\n"
            f"Saved for review: {image_path}"
        )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "👋 Welcome to TimeKeeping!\n\n"
        "Para mag-time in, i-type ang /in\n"
        "Para mag-time out, i-type ang /out"
    )


async def choose_type(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # In a group the text is "/in@BotName" (and may carry args), so match the
    # bare command word, not the whole message.
    command = update.message.text[1:].split("@")[0].split()[0].lower()
    context.user_data["record_type"] = RecordType.TIME_IN if command == "in" else RecordType.TIME_OUT
    await update.message.reply_text(f"Upload your {context.user_data['record_type']} image now.")


async def where(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(f"Local storage folder: {context.application.bot_data['dryrun_storage_dir']}")


async def summary(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = context.application.bot_data["settings"]
    base_dir = context.application.bot_data["dryrun_storage_dir"]

    timein_results = await asyncio.to_thread(scan_images, image_files(base_dir / "timein"), RecordType.TIME_IN, settings.tesseract_cmd)
    timeout_results = await asyncio.to_thread(scan_images, image_files(base_dir / "timeout"), RecordType.TIME_OUT, settings.tesseract_cmd)
    all_results = [*timein_results, *timeout_results]

    def stats(results):
        total = len(results)
        ok = sum(1 for item in results if item.ok)
        rate = (ok / total * 100) if total else 0.0
        return ok, total, rate

    ok_in, total_in, rate_in = stats(timein_results)
    ok_out, total_out, rate_out = stats(timeout_results)
    ok_all, total_all, rate_all = stats(all_results)

    await update.message.reply_text(
        "ATTENDANCE OCR SUMMARY\n"
        f"TIME-IN: {ok_in}/{total_in} ({rate_in:.2f}%)\n"
        f"TIME-OUT: {ok_out}/{total_out} ({rate_out:.2f}%)\n"
        f"OVERALL: {ok_all}/{total_all} ({rate_all:.2f}%)"
    )


async def receive_image(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    record_type = context.user_data.get("record_type")
    if not record_type:
        await update.message.reply_text("Start with /in or /out.")
        return

    settings = context.application.bot_data["settings"]
    base_dir = context.application.bot_data["dryrun_storage_dir"]
    target_folder = _target_folder(base_dir, record_type)

    file_name = f"{datetime.now():%Y%m%d_%H%M%S}_{update.effective_user.id}_{update.message.id}.jpg"
    image_path = target_folder / file_name
    try:
        await _download_with_retry(lambda: update.message.photo[-1].get_file(), image_path)
    except TelegramError as error:
        logger.exception("Could not download photo from Telegram: %r", error)
        await update.message.reply_text("Couldn't download your image from Telegram (network problem). Please send it again.")
        return
    await _scan_and_reply(update, image_path, record_type, settings.tesseract_cmd, update.message.caption)


async def receive_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    record_type = context.user_data.get("record_type")
    if not record_type:
        await update.message.reply_text("Start with /in or /out.")
        return

    document = update.message.document
    if not document:
        await update.message.reply_text("No document detected.")
        return
    mime = (document.mime_type or "").lower()
    if not mime.startswith("image/"):
        await update.message.reply_text("Please upload an image file.")
        return

    settings = context.application.bot_data["settings"]
    base_dir = context.application.bot_data["dryrun_storage_dir"]
    target_folder = _target_folder(base_dir, record_type)

    suffix = Path(document.file_name or "image.jpg").suffix.lower() or ".jpg"
    file_name = f"{datetime.now():%Y%m%d_%H%M%S}_{update.effective_user.id}_{update.message.id}{suffix}"
    image_path = target_folder / file_name
    try:
        await _download_with_retry(document.get_file, image_path)
    except TelegramError as error:
        logger.exception("Could not download document from Telegram: %r", error)
        await update.message.reply_text("Couldn't download your file from Telegram (network problem). Please send it again.")
        return
    await _scan_and_reply(update, image_path, record_type, settings.tesseract_cmd, update.message.caption)


def create_dryrun_application(settings, storage_root: Path) -> Application:
    if not settings.telegram_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured.")

    storage_root.mkdir(parents=True, exist_ok=True)
    application = application_builder(settings).build()
    application.bot_data.update(settings=settings, dryrun_storage_dir=storage_root)

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler(["in", "out"], choose_type))
    application.add_handler(CommandHandler("where", where))
    application.add_handler(CommandHandler("summary", summary))
    application.add_handler(MessageHandler(filters.PHOTO, receive_image))
    application.add_handler(MessageHandler(filters.Document.IMAGE, receive_document))
    application.add_error_handler(on_error)
    return application
