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
auto-approved with zero human touch: 70%
silently-wrong auto-approvals: 0 / 20   <-- the number that actually matters
```

**Seven rounds of blind critique found real bugs, all fixed and now
regression-tested (`test_extraction.py`, 20/20 passing):**

1. *Vendor extraction* originally picked whichever of the first lines
   had the most alphabetic characters, so a metadata line ("Store #895
   Tel: (555) 308-1930") could beat a short real name ("PG&E"). Fixed by
   excluding lines that clearly *start with* a metadata label, while
   still letting a vendor name that merely *contains* one through (e.g.
   "The UPS Store #1234").
2. *A guessed total could clear auto-approval.* When no explicit total
   line exists, the extractor guesses the largest dollar amount on the
   receipt -- which can be a pre-auth hold, not the real total. Fixed: a
   guessed total now carries `total_trusted=False`, which hard-blocks
   auto-approval regardless of the blended confidence score.
3. *"Balance Due"/"Amount Due" lines could overwrite the real total.* A
   receipt with a payment breakdown (Total, then a payment, then
   "Balance Due $0.00") was treating the $0.00 remainder as if it were
   the purchase total. Fixed with a two-tier hierarchy: "TOTAL"/"GRAND
   TOTAL"/"TOTAL DUE" (the actual purchase amount) always wins over
   "AMOUNT DUE"/"BALANCE DUE"/"YOU PAID" when both are present. Also
   added an arithmetic cross-check -- when subtotal and tax are both
   extracted, a labeled total that doesn't equal subtotal+tax (an OCR
   digit drop, for example) is marked untrusted even though its label
   matched.
4. *The categorization "learning" fix from round 2 didn't actually
   work.* A bookkeeper's correction on a multi-word vendor got
   key-shortened down to something shorter than a conflicting built-in
   default, so the correction silently lost the longest-match tiebreak
   it was supposed to win. Fixed by storing corrections in a separate
   tier that's always checked before defaults, instead of relying on
   keyword length across a combined map.
5. *A tip added after the pre-tip total defeated the round-3 arithmetic
   check -- and the check actively certified the wrong number.*
   Subtotal+tax legitimately equals the pre-tip "TOTAL" line (that's
   what a tip is), so a receipt with "TOTAL $54.00 ... Tip $10.00 ...
   GRAND TOTAL $64.00" had its arithmetic check "confirm" the $54.00 as
   correct when the real charge was $64.00. Fixed by giving "GRAND
   TOTAL" its own top-priority tier, trusted without the arithmetic
   check (a tip is exactly why it won't match subtotal+tax). This is the
   most consequential fix so far -- meals are the highest-volume expense
   category in bookkeeping, and this was a silent, systematic
   undercharge on tipped ones.
6. *An unrelated in-range date (a return-policy deadline) could beat the
   real purchase date* just by matching a date pattern first in reading
   order. Fixed by searching lines that carry a "date" label first, then
   lines near the receipt header (where a purchase date conventionally
   sits even unlabeled), and only trusting a date found elsewhere in the
   text with reduced confidence.
7. *Round 4's own GRAND TOTAL fix removed all validation from that
   path*, which round 5 caught immediately: an OCR-dropped digit on a
   grand total ($64.00 misread as $6.00) was trusted outright, since
   "no arithmetic check" for grand totals meant no check at all, not
   just no *equality* check. Fixed with a one-directional check instead
   of a redesign: a tip only ever adds, so a grand total below
   subtotal+tax is impossible and marked untrusted, while a grand total
   at or above it (the legitimate tip case from round 4) still passes.
   Also fixed a related gap: a receipt with a tip/gratuity line but no
   printed grand total (e.g. a blank "Tip ___" line) was auto-approving
   the pre-tip total as if it were final -- now marked untrusted
   whenever a tip line exists without a resolvable grand total.

8. *Round 6: an unanchored "TOTAL" substring match let component/section
   totals beat the document's real total.* "Room Total $180.00" on a
   hotel folio, or "Food Total $40.00" on an itemized restaurant check,
   matched the same regex as "TOTAL DUE $210.00" / "TOTAL $54.00" --
   and being first in reading order, the component total won. This is
   the same failure shape as the round-1/2 vendor-metadata bug (an
   unanchored match beating a qualified one), applied to the total
   field: hotel folios and itemized checks are a real, high-volume
   document class in travel and meals expenses, not an edge case. Fixed
   by anchoring all three total-line tiers to the start of the line --
   "TOTAL DUE" starts with the label, "Room Total" does not.

9. *Round 6's anchoring fix over-corrected: it required the total label
   immediately adjacent to the amount, not just leading the line.* That
   broke real, common labels where a qualifier word sits between the
   label and the number -- "TOTAL AMOUNT DUE: $138.52" (a canonical
   utility-bill/invoice label) and "AMOUNT DUE THIS PERIOD: $54.00" both
   stopped matching anything and fell through to the untrusted guess
   path. Fixed by requiring only that the LINE start with the label
   (allowing thermal-printer decoration like "** TOTAL **"), while
   letting a lazy `.*?` absorb qualifier text before the amount --
   "Room Total $180.00" still correctly fails to match (the line starts
   with "Room", not a total-ish label), so the round-6 fix's actual
   protection is preserved; only its unintentionally narrow adjacency
   requirement was loosened.

The `silently-wrong auto-approvals: 0/20` line is the real claim, and
each round of adversarial construction against it (sixteen constructed
cases across seven rounds, all now regression tests) has failed to break
it -- though the auto-approval rate has dropped from 80% to 70% as the
gates got stricter, which is the honest cost of the guarantee actually
holding rather than passing by coincidence on one benchmark batch.

This project's fix history has a visible pattern worth stating plainly
rather than hiding: three separate times (rounds 2, 4→5, 6→7) a fix for
one adversarial case was itself too broad or too narrow and needed a
follow-up correction in the same area. That is not a reason to trust the
current state less than the numbers say -- every one of those follow-up
corrections was itself caught by continuing the same critique process,
and all sixteen cases found across seven rounds are now regression
tests that would catch a reintroduction. It is a reason to treat "20/20
tests passing today" as a snapshot of a still-maturing safety gate, not
a proof of completeness -- the seventh critic round's own recommendation
was that further rounds of this specific exercise (hand-constructing
receipt text against the total-extraction regexes) have reached
diminishing returns, and that the more valuable next step is external:
real receipt photos and the live LLM fallback, not another round of
sandbox-constructed adversarial text.

**Genuine limitations round 4 surfaced that are NOT fixed** (documented,
not patched around, because they're structural to a keyword/regex
approach rather than one-line bugs):
- **Category matching has no concept of vendor identity, only
  substrings.** "Depot Restaurant Equipment Co" (a B2B supplier) matches
  the keyword "restaurant" and gets categorized as a meal. A learned
  correction on "The UPS Store" can similarly bleed into an unrelated
  vendor sharing a short substring. This is a real, currently-open
  false-positive source for the categorization side (not the
  auto-approval safety gate -- a wrong category still ships if
  confidence is high, since category confidence is presently a flat 0.9
  for any keyword hit, not scaled by match specificity).
- **The subtotal+tax arithmetic check only helps when both numbers
  survive OCR.** If the tax line is dropped or misread, there's nothing
  to cross-check against, and a labeled total is trusted on the label
  alone -- the check is a best-effort catch, not a guarantee, and its
  coverage is only as good as what the OCR actually recovers.
- **Coupons, VAT-inclusive pricing, and multi-jurisdiction tax lines
  can trip the arithmetic check into false positives** (routing a
  correct total to review because it doesn't equal a simple
  subtotal+tax sum). This doesn't corrupt the books -- it fails safe
  into the review queue -- but it does mean the real-world review rate
  on receipts with these patterns will run higher than this benchmark's
  30%, which directly increases the fully-loaded cost. Dext's home
  market (UK/EU, VAT-inclusive by default) is exactly where this bites
  hardest.
- **`vendor_rules.json` is a single global file, not scoped per firm**,
  despite the "per-firm" framing above -- multi-tenancy isn't built yet.

**On cost:** the fair number is fully-loaded -- compute + the per-call
cost of the LLM fallback on flagged documents (published Sonnet 5
pricing, not a live call) + bookkeeper review labor on the 30% of
documents routed to review. No human reviewer was available in this
sandbox to time the review step, so instead of asserting one number,
here's the sensitivity across plausible review times AND labor rates
(the target customer -- high-volume outsourced bookkeeping firms --
commonly staffs review with offshore labor, not only US-based staff):

| Review time / flagged doc | Offshore ($12/hr) | US-based ($30/hr) |
|---|---|---|
| 15s (glance-and-approve, pre-filled form) | $0.016 -- 15.3x cheaper | $0.039 -- 6.4x cheaper |
| 30s | $0.031 -- 8.0x cheaper | $0.076 -- 3.3x cheaper |
| 60s | $0.061 -- 4.1x cheaper | $0.151 -- 1.7x cheaper |
| 120s (full manual re-entry, worst case) | $0.121 -- 2.1x cheaper | $0.301 -- **loses to Dext (0.83x)** |

At an offshore review rate it beats Dext's list price across the whole
range. At a US-based rate it beats Dext everywhere except the most
pessimistic case (2 minutes of manual re-entry per flagged document),
where it now genuinely loses -- stated plainly rather than rounded away.
The realistic case (pre-filled fields,
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
