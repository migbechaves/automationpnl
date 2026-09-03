from datetime import datetime

import pytest

from app.ocr import (
    WatermarkError,
    _extract_scan_title,
    _normalize_ocr_text,
    extract_employee_from_caption,
    extract_employees_from_caption,
    extract_timestamp,
    extract_timestamp_from_caption,
)


def test_extracts_american_timestamp():
    assert extract_timestamp("Camera watermark: 08/26/2026 08:15:32 AM") == datetime(2026, 8, 26, 8, 15, 32)


def test_extracts_iso_timestamp():
    assert extract_timestamp("2026-08-26 17:05:21") == datetime(2026, 8, 26, 17, 5, 21)


def test_extracts_noisy_ocr_timestamp():
    assert extract_timestamp("08-26-2O26 O8;15;32 a.m.") == datetime(2026, 8, 26, 8, 15, 32)


def test_extracts_noisy_ocr_pm_timestamp():
    assert extract_timestamp("2026/08/26 5:05 p . m .") == datetime(2026, 8, 26, 17, 5, 0)


def test_normalize_corrects_s_and_b_digit_lookalikes():
    assert extract_timestamp("08/26/2026 0B:1S:32 AM") == datetime(2026, 8, 26, 8, 15, 32)


def test_normalize_does_not_corrupt_ordinal_suffixes():
    # "S"/"B" corrections are uppercase-only for exactly this reason: a lowercase
    # version would turn "1st"/"2nd" into "15t"/mangled text.
    assert _normalize_ocr_text("1st 2nd 3rd") == "1st 2nd 3rd"


def test_extracts_gps_map_camera_day_first_timestamp():
    text = "Friday, 26/06/2026 09:48 AM GMT+08:00\nNote : Captured by GPS Map Camera"
    assert extract_timestamp(text) == datetime(2026, 6, 26, 9, 48, 0)


def test_extracts_timemark_weekday_comma_format():
    text = "11:19\nMon, Aug 17, 2026\n62 A. Bonifacio St., Makati City, 1208\nMetro Manila\nVerified time by Timemark Camera"
    assert extract_timestamp(text) == datetime(2026, 8, 17, 11, 19, 0)


def test_extracts_security_patrol_badge_format():
    # "Security Patrol Work Record" style overlay: time on its own line, AM/PM and
    # weekday on the next line (meridiem before weekday), then the full date.
    text = (
        "Security Patrol\nWork Record\n10:47\nAM Fri\nAug 28, 2026\n"
        "62 A. Bonifacio St., Makati City, 1208 Metro Manila\nPhoto by\nTimemark"
    )
    assert extract_timestamp(text) == datetime(2026, 8, 28, 10, 47, 0)


def test_normalize_does_not_corrupt_month_and_weekday_names():
    # "Mon" and "Oct" contain letters that look like digits (o, O) -- they must
    # not be mangled by the digit-lookalike cleanup unless the token also has a
    # genuine digit in it.
    assert extract_timestamp("Thu, Oct 15, 2026 09:05 AM") == datetime(2026, 10, 15, 9, 5, 0)


def test_extracts_time_first_month_name_format():
    assert extract_timestamp("07:13 AM Thurs Aug 13, 2026") == datetime(2026, 8, 13, 7, 13, 0)


def test_extracts_time_first_full_month_format():
    assert extract_timestamp("07:13 AM Thursday August 13, 2026") == datetime(2026, 8, 13, 7, 13, 0)


def test_extracts_site_title_from_overlay_text():
    text = "Security Patrol\nVista Marine\n07:13 AM Thurs Aug 13, 2026\nAddress: Navotas"
    assert _extract_scan_title(text) == "Vista Marine"


def test_extracts_site_title_with_joined_words():
    text = "SecurityPatrol\nVistaMarine\n07:13 AMThurs\nAug13,2026"
    assert _extract_scan_title(text) == "Vista Marine"


def test_extracts_bottom_right_stamp_with_24h_time_and_stray_pm():
    # Big white phone-camera stamp: "SEP 04, 2026" over "14:04 PM" -- 24-hour
    # clock with a bogus "PM" that used to break every strptime format.
    assert extract_timestamp("SEP 04, 2026\n14:04 PM") == datetime(2026, 9, 4, 14, 4, 0)


def test_extracts_gps_map_camera_day_before_month_name():
    text = (
        "VISTA MARINE\nTime: Fri,3 Jul 2026 04:44PM\nWeather: Sunny Day 36C\n"
        "Lat/Long: Lat 14.643492 Long 120.948333\nADDRESS: Navotas Fish Port, Pier 5"
    )
    assert extract_timestamp(text) == datetime(2026, 7, 3, 16, 44, 0)


def test_rejects_invalid_timestamp():
    with pytest.raises(WatermarkError):
        extract_timestamp("99/99/9999 99:99:99")


def test_location_word_is_not_read_as_a_date():
    # "Makati 26 2026" / "Navotas 8 2026" must not be taken as "<Month> <day> <year>".
    with pytest.raises(WatermarkError):
        extract_timestamp("Inn Makati 26 2026 Navotas")


def test_real_numeric_date_wins_over_a_location_line():
    # "Makati 26 2026" used to match the month-name date slot and short-circuit
    # the real numeric watermark ("2026-08-26") on the next line -> whole read
    # failed. Now only real month names take that slot.
    text = "Makati 26 2026\n16:02\n2026-08-26"
    assert extract_timestamp(text) == datetime(2026, 8, 26, 16, 2)


def test_bare_name_list_drops_a_location_only_line():
    text = "Inn vistamarine\nNestor bagayawa"
    assert extract_employees_from_caption(text) == [("Nestor bagayawa", None)]


def test_extracts_dash_separated_date_with_labeled_time_on_next_line():
    # Regression: a dash-separated numeric date (e.g. ISO "2026-08-17") also
    # satisfies the bare time pattern's separator class ("-"), which used to steal
    # a false "08:17" match out of the date itself instead of finding the real
    # time later in the text.
    assert extract_timestamp("Date: 2026-08-17\nTime: 17:45") == datetime(2026, 8, 17, 17, 45)


def test_extracts_timestamp_from_duty_report_caption():
    text = "Date: August17-2026\nName: Sir Jan Audrey\nTime in:1119H\nDestination:pnl office\nPURPOSE: on duty"
    assert extract_timestamp_from_caption(text) == datetime(2026, 8, 17, 11, 19)


def test_extracts_timestamp_from_caption_with_ampm_time_out():
    text = "Date: 08/17/2026\nTime out: 5:45 PM"
    assert extract_timestamp_from_caption(text) == datetime(2026, 8, 17, 17, 45)


def test_extracts_employee_name_from_duty_report_caption():
    text = "Date: August17-2026\nName: Sir Jan Audrey\nTime in:1119H\nDestination:pnl office\nPURPOSE: on duty"
    assert extract_employee_from_caption(text) == "Sir Jan Audrey"


def test_extract_employee_from_caption_returns_none_when_no_name_field():
    assert extract_employee_from_caption("Date: 08/17/2026\nTime in:1119H") is None
    assert extract_employee_from_caption(None) is None
    assert extract_employee_from_caption("") is None


def test_extract_employees_from_caption_returns_every_name_line():
    text = "Name: Miguel Migel\nName: Migel Miguel\nName: Meguel Meguel\nDate: Aug 18, 2026\nTime in: 05:15"
    assert extract_employees_from_caption(text) == [
        ("Miguel Migel", None), ("Migel Miguel", None), ("Meguel Meguel", None),
    ]


def test_extract_employees_from_caption_single_and_empty():
    assert extract_employees_from_caption("Name: Sir Jan Audrey\nTime in:1119H") == [("Sir Jan Audrey", None)]
    assert extract_employees_from_caption("Date: 08/17/2026") == []
    assert extract_employees_from_caption(None) == []


def test_extract_employees_from_caption_worker_cadet_sections():
    text = "Worker\nR.flor\nR.bla\nE.tala\nCadet\nboy gerl\nKent sanla\nAlexzander martin lut"
    assert extract_employees_from_caption(text) == [
        ("R.flor", "Worker"), ("R.bla", "Worker"), ("E.tala", "Worker"),
        ("boy gerl", "Cadet"), ("Kent sanla", "Cadet"), ("Alexzander martin lut", "Cadet"),
    ]


def test_worker_cadet_sections_skip_report_fields_and_plural_colon_headers():
    text = "Date: Aug 18, 2026\nWorkers:\n- R.flor\nTime in: 05:15\nCadets:\nboy gerl"
    assert extract_employees_from_caption(text) == [("R.flor", "Worker"), ("boy gerl", "Cadet")]


def test_extract_employees_from_bare_vertical_name_list():
    text = "A. Malatangay\nG. Labida\nMiguel Bechaves"
    assert extract_employees_from_caption(text) == [
        ("A. Malatangay", None), ("G. Labida", None), ("Miguel Bechaves", None),
    ]


def test_bare_name_list_skips_date_time_and_keyword_lines():
    text = "TIME OUT\nDate: 08-27-2026\nTime: 17:05\nName\nA. Malatangay\nG. Labida"
    assert extract_employees_from_caption(text) == [("A. Malatangay", None), ("G. Labida", None)]


def test_worker_cadet_headers_with_name_lines_keep_category():
    text = "Worker\nName: R.flor\nName: R.bla\nCadet\nName: boy gerl\nName: Kent sanla"
    assert extract_employees_from_caption(text) == [
        ("R.flor", "Worker"), ("R.bla", "Worker"),
        ("boy gerl", "Cadet"), ("Kent sanla", "Cadet"),
    ]


def test_caption_extraction_rejects_text_without_date_or_time():
    with pytest.raises(WatermarkError):
        extract_timestamp_from_caption("Name: Sir Jan Audrey\nPURPOSE: on duty")


def test_caption_extraction_falls_back_to_plain_timestamp_formats():
    # No "Date:"/"Time:" labels at all -- should fall back to the same
    # general-purpose parser used for watermark text.
    assert extract_timestamp_from_caption("08/26/2026 08:15:32 AM") == datetime(2026, 8, 26, 8, 15, 32)
    assert extract_timestamp_from_caption("Date: 2026-08-17\nTime: 17:45") == datetime(2026, 8, 17, 17, 45)
