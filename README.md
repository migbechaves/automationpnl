# Attendance Image Recorder

A Telegram bot that records `TIME-IN` and `TIME-OUT` from the date/time watermark in an uploaded image. The watermark is the official timestamp; Telegram upload time is never used.

## What is implemented

- `/timein` and `/timeout` select the record type.
- OCR reads `MM/DD/YYYY HH:MM[:SS] AM/PM` and ISO-style timestamps.
- Invalid or unreadable watermarks are rejected.
- Duplicate records are rejected using date, time, type, and employee.
- Images are renamed and organized in Google Drive as `year/month/date/type`.
- Records are appended to Google Sheets only after Drive upload succeeds.
- CSV export helper is included for reporting.

## Setup

1. Create or use the included Python 3.12 virtual environment.
2. Install dependencies:

   ```powershell
   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
   ```

3. Install the Tesseract OCR executable for Windows. Set `TESSERACT_CMD` in `.env` if it is not on PATH, for example:

   ```text
   TESSERACT_CMD=C:\Program Files\Tesseract-OCR\tesseract.exe
   ```

4. Create a Telegram bot with BotFather and copy the token.
5. Create a Google Cloud service account, enable the Google Sheets API, download its JSON key as `service-account.json`, and share the target spreadsheet with the service-account email. Enable the Google Drive API, create a Desktop OAuth client, and save its downloaded JSON as `oauth_credentials.json`.
6. Copy `.env.example` to `.env` and fill in `TELEGRAM_BOT_TOKEN`, `GOOGLE_SHEET_ID`, and `GOOGLE_DRIVE_ROOT_FOLDER_ID`.
7. Run the tests:

   ```powershell
   .\.venv\Scripts\python.exe -m pytest
   ```

8. Start the bot in OCR dry-run mode:

   ```powershell
   .\.venv\Scripts\python.exe main.py
   ```

   Dry-run mode saves images locally and returns the OCR result; it does not use Google Drive or Sheets.
   When Google Drive storage is ready, set `APP_MODE=production` in `.env` and restart. On the first production start, a browser opens for Google Drive consent; sign in with the Google account that owns the target Drive folder. The resulting `token.json` is refreshed automatically on later starts.

## OCR Dry Run Before Google Setup

Use this before setup step 5 to test timestamp scanning probability with your real images.

1. Set `TELEGRAM_BOT_TOKEN` and `TESSERACT_CMD` in `.env`.
2. Set `APP_MODE=dryrun` in `.env`, then start the Telegram bot:

   ```powershell
   .\.venv\Scripts\python.exe main.py
   ```

3. In Telegram:

   - send `/timein` then upload a Time-In image
   - send `/timeout` then upload a Time-Out image
   - send `/summary` to get current OCR success rates

4. Images are saved automatically into your project:

   - `samples/timein`
   - `samples/timeout`

5. Optional local summary command:

   ```powershell
   .\.venv\Scripts\python.exe tools\ocr_dry_run.py
   ```

The output shows each file result plus success rates for Time-In, Time-Out, and overall.

The Google Sheet worksheet named by `GOOGLE_WORKSHEET` must already exist. The Drive root folder must already exist; put its ID in `GOOGLE_DRIVE_ROOT_FOLDER_ID`. Production uploads are authorized by your personal Google account through `oauth_credentials.json`, then organized as `TIMEIN/year/month/date` or `TIMEOUT/year/month/date`.

## Operational notes

- Employee identity currently uses the Telegram username, falling back to Telegram user ID. A mapping table can be added later if staff names must be managed separately.
- OCR quality depends on the watermark being visible and Tesseract being installed.
- A Sheets failure after Drive upload is surfaced to the user and leaves the image available for manual reconciliation; a production deployment should add a retry queue.
- Never commit `.env` or `service-account.json`.
