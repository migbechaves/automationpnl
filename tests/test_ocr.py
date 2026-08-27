from datetime import datetime

import pytest

from app.models import RecordType
from app.ocr import WatermarkError, _extract_scan_title, build_filename, extract_timestamp


def test_extracts_american_timestamp():
    assert extract_timestamp("Camera watermark: 08/26/2026 08:15:32 AM") == datetime(2026, 8, 26, 8, 15, 32)


def test_extracts_iso_timestamp():
    assert extract_timestamp("2026-08-26 17:05:21") == datetime(2026, 8, 26, 17, 5, 21)


def test_extracts_noisy_ocr_timestamp():
    assert extract_timestamp("08-26-2O26 O8;15;32 a.m.") == datetime(2026, 8, 26, 8, 15, 32)


def test_extracts_noisy_ocr_pm_timestamp():
    assert extract_timestamp("2026/08/26 5:05 p . m .") == datetime(2026, 8, 26, 17, 5, 0)


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


def test_rejects_invalid_timestamp():
    with pytest.raises(WatermarkError):
        extract_timestamp("99/99/9999 99:99:99")


def test_builds_standard_filename():
    timestamp = datetime(2026, 8, 26, 17, 5, 21)
    assert build_filename(timestamp, RecordType.TIME_OUT, "Employee01") == "Employee01_2026-08-26_170521_TIME-OUT.jpg"
