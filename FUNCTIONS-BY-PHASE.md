# Functions By Phase

Every function the project actually runs, in execution order. Mirrors the phases in [README.md](README.md#L5). Two runtime modes share one OCR pipeline.

---

## Startup (both modes)

Entry point: [main.py:11](main.py#L11)

| Step | Function | File |
|---|---|---|
| Set 90s default socket timeout | `socket.setdefaulttimeout` | [main.py:17](main.py#L17) |
| Load `.env` into a frozen config | `Settings.from_env` | [app/config.py:27](app/config.py#L27) |
| Optional per-attempt OCR debug logging | `logging.basicConfig` | [main.py:24](main.py#L24) |
| Build the bot (`APP_MODE=production`) | `create_application` | [app/bot.py:165](app/bot.py#L165) |
| Build the bot (`APP_MODE=dryrun`) | `create_dryrun_application` | [app/dryrun_bot.py:165](app/dryrun_bot.py#L165) |
| Shared httpx timeout / concurrency builder | `application_builder` | [app/bot.py:151](app/bot.py#L151) |
| Wire OCR + Drive + Sheets together (production only) | `configured_service` | [app/service.py:72](app/service.py#L72) |
| Google Drive OAuth login / token refresh | `DriveStorage.__init__` → `DriveStorage._folder` | [app/drive.py:12](app/drive.py#L12) |
| Google Sheets auth + header row check | `SheetsRepository.__init__` | [app/sheets.py:11](app/sheets.py#L11) |
| Start long-polling Telegram | `Application.run_polling` | [main.py:35](main.py#L35) |

Alternate dry-run-only entry point: [dryrun_telegram.py:7](dryrun_telegram.py#L7).

---

## Production mode

Handlers registered in [app/bot.py:170-174](app/bot.py#L170-L174): `start`, `choose_type`, `export_records`, `receive_image`, `on_error`.

### Phase 1 — Employee starts a record
| Function | File | Does |
|---|---|---|
| `start` | [app/bot.py:58](app/bot.py#L58) | Replies with usage on `/start` |
| `choose_type` | [app/bot.py:62](app/bot.py#L62) | Stores `TIME_IN`/`TIME_OUT` from `/timein` or `/timeout` |

### Phase 2 — Who the record is for
All inside `receive_image` ([app/bot.py:67](app/bot.py#L67)):

| Function | File | Does |
|---|---|---|
| `extract_employee_from_caption` | [app/ocr.py:233](app/ocr.py#L233) | Pulls `Name:` line from the caption (`CAPTION_NAME_PATTERN`) |
| `load_roster` | [app/roster.py:19](app/roster.py#L19) | Loads `employees.txt` (cached per path) |
| `correct_employee_name` | [app/roster.py:40](app/roster.py#L40) | Fuzzy-matches caption name to roster spelling (`difflib`) |
| `_strip_honorifics` | [app/roster.py:12](app/roster.py#L12) | Drops "Sir/Maam/Mr…" before matching |
| _fallback_ | [app/bot.py:84](app/bot.py#L84) | Telegram username → Telegram ID if no caption name |
| `_download_photo_with_retry` | [app/bot.py:18](app/bot.py#L18) | Fetches the photo, 3 tries with backoff |
| `_reply_with_retry` | [app/bot.py:39](app/bot.py#L39) | "⏳ analyzing…" notice, retried |

Then hands off to a worker thread: `asyncio.to_thread(service.process, …)` ([app/bot.py:100](app/bot.py#L100)) → `AttendanceService.process` ([app/service.py:15](app/service.py#L15)).

### Phase 3 — Read date/time off the photo
`AttendanceService.process` calls `self.ocr_reader` → `read_watermark` ([app/ocr.py:304](app/ocr.py#L304)) → `read_watermark_details` ([app/ocr.py:309](app/ocr.py#L309)) → `analyze_watermark` ([app/watermark_ocr.py:210](app/watermark_ocr.py#L210)).

Inside `analyze_watermark` (staged, early-exit on HIGH_CONFIDENCE):

| Function | File | Role |
|---|---|---|
| `_named_regions` | [app/watermark_ocr.py:83](app/watermark_ocr.py#L83) | Ordered crop list (bottom-left first) |
| `_detect_badge_region` | [app/watermark_ocr.py:55](app/watermark_ocr.py#L55) | Colour-based crop around a blue overlay badge |
| `_fast_variants` | [app/watermark_ocr.py:147](app/watermark_ocr.py#L147) | Contrast + one threshold (stage 1) |
| `_extended_variants` | [app/watermark_ocr.py:155](app/watermark_ocr.py#L155) | Multi-threshold, invert, sharpen, denoise, dilate, upscale (stage 2/3) |
| `run_pass` | [app/watermark_ocr.py:225](app/watermark_ocr.py#L225) | One `pytesseract.image_to_string` + parse + record vote |
| `_extract_scan_title` | [app/ocr.py:57](app/ocr.py#L57) | Guesses the SITE name from non-numeric lines |
| `_clean_title_line` | [app/ocr.py:51](app/ocr.py#L51) | Normalises a candidate title line |
| `extract_timestamp` | [app/ocr.py:124](app/ocr.py#L124) | Regex + `datetime.strptime` across many formats |
| `_normalize_ocr_text` | [app/ocr.py:90](app/ocr.py#L90) | Fixes OCR lookalikes (`O→0`, `;→:`, AM/PM spacing) |
| `_confidence_state` | [app/watermark_ocr.py:192](app/watermark_ocr.py#L192) | `Counter` vote → HIGH (≥3 agree) / MEDIUM / UNCERTAIN |
| `_pick_most_likely_title` | [app/ocr.py:83](app/ocr.py#L83) | Most common SITE guess wins |

Returns `(timestamp, site, confidence)`.

### Phase 4 — Caption fallback
Only if no image candidate was found ([app/watermark_ocr.py:296](app/watermark_ocr.py#L296)):

| Function | File | Does |
|---|---|---|
| `extract_timestamp_from_caption` | [app/ocr.py:248](app/ocr.py#L248) | Entry for caption parsing |
| `_extract_labeled_caption_timestamp` | [app/ocr.py:266](app/ocr.py#L266) | Labeled `Date:` / `Time in:` (handles `1119H`, `August17-2026`) |
| `extract_timestamp` | [app/ocr.py:124](app/ocr.py#L124) | Reused for an unlabeled plain timestamp in the caption |

If image + caption both fail → `WatermarkError`, upload rejected.

### Phase 5 — Duplicate check
| Function | File | Does |
|---|---|---|
| `build_filename` | [app/ocr.py:328](app/ocr.py#L328) | `employee_YYYY-MM-DD_HHMMSS_TYPE.jpg` |
| `retry_network` | [app/net.py:16](app/net.py#L16) | Wraps every Google call: 3 tries, exp backoff, `OSError`→retry |
| `already_recorded` (closure) | [app/service.py:29](app/service.py#L29) | Calls `repository.existing` |
| `SheetsRepository.existing` | [app/sheets.py:31](app/sheets.py#L31) | Scans sheet rows for same date/time/type/employee |

Match → `ValueError("This attendance record already exists.")`. The shared `SheetsRepository.lock` is held across phases 5–7 ([app/service.py:38](app/service.py#L38)).

### Phase 6 — Save the photo
| Function | File | Does |
|---|---|---|
| `DriveStorage.upload` | [app/drive.py:69](app/drive.py#L69) | Uploads to Drive, returns `webViewLink` |
| `DriveStorage._folder` | [app/drive.py:52](app/drive.py#L52) | Find-or-create `TYPE / YEAR / MM-Month / YYYY-MM-DD` (cached, `RLock`) |

### Phase 7 — Log the record
| Function | File | Does |
|---|---|---|
| `SheetsRepository.next_id` | [app/sheets.py:40](app/sheets.py#L40) | Zero-padded row count as the record ID |
| `AttendanceRecord` | [app/models.py:13](app/models.py#L13) | Frozen record; `.date` / `.time` properties |
| `SheetsRepository.add` | [app/sheets.py:44](app/sheets.py#L44) | Appends the row (`=HYPERLINK` image cell) |
| `already_done=already_recorded` | [app/service.py:56](app/service.py#L56) | Guards against a double row on a lost response |

Failure handling: Sheets error after a successful upload → `RuntimeError`; any `OSError` after retries → `RuntimeError` with a "nothing recorded" message ([app/service.py:60-68](app/service.py#L60-L68)).

### Phase 8 — Confirm back
| Function | File | Does |
|---|---|---|
| `context.user_data.pop("record_type")` | [app/bot.py:120](app/bot.py#L120) | Clears the pending type |
| `_reply_with_retry` | [app/bot.py:39](app/bot.py#L39) | Sends the `RECORDED` summary, retried (record is already safe) |

### On demand
| Function | File | Does |
|---|---|---|
| `export_records` | [app/bot.py:131](app/bot.py#L131) | `/export` handler |
| `export_csv` | [app/export.py:5](app/export.py#L5) | Writes `exports/attendance-export.csv` |
| `SheetsRepository.rows` | [app/sheets.py:52](app/sheets.py#L52) | All records as dicts |
| `on_error` | [app/bot.py:136](app/bot.py#L136) | Catch-all: logs + tells the sender something broke |

---

## Dry-run mode

Handlers in [app/dryrun_bot.py:173-179](app/dryrun_bot.py#L173-L179): `start`, `choose_type`, `where`, `summary`, `receive_image`, `receive_document`, `on_error` (shared with production).

| Function | File | Does |
|---|---|---|
| `start` | [app/dryrun_bot.py:70](app/dryrun_bot.py#L70) | Usage text |
| `choose_type` | [app/dryrun_bot.py:78](app/dryrun_bot.py#L78) | Stores `TIME_IN`/`TIME_OUT` |
| `where` | [app/dryrun_bot.py:83](app/dryrun_bot.py#L83) | Prints the local storage folder |
| `receive_image` | [app/dryrun_bot.py:113](app/dryrun_bot.py#L113) | Photo → save locally → scan |
| `receive_document` | [app/dryrun_bot.py:134](app/dryrun_bot.py#L134) | Image-file document → save locally → scan |
| `_target_folder` | [app/dryrun_bot.py:36](app/dryrun_bot.py#L36) | `samples/timein` or `samples/timeout` |
| `_download_with_retry` | [app/dryrun_bot.py:18](app/dryrun_bot.py#L18) | Fetch with backoff |
| `_scan_and_reply` | [app/dryrun_bot.py:42](app/dryrun_bot.py#L42) | Runs `read_watermark_details` (same Phase 3 pipeline), replies with confidence + SITE |
| `summary` | [app/dryrun_bot.py:87](app/dryrun_bot.py#L87) | `/summary` success-rate report |
| `image_files` | [app/scan.py:34](app/scan.py#L34) | Lists `.jpg/.jpeg/.png` in a folder |
| `scan_images` | [app/scan.py:18](app/scan.py#L18) | Batch OCR → `ScanResult` list |

No Drive, no Sheets, no duplicate check, no record row.

---

## CLI batch tool (not the bot)

[tools/ocr_dry_run.py](tools/ocr_dry_run.py) — run OCR over `samples/` from the command line.

| Function | File | Does |
|---|---|---|
| `main` | [tools/ocr_dry_run.py:26](tools/ocr_dry_run.py#L26) | Scans both sample folders, prints per-image + overall rates |
| `summarize` | [tools/ocr_dry_run.py:11](tools/ocr_dry_run.py#L11) | `(ok, total, rate)` |
| `print_results` | [tools/ocr_dry_run.py:18](tools/ocr_dry_run.py#L18) | `[OK]` / `[FAIL]` lines |
| `image_files`, `scan_images` | [app/scan.py](app/scan.py) | Shared with dry-run mode |
