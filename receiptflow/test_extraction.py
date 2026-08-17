"""
Regression tests for the specific adversarial cases a blind critic
constructed against this pipeline. Each one reproduces a real bug that
was found and fixed -- keeping them here means a future change can't
silently reintroduce them.
"""
from extract import extract_fields
from categorize import categorize, learn, RULES_PATH


def test_vendor_name_containing_metadata_keyword_still_wins():
    # "store #" appearing INSIDE a real vendor name (not as a standalone
    # metadata line) must not be excluded -- this is what broke when the
    # metadata filter was an unanchored substring search.
    ocr = "The UPS Store #1234\n123 Main St\nDate: 07/15/2026\nTOTAL $42.10\n"
    fields = extract_fields(ocr)
    assert fields.vendor == "The Ups Store #1234", f"got vendor={fields.vendor!r}"


def test_metadata_only_line_still_excluded():
    ocr = "PG&E\nStore #895 Tel: (555) 308-1930\nDate: 07-04-2026\nGrand Total $138.52\n"
    fields = extract_fields(ocr)
    assert fields.vendor == "Pg&E", f"got vendor={fields.vendor!r}"


def test_guessed_total_never_auto_trusted():
    # No explicit TOTAL/AMOUNT DUE line -- a "pre-auth hold" amount is
    # larger than the real charge and would be picked as the guessed max.
    # This must come back untrusted regardless of blended confidence, or
    # it can silently auto-approve a wrong dollar amount into the books.
    ocr = "CHEVRON STATION 4471\n2026-07-15\nFuel $62.40\nPre-auth hold $150.00\n"
    fields = extract_fields(ocr)
    assert fields.total == 150.0  # the guess itself is unchanged...
    assert fields.total_trusted is False, "a guessed total must never be marked trusted"


def test_explicit_total_line_is_trusted():
    ocr = "CHEVRON STATION 4471\n2026-07-15\nFuel $62.40\nTOTAL $62.40\n"
    fields = extract_fields(ocr)
    assert fields.total == 62.40
    assert fields.total_trusted is True


def test_bookkeeper_correction_overrides_generic_default():
    if RULES_PATH.exists():
        RULES_PATH.unlink()
    category, _ = categorize("Shell Cafe")
    assert category == "Vehicle & Fuel"  # generic default matches first, before any correction

    learn("Shell Cafe", "Meals & Entertainment")
    category, _ = categorize("Shell Cafe")
    assert category == "Meals & Entertainment", (
        "a specific bookkeeper correction must beat the shorter generic default -- "
        f"got {category!r}"
    )
    RULES_PATH.unlink()


if __name__ == "__main__":
    import sys
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    failures = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"FAIL {t.__name__}: {e}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    sys.exit(1 if failures else 0)
