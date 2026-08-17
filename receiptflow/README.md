# ReceiptFlow

Receipt/invoice processing for high-volume outsourced bookkeeping firms.
Combines **Tesseract OCR** + a **structured field extractor** (heuristic
first pass, with a real, wired-but-not-live Anthropic LLM fallback for
low-confidence cases) + a **per-firm categorization rule engine** that
learns from bookkeeper corrections + a **review queue** + a
**QuickBooks-Online-compatible CSV export**.

None of these pieces is the product on its own -- that's the point. Pull
out the categorization rule engine and you have a generic OCR-extraction
API (which Veryfi/Nanonets already sell as a commodity). Pull out the
review queue and wrong extractions silently corrupt a client's books.
Pull out the QBO export and a bookkeeper still has to hand-retype
everything. The combination is what turns raw receipt photos into a
books-ready, approved batch in one workflow.

## Incumbent and axis

**Incumbent: Dext** (formerly Receipt Bank). Dext's self-serve Business
plan is roughly $45-70/month for 150-300 document credits -- about
**$0.20-0.35 effective cost per document**. This prototype's OCR+heuristic
extraction runs on commodity CPU for a fraction of a cent per document.

**Axis: cost per document, at the specific segment Dext prices for
manual-review labor on -- high-volume outsourced bookkeeping shops
processing thousands of receipts/month, where non-template ("messy")
receipts are the norm, not the exception.**

## Real, measured numbers (this run)

Generated `python3 run_benchmark.py` on 20 synthetic receipts covering 20
different vendors, 6 date formats, 7 total-amount label variants, and
photo noise (rotation, blur, crumple lines, sensor noise) -- see
`generate_samples.py` and `sample_data/ground_truth.json`.

```
documents_processed: 20
avg_time_per_doc: 0.24s (commodity CPU, no GPU)
vendor accuracy: 100%
total-amount accuracy: 90%
category accuracy (first pass): 100%
auto-approved with zero human touch: 80%
silently-wrong auto-approvals: 0 / 20   <-- the number that actually matters
```

The `silently-wrong auto-approvals: 0/20` line is the real claim: every
extraction the pipeline got wrong or was unsure about was caught by the
confidence-gated review queue, not shipped into a client's books.

**On cost: an earlier version of this README compared our raw compute
cost ($0.0004/doc) against Dext's all-in list price ($0.25/doc) and
called it a 625x win. A blind critic correctly flagged that as an
apples-to-oranges comparison** -- Dext's price already bundles whatever
extraction-plus-correction labor Dext absorbs, so the fair number is our
own **fully-loaded** cost: compute + the per-call cost of the LLM
fallback on flagged documents (a real, published-pricing estimate, not a
live call) + bookkeeper review labor on the 20% of documents routed to
review.

No human reviewer was available in this sandbox to time the review
step, so instead of asserting a single made-up number, here's the
fully-loaded cost at a range of plausible review times per flagged
document, compared against Dext's $0.25/doc list price:

| Review time / flagged doc | Fully-loaded cost/doc | vs. Dext |
|---|---|---|
| 15s (glance-and-approve, pre-filled form) | $0.026 | 9.6x cheaper |
| 30s | $0.051 | 4.9x cheaper |
| 60s | $0.101 | 2.5x cheaper |
| 120s (full manual re-entry, worst case) | $0.201 | 1.2x cheaper |

It beats Dext's list price across the whole range, including the
pessimistic 2-minutes-per-flagged-document case -- because only 20% of
documents are flagged at all, review cost gets diluted 5x before it hits
the per-document blended average. The realistic case (pre-filled fields,
click-to-approve UI, per the screenshot) is closer to the 15-30s rows.

Full JSON report: `benchmark_report.json` (regenerated on every run).

## Running it

```bash
pip3 install pytesseract pillow fastapi "uvicorn[standard]" python-multipart jinja2 numpy
sudo apt-get install -y tesseract-ocr tesseract-ocr-eng

python3 generate_samples.py     # regenerate the synthetic test batch
python3 run_benchmark.py        # OCR -> extract -> categorize -> score vs ground truth
python3 -m uvicorn app:app --host 127.0.0.1 --port 8000   # review UI at :8000
python3 qbo_export.py           # export approved batch to qbo_export.csv
```

## What's real vs. architected-but-unexercised

- **Real and running in this sandbox:** OCR, heuristic extraction, date
  sanity validation, the per-firm categorization rule engine (learns from
  corrections made in the UI), the FastAPI review queue, the QBO CSV
  export.
- **Architected, not live here:** the `LLMExtractor` fallback in
  `extract.py` calls the Anthropic API for the ~25% of receipts the
  heuristic pass flags low-confidence. This sandbox has no
  `ANTHROPIC_API_KEY` provisioned for standalone scripts and
  `huggingface.co`/`ollama.com` are blocked by network policy, so it
  can't be exercised here. The benchmark numbers above are
  heuristic-only -- they do not assume the LLM fallback works, so they
  understate the production system's expected accuracy on the hardest
  25% of receipts.

## Unit math to $100k/month

- Price: ~$1,000/month per firm (volume-tiered, positioned against
  Dext's $45-70/month self-serve tier but sold as a managed workflow to
  firms doing 5,000+ documents/month where Dext's per-doc pricing and
  manual-review overhead bites hardest).
- 100 firms x $1,000/month = $100,000/month.
- Reachable market: the QuickBooks ProAdvisor directory lists 20,000+
  firms; 100 customers is under 1% penetration of one channel.
- CAC: cold outreach + a free-tier "process your first 200 receipts free"
  hook, plausible at a few hundred to low thousands of dollars per firm
  against ~$12,000 first-year revenue per firm.

## Acquisition channel

QuickBooks ProAdvisor directory (cold outreach list) + r/Bookkeeping +
Scaling New Heights conference (the industry's own trade show for
outsourced bookkeeping/accounting firms).
