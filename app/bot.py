import asyncio
import logging
import tempfile
from pathlib import Path

from telegram import Message, Update
from telegram.error import NetworkError, RetryAfter, TelegramError
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from .models import RecordType
from .ocr import WatermarkError, extract_employees_from_caption
from .export import export_csv
from .roster import correct_employee_name, load_roster

logger = logging.getLogger(__name__)

# Telegram sends each photo of an album as a separate update with no "album
# complete" signal, so we collect them per media_group_id and process the whole
# album once it has been quiet for this long. Bump it on a slow link if album
# photos start arriving as separate one-photo submissions.
_ALBUM_SETTLE_SECONDS = 2.0
_pending_albums: dict[str, dict] = {}


async def _download_photo_with_retry(message: Message, image_path: Path, attempts: int = 3, delay: float = 2.0) -> None:
    """Fetch a message's photo file from Telegram, retrying a few times on a
    transient network error (the httpx.ReadError / NetworkError this was added
    for) before giving up. Nothing has been recorded at this point, so a final
    failure just means asking the sender to resend.
    """
    last_error: TelegramError | None = None
    wait = delay
    for attempt in range(attempts):
        try:
            telegram_file = await message.photo[-1].get_file()
            await telegram_file.download_to_drive(image_path)
            return
        except TelegramError as error:
            last_error = error
            if attempt < attempts - 1:
                await asyncio.sleep(wait)
                wait *= 2
    raise last_error


async def _reply_with_retry(message: Message, text: str, attempts: int = 12, delay: float = 3.0) -> None:
    """Send a reply, retrying on a transient Telegram/network error (timeouts,
    DNS blips, httpx.ReadError) or a group flood limit before giving up. Used for
    outcome messages -- by the time these are sent OCR + Drive + Sheets have
    already finished, so the record is safe and only the notification is at risk.
    That means we can keep trying for several minutes through a longer outage --
    the real case that left "analyzing..." on screen with the row already in the
    Sheet. Callers that only want a best-effort ack (the "analyzing..." line) pass
    a small ``attempts`` so a dead link can't stall the actual work behind it.
    """
    last_error: TelegramError | None = None
    wait = delay
    for attempt in range(attempts):
        try:
            await message.reply_text(text)
            return
        except RetryAfter as error:
            # Telegram flood limit (common in a busy group): it says exactly how
            # long to wait. Honour that -- our own backoff would just retry early
            # and get throttled again, burning attempts for nothing.
            last_error = error
            if attempt < attempts - 1:
                await asyncio.sleep(error.retry_after + 1)
        except TelegramError as error:
            last_error = error
            if attempt < attempts - 1:
                await asyncio.sleep(wait)
                wait = min(wait * 2, 30.0)
    logger.exception("Failed to deliver reply to Telegram after %d attempts: %r", attempts, text[:200], exc_info=last_error)


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
    await update.message.reply_text(
        f"Please upload your {context.user_data['record_type']} image(s). "
        f"Stays active for more uploads until you send /in or /out again."
    )


async def receive_image(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    record_type = context.user_data.get("record_type")
    if not record_type:
        await update.message.reply_text("Start with /in or /out.")
        return
    message = update.message
    if not message.media_group_id:
        await _process_submission([message], record_type, update, context)
        return
    # Album: buffer every photo of this media group, then process the whole album
    # once it goes quiet (see _ALBUM_SETTLE_SECONDS). No await between setdefault
    # and create_task, so concurrent album updates don't race here.
    album = _pending_albums.setdefault(message.media_group_id, {"messages": [], "task": None})
    album["messages"].append(message)
    if album["task"]:
        album["task"].cancel()
    album["task"] = asyncio.create_task(
        _flush_album_when_settled(message.media_group_id, record_type, update, context)
    )


async def _flush_album_when_settled(media_group_id, record_type, update, context) -> None:
    try:
        await asyncio.sleep(_ALBUM_SETTLE_SECONDS)
    except asyncio.CancelledError:
        return  # another photo of the same album arrived; it reschedules the flush
    album = _pending_albums.pop(media_group_id, None)
    if not album or not album["messages"]:
        return
    try:
        await _process_submission(album["messages"], record_type, update, context)
    except Exception:
        # Detached task -- the app-level error handler won't see this, so log it
        # here rather than let asyncio swallow it as "never retrieved".
        logger.exception("Failed to process album %s", media_group_id)


def _roster_corrected_names(text: str | None, roster: tuple[str, ...]) -> list[str]:
    """Every person named in `text` (a caption or a reply to the name prompt),
    each on its own -- "Name:" lines, a bare vertical list, or a single
    "First Last" line -- roster-corrected, with any Worker/Cadet category
    appended as " - Worker" / " - Cadet".
    """
    return [
        f"{correct_employee_name(name, roster)} - {category}" if category
        else correct_employee_name(name, roster)
        for name, category in extract_employees_from_caption(text)
    ]


async def _process_submission(
    messages: list[Message], record_type: RecordType, update: Update, context: ContextTypes.DEFAULT_TYPE,
    employees: list[str] | None = None,
) -> None:
    """Record one time-in/out submission: one photo or a whole album. All photos
    go into a single Drive folder and every named employee's Sheet row links to
    that folder.

    ``employees`` is normally worked out from the caption here; it's passed in
    only when resuming a submission that had no name and the sender was asked for
    one (see receive_name).
    """
    service = context.application.bot_data["service"]
    settings = context.application.bot_data["settings"]
    caption = next((message.caption for message in messages if message.caption), None)
    reply_to = next((message for message in messages if message.caption), messages[0])

    # Who the record(s) are for comes from the caption -- "Name:" lines, a bare
    # vertical list, and/or "Worker"/"Cadet" sections (see
    # extract_employees_from_caption). Each name is roster-corrected (employees.txt)
    # so a typo still lands under the real spelling; a category, when present, is
    # appended as " - Worker" / " - Cadet".
    if employees is None:
        employees = _roster_corrected_names(caption, load_roster(settings.employee_roster_file))
        if not employees:
            # No name anywhere in the caption: hold the submission and ask the
            # sender who is in the photo, in English + Filipino. Nothing reaches
            # the Sheet until they reply (see receive_name).
            context.user_data["pending_name"] = {"messages": messages, "record_type": record_type}
            await _reply_with_retry(
                reply_to, "What is the name of the person in the image? / Sino po ang nasa larawan?"
            )
            return

    # Who physically sent the photo -- readable name plus numeric id (display
    # names aren't unique). Goes in the informational "Telegram ID" sheet column.
    sender = f"{update.effective_user.full_name} ({update.effective_user.id})"
    with tempfile.TemporaryDirectory() as temp_dir:
        image_paths = []
        for message in messages:
            image_path = Path(temp_dir) / f"telegram-{message.id}.jpg"
            try:
                await _download_photo_with_retry(message, image_path)
            except TelegramError as error:
                logger.exception("Could not download photo from Telegram: %r", error)
                await _reply_with_retry(
                    reply_to,
                    "Couldn't download your image(s) from Telegram (network problem). Please send again.",
                )
                return
            image_paths.append(image_path)
        # Best-effort ack only: keep attempts low so a flaky link can't delay the
        # OCR/Drive/Sheets work (awaited right below) by minutes of reply retries.
        await _reply_with_retry(reply_to, "⏳ Please wait, analyzing... don't send anything else yet.", attempts=2)
        try:
            # OCR + Drive + Sheets are blocking; run them off the event loop.
            records, skipped = await asyncio.to_thread(
                service.process, image_paths, record_type, employees, sender,
                str(reply_to.id), caption,
            )
        except WatermarkError as error:
            await _reply_with_retry(reply_to, f"Unable to record image: {error}")
            return
        except ValueError as error:
            await _reply_with_retry(reply_to, f"Record rejected: {error}")
            return
        except RuntimeError as error:
            await _reply_with_retry(reply_to, f"Processing error: {error}")
            return
        except OSError as error:
            # service.process normally converts network failures to RuntimeError;
            # last-resort net so a stray socket error is reported, not crashed on.
            logger.exception("Network error while processing image: %r", error)
            await _reply_with_retry(reply_to, "Network error while recording. Nothing was saved -- please try again.")
            return
    # record_type is left set on purpose: the sender can upload the next photo (or
    # album) straight away without re-typing /in or /out. It changes only when
    # they send the other command; every RECORDED reply names the type so a
    # forgotten switch is visible on sight.
    # OCR + Drive + Sheets already succeeded here -- retry each reply a few times
    # on a transient blip so the sender still gets told what was recorded.
    for employee in skipped:
        await _reply_with_retry(reply_to, f"Skipped {employee}: this attendance record already exists.")
    for record in records:
        await _reply_with_retry(
            reply_to,
            f"{record.record_type} RECORDED\nEmployee: {record.employee}\nDate: {record.date}\nTime: {record.time}\nImage: {record.image_url}",
        )


async def receive_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Catch the sender's reply to the "who is in the photo?" question that
    _process_submission asks when a submission arrives with no name in the
    caption. Roster-corrects the typed name and resumes the held submission.
    """
    pending = context.user_data.get("pending_name")
    if not pending:
        return  # ordinary chatter, not a reply we're waiting on
    text = (update.message.text or "").strip()
    if not text:
        await update.message.reply_text("Please type the person's name. / Pakitype ang pangalan.")
        return
    context.user_data.pop("pending_name", None)
    roster = load_roster(context.application.bot_data["settings"].employee_roster_file)
    # One record per name: the reply may be several names stacked vertically
    # ("Name:\nName:" or a bare list), or a single "First Last". Fall back to one
    # record per non-blank line if the name parser recognises nothing, so a name
    # it doesn't like is still logged rather than dropped.
    employees = _roster_corrected_names(text, roster) or [
        correct_employee_name(line.strip(), roster) for line in text.splitlines() if line.strip()
    ]
    # ponytail: no per-submission timeout -- a stale pending_name is overwritten by
    # the next nameless submission, or consumed by the next text this user sends.
    # Add an expiry if senders start seeing their old photo recorded under a
    # later, unrelated message.
    await _process_submission(
        pending["messages"], pending["record_type"], update, context, employees=employees,
    )


async def export_records(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    output_path = export_csv(context.application.bot_data["service"].repository, Path("exports"))
    await update.message.reply_document(output_path.open("rb"), filename=output_path.name)


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Catch-all so a single failed update is logged with context and (where
    possible) acknowledged to the sender, instead of printing a bare traceback
    and leaving the user staring at "⏳ Please wait...". Registered on every
    application below.
    """
    # A transient network drop during long-polling (httpx.ReadError, timeouts)
    # surfaces here as a NetworkError. python-telegram-bot's own poll loop keeps
    # retrying and recovers on its own, so log one line, not a scary traceback,
    # and don't try to message anyone about it.
    if isinstance(context.error, NetworkError):
        logger.warning("Transient Telegram network error (auto-retrying): %s", context.error)
        return
    logger.exception("Unhandled exception while processing an update", exc_info=context.error)
    message = getattr(update, "effective_message", None)
    if message is not None:
        try:
            await message.reply_text("Something went wrong handling that. Please try again in a moment.")
        except TelegramError:
            pass


def application_builder(settings):
    """Shared ``ApplicationBuilder`` used by both the production and dryrun bots.

    The per-phase httpx timeouts default to 5s, which is too tight on a slow or
    congested link and is what surfaced as httpx.ReadError / connect timeouts.
    Give network calls (and especially media downloads) more room before failing.
    """
    return (
        Application.builder().token(settings.telegram_token).concurrent_updates(8)
        .connect_timeout(20.0).read_timeout(30.0).write_timeout(30.0)
        .pool_timeout(20.0).media_write_timeout(60.0)
    )


def create_application(settings, service) -> Application:
    if not settings.telegram_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured.")
    application = application_builder(settings).build()
    application.bot_data.update(service=service, settings=settings)
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler(["in", "out"], choose_type))
    application.add_handler(CommandHandler("export", export_records))
    application.add_handler(MessageHandler(filters.PHOTO, receive_image))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, receive_name))
    application.add_error_handler(on_error)
    return application
