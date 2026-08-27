import tempfile
from pathlib import Path

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from .models import RecordType
from .ocr import WatermarkError
from .export import export_csv


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Use /timein or /timeout, then upload the watermarked image.")


async def choose_type(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data["record_type"] = RecordType.TIME_IN if update.message.text == "/timein" else RecordType.TIME_OUT
    await update.message.reply_text(f"Please upload your {context.user_data['record_type']} image.")


async def receive_image(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    record_type = context.user_data.get("record_type")
    if not record_type:
        await update.message.reply_text("Start with /timein or /timeout.")
        return
    service = context.application.bot_data["service"]
    settings = context.application.bot_data["settings"]
    employee = str(update.effective_user.username or update.effective_user.id)
    with tempfile.TemporaryDirectory() as temp_dir:
        image_path = Path(temp_dir) / f"telegram-{update.message.id}.jpg"
        telegram_file = await (await update.message.photo[-1].get_file()).download_to_drive(image_path)
        try:
            record = service.process(image_path, record_type, employee, str(update.effective_user.id), str(update.message.id))
        except WatermarkError as error:
            await update.message.reply_text(f"Unable to record image: {error}")
            return
        except ValueError as error:
            await update.message.reply_text(f"Record rejected: {error}")
            return
        except RuntimeError as error:
            await update.message.reply_text(f"Processing error: {error}")
            return
    context.user_data.pop("record_type", None)
    await update.message.reply_text(f"{record.record_type} RECORDED\nDate: {record.date}\nTime: {record.time}\nImage: {record.image_url}")


async def export_records(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    output_path = export_csv(context.application.bot_data["service"].repository, Path("exports"))
    await update.message.reply_document(output_path.open("rb"), filename=output_path.name)


def create_application(settings, service) -> Application:
    if not settings.telegram_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured.")
    application = Application.builder().token(settings.telegram_token).build()
    application.bot_data.update(service=service, settings=settings)
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler(["timein", "timeout"], choose_type))
    application.add_handler(CommandHandler("export", export_records))
    application.add_handler(MessageHandler(filters.PHOTO, receive_image))
    return application
