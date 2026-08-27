import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings
from app.models import RecordType
from app.scan import image_files, scan_images


def summarize(results):
    total = len(results)
    ok = sum(1 for item in results if item.ok)
    rate = (ok / total * 100) if total else 0.0
    return ok, total, rate


def print_results(results):
    for item in results:
        if item.ok:
            print(f"[OK] {item.record_type} | {item.image_path.name} | {item.timestamp:%Y-%m-%d %H:%M:%S}")
        else:
            print(f"[FAIL] {item.record_type} | {item.image_path.name} | {item.error}")


def main() -> int:
    settings = Settings.from_env()
    timein_paths = image_files(Path("samples/timein"))
    timeout_paths = image_files(Path("samples/timeout"))
    if not timein_paths and not timeout_paths:
        print("No sample images found.")
        print("Add images to samples/timein and samples/timeout, then run again.")
        return 1

    timein_results = scan_images(timein_paths, RecordType.TIME_IN, settings.tesseract_cmd)
    timeout_results = scan_images(timeout_paths, RecordType.TIME_OUT, settings.tesseract_cmd)

    print("\\nTIME-IN results")
    print_results(timein_results)
    ok_in, total_in, rate_in = summarize(timein_results)
    print(f"TIME-IN success: {ok_in}/{total_in} ({rate_in:.2f}%)")

    print("\\nTIME-OUT results")
    print_results(timeout_results)
    ok_out, total_out, rate_out = summarize(timeout_results)
    print(f"TIME-OUT success: {ok_out}/{total_out} ({rate_out:.2f}%)")

    all_results = [*timein_results, *timeout_results]
    ok_all, total_all, rate_all = summarize(all_results)
    print("\\nOVERALL")
    print(f"Success: {ok_all}/{total_all} ({rate_all:.2f}%)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
