"""
Export accepted queue items to a QuickBooks-Online-importable 3-column
CSV (Date, Description, Amount) with a Category column that QBO's
standard CSV bank-transaction importer maps to chart-of-accounts on
import. This is the real integration surface a bookkeeping firm uses
today without needing OAuth app-store approval -- QBO's CSV import is a
standard, supported path.
"""
import csv
import json
from pathlib import Path

QUEUE_PATH = Path(__file__).parent / "review_queue.json"
EXPORT_PATH = Path(__file__).parent / "qbo_export.csv"


def export_approved() -> int:
    items = json.loads(QUEUE_PATH.read_text()) if QUEUE_PATH.exists() else []
    approved = [i for i in items if i["status"] in ("approved", "auto_approved")]
    with open(EXPORT_PATH, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Date", "Description", "Amount", "Category"])
        for item in approved:
            writer.writerow([
                item.get("date") or "",
                item.get("vendor") or "Unknown Vendor",
                f"{item.get('total') or 0:.2f}",
                item.get("category") or "Uncategorized",
            ])
    return len(approved)


if __name__ == "__main__":
    n = export_approved()
    print(f"Exported {n} approved receipts to {EXPORT_PATH}")
