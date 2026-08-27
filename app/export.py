import csv
from pathlib import Path


def export_csv(repository, output_dir: Path) -> Path:
    rows = repository.rows()
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "attendance-export.csv"
    fieldnames = list(rows[0].keys()) if rows else ["Record ID", "Date", "Time", "Type", "Employee", "Image", "Telegram ID", "Message ID", "Status"]
    with output_path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return output_path
