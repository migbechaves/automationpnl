# Privacy Policy — Attendance Recorder

_Last updated: 2026-09-04_

Attendance Recorder is a private tool used by our organization to log employee
time-in and time-out attendance. It is not offered to the public.

## What it accesses

With your consent, the app uses Google OAuth with a single scope:

- **`https://www.googleapis.com/auth/drive.file`** — lets the app create and
  manage **only the files and folders it creates itself** in the authorizing
  account's Google Drive. It cannot see or touch any other file in your Drive.

Attendance spreadsheet rows are written through a separate Google service
account, not through your personal login.

## What data is stored

- Attendance photos you send to the Telegram bot, uploaded to a folder in Google
  Drive owned by the authorizing account.
- One row per attendance record in a Google Sheet: record ID, date, time,
  type (time-in / time-out), employee name, a link to the photo folder, the
  sender's Telegram display name and numeric user ID, and the Telegram message ID.

## How data is used

Solely to produce the organization's internal attendance record. It is **not**
sold, shared with third parties, or used for advertising or any purpose beyond
attendance tracking.

## Retention

Records and photos remain in the organization's Google Sheet and Drive until an
administrator deletes them.

## Revoking access

Remove the app's Drive access at any time from
<https://myaccount.google.com/permissions>.

## Contact

pnl.itmonitoring@gmail.com
