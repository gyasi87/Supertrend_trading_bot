"""
Per-firm categorization rule engine.

This is the actual moat the critic flagged: extraction alone is a
commodity (Veryfi/Nanonets sell that as a bare API). What a bookkeeping
firm needs is books-ready output mapped to *their* chart of accounts and
*their* client's historical categorization pattern -- not a generic
"receipts" bucket that still needs a human to re-sort at month end.

vendor_rules.json holds two tiers: built-in DEFAULT_RULES keyword matches,
and a per-firm "learned" map written every time a bookkeeper corrects a
category in the review UI (see app.py:/accept). Learned entries always
outrank defaults, regardless of keyword length -- an earlier version
picked the longest matching keyword across both tiers combined, which
meant a correction like "shell cafe" -> Meals & Entertainment could lose
to the built-in "ups store" (longer, in a different tier entirely) after
key-shortening logic trimmed the learned key down. Keeping the tiers
separate and always checking learned first removes that failure mode at
the root instead of tuning the key-shortening heuristic further.
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


def _load_store() -> dict:
    if RULES_PATH.exists():
        data = json.loads(RULES_PATH.read_text())
        if "defaults" in data and "learned" in data:
            return data
    store = {"defaults": dict(DEFAULT_RULES), "learned": {}}
    RULES_PATH.write_text(json.dumps(store, indent=2))
    return store


def _longest_match(v: str, rules: dict):
    matches = [(keyword, category) for keyword, category in rules.items() if keyword in v]
    if not matches:
        return None
    return max(matches, key=lambda kc: len(kc[0]))


def categorize(vendor: str) -> tuple[str, float]:
    """Returns (category, confidence). Falls back to 'Uncategorized' with
    0 confidence, which routes the item to the review queue rather than
    silently mis-booking it."""
    if not vendor:
        return "Uncategorized", 0.0
    store = _load_store()
    v = vendor.lower()

    learned_match = _longest_match(v, store["learned"])
    if learned_match:
        return learned_match[1], 0.9

    default_match = _longest_match(v, store["defaults"])
    if default_match:
        return default_match[1], 0.9

    return "Uncategorized", 0.0


def learn(vendor: str, category: str) -> None:
    """Called from the review UI when a bookkeeper corrects/confirms a
    category -- persists the firm-specific mapping for next time. Stored
    in a separate "learned" tier that categorize() always checks first,
    so this correction can never lose to a generic default no matter how
    the vendor string gets normalized."""
    if not vendor or not category:
        return
    store = _load_store()
    key = vendor.lower().split(" #")[0].split(" store")[0].strip()
    key = " ".join(key.split()[:2]) if len(key.split()) > 2 else key
    store["learned"][key] = category
    RULES_PATH.write_text(json.dumps(store, indent=2))
