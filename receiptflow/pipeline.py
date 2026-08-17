"""Orchestrates OCR -> extraction -> categorization -> review queue."""
import json
import time
from pathlib import Path

from ocr import ocr_image
from extract import extract_fields
from categorize import categorize

SAMPLE_DIR = Path(__file__).parent / "sample_data"
QUEUE_PATH = Path(__file__).parent / "review_queue.json"


def process_batch(image_dir: Path = SAMPLE_DIR) -> dict:
    images = sorted(image_dir.glob("receipt_*.png"))
    items = []
    timings = []
    for img_path in images:
        t0 = time.perf_counter()
        text = ocr_image(img_path)
        fields = extract_fields(text)
        category, cat_conf = categorize(fields.vendor or "")
        elapsed = time.perf_counter() - t0
        timings.append(elapsed)
        items.append({
            "id": img_path.stem,
            "image": img_path.name,
            "ocr_text": text,
            "vendor": fields.vendor,
            "date": fields.date,
            "total": fields.total,
            "extract_confidence": fields.confidence,
            "extract_method": fields.method,
            "category": category,
            "category_confidence": cat_conf,
            "status": "needs_review" if (fields.confidence < 0.7 or cat_conf < 0.9) else "auto_approved",
            "elapsed_sec": round(elapsed, 3),
        })

    QUEUE_PATH.write_text(json.dumps(items, indent=2))
    return {
        "count": len(items),
        "total_time_sec": round(sum(timings), 3),
        "avg_time_sec": round(sum(timings) / len(timings), 3) if timings else 0,
        "needs_review": sum(1 for i in items if i["status"] == "needs_review"),
        "auto_approved": sum(1 for i in items if i["status"] == "auto_approved"),
    }


if __name__ == "__main__":
    summary = process_batch()
    print(json.dumps(summary, indent=2))
