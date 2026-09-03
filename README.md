# Attendance Image Recorder

A Telegram bot that records employee `TIME-IN` and `TIME-OUT` from the date/time stamped onto an uploaded photo. The photo's own timestamp is always the official record — when the phone or app sent the photo is never used.

## How It Works (Step by Step)

**Phase 1 — Employee starts a record**
The employee sends `/timein` or `/timeout` to the bot, then uploads their photo. They can optionally add a caption with extra details (name, date, time) — useful as a backup if the photo itself is hard to read.

**Phase 2 — Bot figures out who the record is for**
- If the caption includes a line like `Name: Jan Aubrey Azures`, the bot uses that.
- The name is checked against the company's employee list (`employees.txt`) and auto-corrected if it's a close match with a typo (e.g. `Sir Jan Audrey` → `Jan Aubrey Azures`). A name that doesn't match anyone closely is left as typed, rather than guessed.
- If there's no name in the caption, the bot falls back to the sender's Telegram username, then their Telegram ID.

**Phase 3 — Bot reads the date and time off the photo**
This is the core of the system. The bot doesn't just try once — it tries the photo many different ways (different crops of the image, different image adjustments, different reading settings) until it's confident it has the right answer, and it stops as soon as it's confident rather than over-processing an easy photo. Each result is scored:
- **High confidence** — several attempts agree on the same date and time.
- **Medium confidence** — a date and time were found, but only once or with some disagreement between attempts.
- **Uncertain** — nothing readable was found on the photo at all.

**Phase 4 — Backup: read the caption text**
If the photo truly can't be read, the bot looks at the caption for a labeled date/time (e.g. `Date: August 17, 2026` / `Time in: 11:19H`) and uses that instead. Only if both the photo and the caption have nothing usable does the bot reject the upload and tell the employee why.

**Phase 5 — Duplicate check**
Before saving anything, the bot checks whether this exact date, time, type, and employee has already been recorded. If so, it's rejected — this prevents the same clock-in from being logged twice.

**Phase 6 — Save the photos**
Every photo of the submission (a single photo, or a whole album sent at once) is uploaded to Google Drive into one folder per submission, nested by type, year, month, and date (e.g. `TIMEIN / 2026 / 08-August / 2026-08-28 / 2026-08-28_081532_<msg id>`).

**Phase 7 — Log the record**
Once the photos are safely in Drive, a row is added to Google Sheets for each named employee with the record ID, date, time, type, employee, a link to the **submission folder** (so an album's photos are all one click away), and the Telegram message details. Rows are only written after the upload succeeds — never before.

**Phase 8 — Confirm back to the employee**
The bot replies once per recorded employee with the date, time, name, and the folder link, and notes anyone whose row already existed. If a temporary network hiccup blocks a confirmation message, the bot retries a few times before giving up — the record itself is already saved by this point, only the notification is at risk.

## Two Modes

| Mode | What it does | When to use it |
|---|---|---|
| **Dry-run** | Saves photos locally and shows the OCR result. Does not touch Google Drive or Sheets. Includes a `/summary` command that reports how often photos are being read successfully. | Testing and tuning before Google is fully set up, or when checking whether a batch of real photos reads reliably. |
| **Production** | The real thing — uploads to Drive, logs to Sheets. | Day-to-day use once setup is complete. |

Switch between them with `APP_MODE` in `.env`.

## Setup

1. Use the included Python 3.12 virtual environment.
2. Install dependencies:

   ```powershell
   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
   ```

3. Install the Tesseract OCR program (this is what actually reads the text off each photo). If it's not automatically found, point to it in `.env`:

   ```text
   TESSERACT_CMD=C:\Program Files\Tesseract-OCR\tesseract.exe
   ```

4. Create a Telegram bot through BotFather and copy its token.
5. Set up Google access:
   - Create a Google Cloud project, enable the Google Sheets API and Google Drive API.
   - Create a service account, download its key as `service-account.json`, and share your target spreadsheet with that service account's email.
   - Create a Desktop OAuth client, download it as `oauth_credentials.json`, and publish the consent screen so login doesn't expire weekly.
6. Copy `.env.example` to `.env` and fill in `TELEGRAM_BOT_TOKEN` and `GOOGLE_SHEET_ID`. Leave `GOOGLE_DRIVE_ROOT_FOLDER_ID` blank — the bot creates and owns its own Drive folder automatically the first time it runs.
7. Run the tests to confirm everything is wired up correctly:

   ```powershell
   .\.venv\Scripts\python.exe -m pytest
   ```

8. Start the bot:

   ```powershell
   .\.venv\Scripts\python.exe main.py
   ```

   The first time production mode runs, a browser window opens asking you to sign in with the Google account that should own the Drive folder. After that, it logs in automatically.

## Running Unattended (auto-restart)

For a machine that should keep the bot up on its own:

- **`run-bot.bat`** — runs `main.py` and relaunches it 10s after any exit (crash, reboot, power loss). Logs to `bot-restart.log`. Use this instead of `python main.py`.
- **`install-autostart.ps1`** — run once (`powershell -ExecutionPolicy Bypass -File install-autostart.ps1`, no admin needed) to drop a launcher in your Startup folder, so `run-bot.bat` also starts at every logon. Remove it by deleting `"%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\PNL Attendance Bot.cmd"`.

Do the Google sign-in once with `python main.py` before switching to unattended mode — the OAuth browser prompt needs a logged-in desktop session. For sites that reboot without auto-logon, enable Windows auto-logon so the logon task fires.

## Testing Photo Readability Before Going Live

Before fully setting up Google, or any time you want to check how reliably real photos are being read:

1. Set `APP_MODE=dryrun` in `.env` and start the bot.
2. In Telegram, send `/timein` or `/timeout` and upload a photo — the bot saves it locally and immediately tells you whether it could read the date/time, and how confident it was.
3. Send `/summary` at any time to see the overall success rate across everything tested so far.
4. Photos are saved into `samples/timein` and `samples/timeout` for later review.

If a particular photo won't read no matter what, turn on detailed logging by setting `OCR_DEBUG=true` in `.env` and trying that photo again — every attempt the bot made gets written to `ocr_debug.log`, which is the fastest way to diagnose exactly why a specific photo is failing.

## Employee Name List

`employees.txt` holds the correct spelling of every employee's name, one per line, in "First Middle Last" format. This is what Phase 2 checks captions against. To add or fix an employee, just edit this file and restart the bot — no code changes needed.

## Notes

- Never commit `.env`, `service-account.json`, `oauth_credentials.json`, or `token.json` — these are already excluded from version control.
- Photo quality matters: a blurry, tiny, or heavily compressed watermark is harder to read no matter how many ways the bot tries it.
- A CSV export of all records is available via the `/export` command in production mode.

## Improvements Needed

- **Medium-confidence records aren't flagged for a human to double-check.** Right now a medium-confidence read is recorded exactly like a high-confidence one. Tagging it in the Sheet's Status column (e.g. "Needs  someone spot-cReview") would letheck borderline reads without slowing down normal use.
- **No hot-reload for the employee list.** Changes to `employees.txt` require a bot restart to take effect.
- **No measured, ongoing success-rate baseline.** The dry-run `/summary` command is built for exactly this, but it hasn't been run yet against a real, representative batch of company photos to know the current real-world success rate.
- **`.env.example` should be double-checked before sharing or committing** — it's meant to hold only placeholder values, not a real spreadsheet ID.