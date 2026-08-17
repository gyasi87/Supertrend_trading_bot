"""
Review-queue web UI: the "last mile" that turns raw extraction into a
books-ready, bookkeeper-approved batch. This is real, running FastAPI --
not a mockup -- serving the actual review_queue.json produced by pipeline.py.
"""
import json
from pathlib import Path

from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from categorize import learn
from qbo_export import export_approved

BASE = Path(__file__).parent
QUEUE_PATH = BASE / "review_queue.json"

app = FastAPI(title="ReceiptFlow Review Queue")
app.mount("/images", StaticFiles(directory=BASE / "sample_data"), name="images")
templates = Jinja2Templates(directory=BASE / "templates")


def load_queue():
    return json.loads(QUEUE_PATH.read_text()) if QUEUE_PATH.exists() else []


def save_queue(items):
    QUEUE_PATH.write_text(json.dumps(items, indent=2))


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    items = load_queue()
    needs_review = [i for i in items if i["status"] == "needs_review"]
    approved = [i for i in items if i["status"] in ("approved", "auto_approved")]
    return templates.TemplateResponse(request, "dashboard.html", {
        "needs_review": needs_review,
        "approved": approved,
        "total": len(items),
    })


@app.post("/accept/{item_id}")
def accept(item_id: str, vendor: str = Form(...), date: str = Form(...),
           total: float = Form(...), category: str = Form(...)):
    items = load_queue()
    for item in items:
        if item["id"] == item_id:
            item["vendor"], item["date"], item["total"], item["category"] = vendor, date, total, category
            item["status"] = "approved"
            learn(vendor, category)  # firm-specific rule engine learns from this correction
    save_queue(items)
    return RedirectResponse("/", status_code=303)


@app.post("/export")
def export():
    n = export_approved()
    return RedirectResponse("/", status_code=303)


@app.get("/health")
def health():
    return {"status": "ok"}
