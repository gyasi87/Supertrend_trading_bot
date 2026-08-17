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
avg_time_per_doc: 0.23s (commodity CPU, no GPU)
vendor accuracy: 100%
total-amount accuracy: 90%
category accuracy (first pass): 100%
auto-approved with zero human touch: 75%
silently-wrong auto-approvals: 0 / 20   <-- the number that actually matters
```

**Two rounds of blind critique found real bugs, both fixed and now
regression-tested (`test_extraction.py`):**

1. *Vendor extraction* originally picked whichever of the first lines
   had the most alphabetic characters, so a metadata line ("Store #895
   Tel: (555) 308-1930") could beat a short real name ("PG&E"). Fixed by
   excluding lines that clearly *start with* a metadata label, while
   still letting a vendor name that merely *contains* one through (e.g.
   "The UPS Store #1234" -- an unanchored first fix broke this case, and
   there's a test for it now).
2. *The auto-approve safety gate had a hole*: when no explicit
   TOTAL/AMOUNT DUE line exists, the extractor guesses the largest
   dollar amount on the receipt -- which can be a pre-auth hold or a
   line-item price, not the real total. That guess used to earn partial
   confidence credit that could still clear the auto-approve threshold
   blended with a good vendor+date. Fixed: a guessed total now carries a
   `total_trusted=False` flag that hard-blocks auto-approval regardless
   of the blended confidence score -- confirmed with a constructed
   adversarial receipt in `test_extraction.py`, not just the benchmark
   batch. Also fixed: the categorization rule engine matched keywords by
   dict insertion order, so a bookkeeper's correction (e.g. "Shell Cafe"
   -> Meals & Entertainment) could never override a shorter generic
   default ("shell" -> Vehicle & Fuel) inserted earlier -- it now matches
   the most specific (longest) keyword, which is what makes the
   "learns from corrections" claim actually true.

The `silently-wrong auto-approvals: 0/20` line is the real claim, and it
now rests on an explicit hard gate (`total_trusted`), not just on
threshold tuning that happened to work on one benchmark batch.

**On cost:** the fair number is fully-loaded -- compute + the per-call
cost of the LLM fallback on flagged documents (published Sonnet 5
pricing, not a live call) + bookkeeper review labor on the 25% of
documents routed to review. No human reviewer was available in this
sandbox to time the review step, so instead of asserting one number,
here's the sensitivity across plausible review times AND labor rates
(the target customer -- high-volume outsourced bookkeeping firms --
commonly staffs review with offshore labor, not only US-based staff):

| Review time / flagged doc | Offshore ($12/hr) | US-based ($30/hr) |
|---|---|---|
| 15s (glance-and-approve, pre-filled form) | $0.014 -- 18.3x cheaper | $0.032 -- 7.7x cheaper |
| 30s | $0.026 -- 9.6x cheaper | $0.064 -- 3.9x cheaper |
| 60s | $0.051 -- 4.9x cheaper | $0.126 -- 2.0x cheaper |
| 120s (full manual re-entry, worst case) | $0.101 -- 2.5x cheaper | $0.251 -- roughly at parity |

At an offshore review rate it beats Dext's list price across the whole
range. At a US-based rate it beats Dext everywhere except the most
pessimistic case (2 minutes of manual re-entry per flagged document),
where it's roughly a wash rather than a clear win -- stated plainly
rather than rounded away. The realistic case (pre-filled fields,
click-to-approve UI, per the screenshot) is closer to the 15-30s rows.

Full JSON report: `benchmark_report.json` (regenerated on every run).
Regression tests for the specific bugs found by critique:
`python3 test_extraction.py`.

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

## Known limitations (not yet fixed)

- **The synthetic benchmark is not a substitute for real messy
  receipts.** `generate_samples.py` adds rotation/blur/noise/crumple
  lines, but has no thermal-paper fade, handwriting, perspective
  distortion, or real camera lighting -- and it also generated the
  ground truth, so accuracy numbers can't be assumed to generalize to
  actually messy photos. No real receipt dataset could be downloaded in
  this sandbox (huggingface.co and similar hosts are network-blocked).
  This is the single biggest thing a next iteration needs: real receipt
  photos, or at minimum a much harsher synthetic generator.
- **Ambiguous numeric dates can silently misparse.** "3-7-2026" is
  parsed as day-month-year (March 7 vs July 3) by pattern order in
  `extract.py`, which matches this project's own synthetic date
  generator but is a genuine ambiguity on real US receipts using
  dash-separated MM-DD-YYYY. Not yet disambiguated or confidence-gated.
- **The LLM fallback has never actually run.** It's real, callable code
  (`LLMExtractor` in `extract.py`), but every accuracy number in this
  README is heuristic-only, because no Anthropic API key is available to
  standalone scripts in this sandbox. The fallback exists specifically
  to handle the harder end of the messy-receipt distribution that the
  heuristic pass routes to review -- until it's exercised against real
  cases, its contribution is a design claim, not a measured one.

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
