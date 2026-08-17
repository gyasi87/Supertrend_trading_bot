"""
Per-firm categorization rule engine.

This is the actual moat the critic flagged: extraction alone is a
commodity (Veryfi/Nanonets sell that as a bare API). What a bookkeeping
firm needs is books-ready output mapped to *their* chart of accounts and
*their* client's historical categorization pattern -- not a generic
"receipts" bucket that still needs a human to re-sort at month end.

vendor_rules.json holds a per-firm keyword -> QBO category map. Every time
a bookkeeper corrects a category in the review UI, the correction is
written back into the rules file (see app.py:/accept), so the mapping
gets more accurate the more the firm uses it -- a cold-start problem Dext
does not solve, because Dext's categories are generic, not firm-specific.
"""
import json
from pathlib import Path

RULES_PATH = Path(__file__).parent / "vendor_rules.json"

DEFAULT_RULES = {
    "hardware": "Supplies & Materials", "paint": "Supplies & Materials",
    "depot": "Supplies & Materials", "costco": "Supplies & Materials",
    "ace hardware": "Supplies & Materials",
    "diner": "Meals & Entertainment", "coffee": "Meals & Entertainment",
    "whole foods": "Meals & Entertainment", "restaurant": "Meals & Entertainment",
    "fuel": "Vehicle & Fuel", "chevron": "Vehicle & Fuel", "shell": "Vehicle & Fuel",
    "gas station": "Vehicle & Fuel",
    "staples": "Office Supplies", "office center": "Office Supplies",
    "parking": "Travel", "airlines": "Travel", "marriott": "Travel",
    "hotel": "Travel", "rent-a-car": "Travel", "enterprise rent": "Travel",
    "uber": "Travel", "lyft": "Travel",
    "comcast": "Utilities", "at&t": "Utilities", "pg&e": "Utilities",
    "wireless": "Utilities", "internet": "Utilities",
    "fedex": "Shipping & Postage", "ups store": "Shipping & Postage",
    "usps": "Shipping & Postage",
}


def _load_rules() -> dict:
    if RULES_PATH.exists():
        return json.loads(RULES_PATH.read_text())
    RULES_PATH.write_text(json.dumps(DEFAULT_RULES, indent=2))
    return dict(DEFAULT_RULES)


def categorize(vendor: str) -> tuple[str, float]:
    """Returns (category, confidence). Falls back to 'Uncategorized' with
    0 confidence, which routes the item to the review queue rather than
    silently mis-booking it."""
    if not vendor:
        return "Uncategorized", 0.0
    rules = _load_rules()
    v = vendor.lower()
    # match the LONGEST (most specific) matching keyword, not whichever
    # happens to come first by insertion order -- otherwise a bookkeeper
    # correction like "shell cafe" -> Meals & Entertainment can never
    # override the generic default "shell" -> Vehicle & Fuel, since dict
    # iteration order put the shorter, earlier-inserted default first.
    # This is what makes learn() below actually take effect.
    matches = [(keyword, category) for keyword, category in rules.items() if keyword in v]
    if matches:
        keyword, category = max(matches, key=lambda kc: len(kc[0]))
        return category, 0.9
    return "Uncategorized", 0.0


def learn(vendor: str, category: str) -> None:
    """Called from the review UI when a bookkeeper corrects/confirms a
    category -- persists the firm-specific mapping for next time."""
    if not vendor or not category:
        return
    rules = _load_rules()
    key = vendor.lower().split(" #")[0].split(" store")[0].strip()
    # keep keys short and generic enough to match future variants
    key = " ".join(key.split()[:2]) if len(key.split()) > 2 else key
    rules[key] = category
    RULES_PATH.write_text(json.dumps(rules, indent=2))
