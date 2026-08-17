"""
Structured field extraction from raw OCR text.

Two backends behind one interface:

- HeuristicExtractor: layout/regex-based first pass. Free, instant,
  no external dependency. This is what actually runs in this sandbox
  (huggingface.co and ollama.com are network-blocked here, and no
  Anthropic API key is provisioned for standalone scripts).

- LLMExtractor: same interface, calls out to an LLM (Anthropic API) for
  receipts the heuristic pass flags as low-confidence -- vendor name
  buried in a noisy header, non-standard total label, handwritten
  amendments, etc. This is the piece that lets the product beat
  template-matching OCR (Dext/Ocrolus) on messy, non-standard receipts:
  those tools fall back to manual review on exactly this class of
  document, which is where the labor cost that makes Dext expensive
  actually comes from. Wired for real here (see llm_extract below) but
  not exercised live in this sandbox for lack of API credentials -- the
  benchmark reports heuristic-only accuracy so the numbers are real.
"""
import os
import re
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Optional

MONEY_RE = re.compile(r"\$?\s?(\d{1,3}(?:,\d{3})*\.\d{2})")
DATE_PATTERNS = [
    (re.compile(r"\b(\d{4}-\d{2}-\d{2})\b"), "%Y-%m-%d"),
    (re.compile(r"\b(\d{1,2}/\d{1,2}/\d{4})\b"), "%m/%d/%Y"),
    (re.compile(r"\b(\d{1,2}/\d{1,2}/\d{2})\b"), "%m/%d/%y"),
    (re.compile(r"\b(\d{1,2}-\d{1,2}-\d{4})\b"), "%d-%m-%Y"),
    (re.compile(r"\b([A-Z][a-z]{2} \d{1,2}, \d{4})\b"), "%b %d, %Y"),
    (re.compile(r"\b(\d{1,2} [A-Z][a-z]{2} \d{4})\b"), "%d %b %Y"),
]
TOTAL_LINE_RE = re.compile(
    r"(TOTAL(?!ED)|AMOUNT\s*DUE|GRAND\s*TOTAL|BALANCE\s*DUE|YOU\s*PAID|TOTAL DUE)\s*:?\s*\$?\s?(\d[\d,]*\.\d{2})",
    re.IGNORECASE,
)
SUBTOTAL_WORD_RE = re.compile(r"\bSUB\s*-?\s*TOTAL", re.IGNORECASE)
# lines that are clearly receipt metadata, not a business name -- a store
# number, phone number, transaction id, or date line can have plenty of
# alphabetic characters ("Store #895 Tel: (555) 308-1930") and used to
# out-score the real vendor name under a pure alpha-count heuristic.
# Anchored to the START of the line: a business whose actual name
# contains "Store #" ("The UPS Store #1234") should still win vendor
# selection, since the label only appears as noise mid-line there, not
# as the line's own subject -- an unanchored search killed that case.
METADATA_LINE_START_RE = re.compile(
    r"^\s*(store\s*#|tel:?\s*\(?\d|trans(?:action)?\s*#|date:?\b|receipt\b|invoice\s*#)",
    re.IGNORECASE,
)
# a bare phone number, in contrast, essentially never appears as part of
# a business's printed name, so this one is fine to match anywhere
PHONE_NUMBER_RE = re.compile(r"\(\d{3}\)\s*\d{3}[-.]?\d{4}")


@dataclass
class ExtractedFields:
    vendor: Optional[str]
    date: Optional[str]
    total: Optional[float]
    confidence: float
    method: str
    # False when `total` is a fallback guess (the largest dollar amount
    # found anywhere on the receipt) rather than a match on an explicit
    # TOTAL/AMOUNT DUE/etc. line. A guessed total can pick up a pre-auth
    # hold, a tip suggestion, or a line-item price larger than the actual
    # total -- it must never be enough on its own to clear auto-approval,
    # no matter how confident the other fields are. See pipeline.py.
    total_trusted: bool = True

    def to_dict(self):
        return asdict(self)


class HeuristicExtractor:
    """Regex + layout heuristics. This is the real, running extractor."""

    def extract(self, ocr_text: str) -> ExtractedFields:
        lines = [l.strip() for l in ocr_text.splitlines() if l.strip()]
        conf_hits = 0
        conf_total = 3

        # vendor: the first line that (a) has real letters and (b) isn't
        # obviously metadata (store#/phone/trans#/date) -- receipts print
        # the business name first, but a pure "most alphabetic characters"
        # heuristic loses to a metadata line like "Store #895 Tel: (555)
        # 308-1930" against a short-but-real name like "PG&E", so metadata
        # lines are excluded before scoring rather than after.
        vendor = None
        for l in lines[:5]:
            if METADATA_LINE_START_RE.match(l) or PHONE_NUMBER_RE.search(l):
                continue
            letters = sum(c.isalpha() for c in l)
            if letters >= 2:
                vendor = l.title()
                conf_hits += 1
                break

        # date: try each known pattern across the whole text. A digit OCR
        # misread (e.g. "2026" -> "2626") still parses as a valid date, so
        # it won't raise -- it has to be caught by sanity-checking the
        # year against a plausible range, or it silently corrupts the
        # books instead of routing to review. When the sanity check fails
        # we still surface the raw (untrusted) parse as `date` so the
        # review UI can pre-fill a best guess for the bookkeeper to
        # correct rather than leaving the field blank to type from
        # scratch -- but it does NOT count toward confidence, so the item
        # still routes to review either way.
        date = None
        current_year = datetime.now().year
        for pattern, fmt in DATE_PATTERNS:
            m = pattern.search(ocr_text)
            if m:
                try:
                    parsed = datetime.strptime(m.group(1), fmt).date()
                    date = parsed.isoformat()
                    if current_year - 2 <= parsed.year <= current_year + 1:
                        conf_hits += 1
                    break
                except ValueError:
                    continue

        # total: prefer an explicit TOTAL/AMOUNT DUE line, scanning lines
        # individually and skipping "Subtotal" (which contains "total" as
        # a substring -- a whole-text regex would grab it by mistake).
        # The real total line is usually the last such match on a receipt,
        # since subtotal/tax are listed before the grand total.
        total = None
        total_trusted = False
        for line in lines:
            if SUBTOTAL_WORD_RE.search(line):
                continue
            m = TOTAL_LINE_RE.search(line)
            if m:
                total = float(m.group(2).replace(",", ""))
        if total is not None:
            conf_hits += 1
            total_trusted = True
        else:
            amounts = [float(x.replace(",", "")) for x in MONEY_RE.findall(ocr_text)]
            if amounts:
                total = max(amounts)
                conf_hits += 0.4  # partial credit toward the *display* confidence score only --
                # total_trusted stays False, which independently blocks
                # auto-approval in pipeline.py regardless of this score

        confidence = conf_hits / conf_total
        return ExtractedFields(vendor=vendor, date=date, total=total,
                                confidence=round(min(confidence, 1.0), 2),
                                method="heuristic", total_trusted=total_trusted)


class LLMExtractor:
    """
    Real integration path for messy cases the heuristic pass can't resolve
    confidently. Calls the Anthropic API with the OCR text and asks for
    strict-JSON {vendor, date, total}. Requires ANTHROPIC_API_KEY.
    Not exercised in this sandbox (no key provisioned to standalone
    scripts) -- included so the architecture is real, not hand-waved.
    """

    def __init__(self):
        self.api_key = os.environ.get("ANTHROPIC_API_KEY")

    def available(self) -> bool:
        return bool(self.api_key)

    def extract(self, ocr_text: str) -> ExtractedFields:
        if not self.available():
            raise RuntimeError("ANTHROPIC_API_KEY not set -- LLM fallback unavailable in this environment")
        import anthropic  # pip install anthropic
        client = anthropic.Anthropic(api_key=self.api_key)
        prompt = (
            "Extract vendor, date (ISO 8601), and total (number only) from this "
            "receipt OCR text. Respond with strict JSON only, no prose.\n\n" + ocr_text
        )
        resp = client.messages.create(
            model="claude-sonnet-5",
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        import json
        data = json.loads(resp.content[0].text)
        return ExtractedFields(vendor=data.get("vendor"), date=data.get("date"),
                                total=data.get("total"), confidence=0.95, method="llm")


def extract_fields(ocr_text: str, low_confidence_threshold: float = 0.7) -> ExtractedFields:
    """Heuristic first pass; escalate to the LLM backend only when it's
    actually available and the heuristic pass came back low-confidence."""
    result = HeuristicExtractor().extract(ocr_text)
    if result.confidence < low_confidence_threshold:
        llm = LLMExtractor()
        if llm.available():
            try:
                return llm.extract(ocr_text)
            except Exception:
                pass
    return result
