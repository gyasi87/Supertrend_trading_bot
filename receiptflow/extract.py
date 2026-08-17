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
# Two priority tiers, not one flat alternation: "TOTAL"/"GRAND TOTAL"/
# "TOTAL DUE" name the actual purchase amount. "AMOUNT DUE"/"BALANCE
# DUE"/"YOU PAID" are sometimes the only total-like label on a plain
# receipt (fine to use), but on a receipt with a payment breakdown
# (Total, then a payment line, then a remaining Balance Due) they name
# what's left owed -- not the amount to book as the expense. Treating
# both tiers as interchangeable and taking "whichever matched last" let
# "Balance Due $0.00" after a cash payment silently overwrite the real
# $138.52 total. Primary is checked first and, when present, wins
# outright regardless of where secondary matches fall.
# "GRAND TOTAL" is its own top tier, checked before plain "TOTAL": on a
# receipt with a tip/gratuity added after the pre-tip subtotal+tax total
# ("TOTAL $54.00" ... "Tip $10.00" ... "GRAND TOTAL $64.00"), taking the
# first plain-TOTAL match picked the pre-tip figure, and the arithmetic
# cross-check below then *certified* it as correct because subtotal+tax
# does equal the pre-tip total -- the safety check laundered the error
# instead of catching it. A grand total legitimately does not have to
# equal subtotal+tax (that's exactly what a tip is), so it is trusted on
# its own without that cross-check once found.
# All three tiers are anchored to the START of the line (allowing only
# leading whitespace and thermal-printer decoration like "** TOTAL **").
# An unanchored "TOTAL" substring search matches a *component* total just
# as happily as the document's real total -- "Room Total $180.00" on a
# hotel folio, "Food Total $40.00" on an itemized restaurant check -- and
# being first in reading order, a qualifier-prefixed component total
# would win over the real "TOTAL DUE" line that follows it. This is the
# same failure shape as the earlier vendor-metadata bug (an unanchored
# match beats a qualified one) applied to a new field -- anchoring is the
# fix there too: "TOTAL DUE $210.00" starts with the label, "Room Total
# $180.00" does not.
#
# The first anchoring pass required the label immediately adjacent to
# the amount ("TOTAL\s*:?\s*\$?\s?<amount>"), which broke just as many
# real labels as it fixed: "TOTAL AMOUNT DUE: $138.52" (a canonical
# invoice/utility-bill label) or "AMOUNT DUE THIS PERIOD: $54.00" no
# longer matched anything, since a qualifier word sits between the label
# and the amount, not just punctuation. The follow-up fix loosened the
# gap to a generic `.*?` (any characters) before the amount -- which
# over-corrected the other way: "TOTAL MONTHLY CHARGES 45.00" and "TOTAL
# ROOM CHARGES 180.00" then matched too, treating a component/scoped
# total exactly like the earlier "Room Total" bug this whole anchoring
# effort started from, just with the disqualifying word trailing the
# label instead of leading it.
#
# Two point-fixes in the same direction both failed because "how much
# text is allowed between the label and the amount" isn't answerable
# with a single generic wildcard OR a single strict adjacency rule --
# some continuations are legitimate (AMOUNT DUE, THIS PERIOD) and some
# are disqualifying (MONTHLY CHARGES, ROOM, FOOD), and a regex has no
# way to tell those apart by shape alone. The fix is a closed, explicit
# list of known-good compound labels (matched as fixed phrases) instead
# of a wildcard gap -- unlisted continuations no longer match at all,
# which is safe by construction: an unrecognized label falls through to
# the untrusted guess path rather than being silently accepted either
# too broadly or too narrowly.
_LEADING_DECORATION = r"^[\s*=~\-]*"
# separator between a matched label and the amount: punctuation and
# whitespace only, never letters -- this is what actually distinguishes
# "TOTAL AMOUNT DUE: $138.52" (colon+space, fine) from "TOTAL MONTHLY
# CHARGES 45.00" (the letters "MONTHLY" are not a separator, so that
# input can only match if "MONTHLY" is itself part of a listed phrase --
# it isn't, so it correctly doesn't match at all).
_SEP = r"[\s*=~:.\-]*\$?\s?"
GRAND_TOTAL_LINE_RE = re.compile(_LEADING_DECORATION + r"(?:GRAND\s*TOTAL)" + _SEP + r"(\d[\d,]*\.\d{2})", re.IGNORECASE)
PRIMARY_TOTAL_LINE_RE = re.compile(
    _LEADING_DECORATION
    + r"(TOTAL\s*AMOUNT\s*DUE|TOTAL\s*DUE|TOTAL\s*PAYABLE|TOTAL\s*BALANCE\s*DUE|TOTAL\s*CHARGES|TOTAL(?!ED))"
    + _SEP + r"(\d[\d,]*\.\d{2})", re.IGNORECASE,
)
SECONDARY_TOTAL_LINE_RE = re.compile(
    _LEADING_DECORATION
    + r"(AMOUNT\s*DUE\s*THIS\s*PERIOD|AMOUNT\s*DUE\s*NOW|AMOUNT\s*DUE|BALANCE\s*DUE|YOU\s*PAID)"
    + _SEP + r"(\d[\d,]*\.\d{2})", re.IGNORECASE,
)
SUBTOTAL_WORD_RE = re.compile(r"\bSUB\s*-?\s*TOTAL", re.IGNORECASE)
TAX_WORD_RE = re.compile(r"\b(SALES\s*TAX|HST|VAT|TAX)\b", re.IGNORECASE)
TIP_WORD_RE = re.compile(r"\b(TIP|GRATUITY)\b", re.IGNORECASE)
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

        # date: search lines carrying a "date" label FIRST, before falling
        # back to any date-shaped pattern anywhere in the text -- a
        # receipt can print an unrelated date (a return-by deadline, a
        # loyalty-program expiry) well before the actual purchase date
        # line, and an unlabeled whole-text scan has no way to tell them
        # apart. A digit OCR misread (e.g. "2026" -> "2626") still parses
        # as a valid date, so it won't raise -- caught separately by
        # sanity-checking the year against a plausible range. When either
        # check fails we still surface the raw (untrusted) parse as
        # `date` so the review UI can pre-fill a best guess rather than
        # leaving the field blank -- but it does NOT count toward
        # confidence, so the item still routes to review either way.
        date = None
        current_year = datetime.now().year
        labeled_lines = [l for l in lines if re.search(r"\bdate\b", l, re.IGNORECASE)]
        near_header_lines = lines[:8]  # where a purchase date conventionally
        # sits even without a "Date:" label -- trusted as a secondary zone,
        # searched only if no explicitly labeled line matched
        for candidate_lines, trustable in ((labeled_lines, True), (near_header_lines, True), (lines, False)):
            if date is not None:
                break
            haystack = "\n".join(candidate_lines)
            for pattern, fmt in DATE_PATTERNS:
                m = pattern.search(haystack)
                if m:
                    try:
                        parsed = datetime.strptime(m.group(1), fmt).date()
                        date = parsed.isoformat()
                        if trustable and current_year - 2 <= parsed.year <= current_year + 1:
                            conf_hits += 1
                        break
                    except ValueError:
                        continue

        # total: three tiers, checked in order. GRAND TOTAL (the final,
        # all-inclusive figure, tip/gratuity included -- trusted on its
        # own, no arithmetic cross-check, since a tip is a legitimate
        # reason it won't equal subtotal+tax). Then plain TOTAL/TOTAL DUE
        # (the FIRST match -- the original total, before any payment-
        # breakdown lines that follow it -- cross-checked against
        # subtotal+tax when both are known). Then AMOUNT DUE/BALANCE
        # DUE/YOU PAID only if nothing above matched (can be a post-
        # payment remainder, not the purchase total).
        total = None
        total_trusted = False
        subtotal_amt = tax_amt = None
        for line in lines:
            if SUBTOTAL_WORD_RE.search(line):
                nums = MONEY_RE.findall(line)
                if nums:
                    subtotal_amt = float(nums[-1].replace(",", ""))
                continue
            if TAX_WORD_RE.search(line):
                nums = MONEY_RE.findall(line)
                if nums:
                    tax_amt = float(nums[-1].replace(",", ""))
            m = GRAND_TOTAL_LINE_RE.search(line)
            if m:
                total = float(m.group(1).replace(",", ""))

        if total is not None:
            conf_hits += 1
            total_trusted = True
            # a tip only ever adds -- if subtotal+tax is known, the grand
            # total must be at least that much. A grand total LESS than
            # subtotal+tax means a digit was dropped (OCR corruption) or
            # this "grand total" doesn't belong to this subtotal/tax pair
            # at all; either way it must not be trusted on the label alone.
            if subtotal_amt is not None and tax_amt is not None:
                if total < (subtotal_amt + tax_amt) - 0.02:
                    total_trusted = False
        else:
            has_tip_line = any(TIP_WORD_RE.search(line) for line in lines)
            for line in lines:
                if SUBTOTAL_WORD_RE.search(line):
                    continue
                m = PRIMARY_TOTAL_LINE_RE.search(line)
                if m and total is None:
                    total = float(m.group(2).replace(",", ""))

            if total is None:
                for line in lines:
                    if SUBTOTAL_WORD_RE.search(line):
                        continue
                    m = SECONDARY_TOTAL_LINE_RE.search(line)
                    if m:
                        total = float(m.group(2).replace(",", ""))

            if total is not None:
                conf_hits += 1
                total_trusted = True
                # arithmetic cross-check: if subtotal + tax is known and
                # disagrees with the labeled total by more than a cent,
                # the label matched but the number itself is suspect (OCR
                # digit corruption, or a total that doesn't correspond to
                # this subtotal/tax pair at all) -- don't let a matched
                # label alone certify a number that fails its own
                # receipt's arithmetic.
                if subtotal_amt is not None and tax_amt is not None:
                    if abs((subtotal_amt + tax_amt) - total) > 0.02:
                        total_trusted = False
                # a tip/gratuity line with no GRAND TOTAL anywhere means
                # this plain TOTAL is the pre-tip figure, not the final
                # charge -- arithmetic matching subtotal+tax doesn't save
                # it here, since that's exactly the pre-tip case.
                if has_tip_line:
                    total_trusted = False

        if total is None:
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
