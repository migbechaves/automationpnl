from datetime import datetime
from pathlib import Path

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from .models import RecordType
from .ocr import WatermarkError, read_watermark_details
from .scan import image_files, scan_images


def _target_folder(base_dir: Path, record_type: RecordType) -> Path:
    folder = base_dir / ("timein" if record_type == RecordType.TIME_IN else "timeout")
    folder.mkdir(parents=True, exist_ok=True)
    return folder


async def _scan_and_reply(update: Update, image_path: Path, record_type: RecordType, tesseract_cmd: str | None) -> None:
    try:
        timestamp, site_name = read_watermark_details(image_path, tesseract_cmd)
        await update.message.reply_text(
            "ATTENDANCE SCAN RESULT\n"
            "Status: SUCCESS\n"
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
        "ATTENDANCE OCR\n\n"
        "1. Send /timein or /timeout\n"
        "2. Upload image"
    )


async def choose_type(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data["record_type"] = RecordType.TIME_IN if update.message.text == "/timein" else RecordType.TIME_OUT
    await update.message.reply_text(f"Upload your {context.user_data['record_type']} image now.")


async def where(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(f"Local storage folder: {context.application.bot_data['dryrun_storage_dir']}")


async def summary(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = context.application.bot_data["settings"]
    base_dir = context.application.bot_data["dryrun_storage_dir"]

    timein_results = scan_images(image_files(base_dir / "timein"), RecordType.TIME_IN, settings.tesseract_cmd)
    timeout_results = scan_images(image_files(base_dir / "timeout"), RecordType.TIME_OUT, settings.tesseract_cmd)
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
        await update.message.reply_text("Start with /timein or /timeout.")
        return

    settings = context.application.bot_data["settings"]
    base_dir = context.application.bot_data["dryrun_storage_dir"]
    target_folder = _target_folder(base_dir, record_type)

    file_name = f"{datetime.now():%Y%m%d_%H%M%S}_{update.effective_user.id}_{update.message.id}.jpg"
    image_path = target_folder / file_name
    file_to_download = await update.message.photo[-1].get_file()
    await file_to_download.download_to_drive(image_path)
    await _scan_and_reply(update, image_path, record_type, settings.tesseract_cmd)


async def receive_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    record_type = context.user_data.get("record_type")
    if not record_type:
        await update.message.reply_text("Start with /timein or /timeout.")
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
    await (await document.get_file()).download_to_drive(image_path)
    await _scan_and_reply(update, image_path, record_type, settings.tesseract_cmd)


def create_dryrun_application(settings, storage_root: Path) -> Application:
    if not settings.telegram_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured.")

    storage_root.mkdir(parents=True, exist_ok=True)
    application = Application.builder().token(settings.telegram_token).build()
    application.bot_data.update(settings=settings, dryrun_storage_dir=storage_root)

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler(["timein", "timeout"], choose_type))
    application.add_handler(CommandHandler("where", where))
    application.add_handler(CommandHandler("summary", summary))
    application.add_handler(MessageHandler(filters.PHOTO, receive_image))
    application.add_handler(MessageHandler(filters.Document.IMAGE, receive_document))
    return application
