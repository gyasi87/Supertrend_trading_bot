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


def test_balance_due_after_payment_does_not_override_real_total():
    # A payment-breakdown receipt: the real purchase total is $138.52,
    # but a "Balance Due $0.00" line follows because it was paid in cash.
    # The old "whichever total-like line matched last" logic would book
    # $0.00 for a $138.52 expense.
    ocr = "ACE HARDWARE\n2026-03-14\nTOTAL $138.52\nPAID CASH $140.00\nBALANCE DUE $0.00\n"
    fields = extract_fields(ocr)
    assert fields.total == 138.52, f"got total={fields.total!r}"
    assert fields.total_trusted is True


def test_gift_card_amount_due_does_not_override_total():
    ocr = "COSTCO\n2026-03-14\nTOTAL $412.00\nGIFT CARD -$400.00\nAMOUNT DUE $12.00\n"
    fields = extract_fields(ocr)
    assert fields.total == 412.00, f"got total={fields.total!r}"


def test_arithmetic_mismatch_marks_total_untrusted():
    # subtotal + tax = 454.68, but the labeled total is 45.68 -- an OCR
    # digit drop. The label matched, but the number fails its own
    # receipt's arithmetic and must not be auto-approved on label match
    # alone.
    ocr = "STAPLES\n2026-03-14\nSubtotal $421.00\nTax $33.68\nTOTAL $45.68\n"
    fields = extract_fields(ocr)
    assert fields.total_trusted is False, "an arithmetic mismatch must mark the total untrusted"


def test_arithmetic_match_stays_trusted():
    ocr = "STAPLES\n2026-03-14\nSubtotal $421.00\nTax $33.68\nTOTAL $454.68\n"
    fields = extract_fields(ocr)
    assert fields.total == 454.68
    assert fields.total_trusted is True


def test_tip_after_pretip_total_is_not_laundered_by_arithmetic_check():
    # Subtotal + tax legitimately equals the PRE-tip total ($54.00) --
    # that's not an error, it's what a tip is. Taking the first
    # "TOTAL"-labeled line and having the arithmetic check "confirm" it
    # actively certified the wrong (pre-tip) amount as trusted. The real
    # charge is the GRAND TOTAL after the tip.
    ocr = "OAK DINER\n2026-03-14\nSubtotal $50.00\nTax $4.00\nTOTAL $54.00\nTip $10.00\nGRAND TOTAL $64.00\n"
    fields = extract_fields(ocr)
    assert fields.total == 64.00, f"got total={fields.total!r}, must be the post-tip grand total"
    assert fields.total_trusted is True


def test_unlabeled_unrelated_date_does_not_win_over_labeled_purchase_date():
    # "Return by 2026-09-30" is a policy deadline, not the purchase date
    # -- it must not win just because it's a valid, in-range, ISO-shaped
    # date that appears first in the text.
    ocr = "GENERAL STORE\nReturn any item by 2026-09-30\nDate: 07/15/2026\nTOTAL $22.00\n"
    fields = extract_fields(ocr)
    assert fields.date == "2026-07-15", f"got date={fields.date!r}"


def test_grand_total_below_subtotal_plus_tax_is_untrusted():
    # A tip only ever adds -- a "GRAND TOTAL" that's LESS than
    # subtotal+tax means a digit was dropped (OCR corruption), not a
    # legitimate discount. Round 4 exempted GRAND TOTAL from the
    # arithmetic check entirely (correct for the >= case, a tip), but
    # that also let an impossible under-total slip through untrusted.
    ocr = "OAK DINER\n2026-03-14\nSubtotal $50.00\nTax $4.00\nTOTAL $54.00\nTip $10.00\nGRAND TOTAL $6.00\n"
    fields = extract_fields(ocr)
    assert fields.total_trusted is False, "a grand total below subtotal+tax must not be trusted"


def test_tip_line_without_grand_total_marks_pretip_total_untrusted():
    # A handwritten/blank tip line with no printed grand total: the only
    # extractable total is the pre-tip "TOTAL" line, which is not the
    # final charge. Must not auto-approve on the pre-tip figure.
    ocr = "OAK DINER\n2026-03-14\nSubtotal $50.00\nTax $4.00\nTOTAL $54.00\nTip ____\n"
    fields = extract_fields(ocr)
    assert fields.total_trusted is False, "a pre-tip total with an unresolved tip line must not be trusted"


def test_hotel_folio_room_total_does_not_beat_total_due():
    ocr = "MARRIOTT DOWNTOWN\n2026-03-14\nRoom Total $180.00\nOccupancy Tax $30.00\nTOTAL DUE $210.00\n"
    fields = extract_fields(ocr)
    assert fields.total == 210.00, f"got total={fields.total!r}, must be TOTAL DUE, not the component Room Total"


def test_itemized_check_section_total_does_not_beat_grand_total():
    ocr = "OAK DINER\n2026-03-14\nFood Total $40.00\nBar Total $14.00\nTOTAL $54.00\n"
    fields = extract_fields(ocr)
    assert fields.total == 54.00, f"got total={fields.total!r}, must be the receipt's TOTAL, not a section subtotal"


def test_qualifier_prefixed_total_line_is_not_matched_as_primary():
    # No anchored TOTAL/TOTAL DUE line exists at all here -- only a
    # component "Line Total". Must NOT be picked up as a trusted primary
    # total; falling through to the untrusted guess path is correct.
    ocr = "SOME VENDOR\n2026-03-14\nItem A Line Total $12.00\nItem B Line Total $8.00\n"
    fields = extract_fields(ocr)
    assert fields.total_trusted is False, "an unanchored component total must not be trusted as the document total"


def test_total_amount_due_label_with_qualifier_words_is_recognized():
    # The first anchoring fix (round 6) required the label immediately
    # adjacent to the amount, which broke this canonical invoice/utility
    # label: qualifier words sit between "TOTAL" and the dollar amount,
    # not just punctuation. Must still be trusted.
    ocr = "CITY UTILITIES\n2026-03-14\nTOTAL AMOUNT DUE: $138.52\n"
    fields = extract_fields(ocr)
    assert fields.total == 138.52
    assert fields.total_trusted is True


def test_amount_due_with_trailing_qualifier_is_recognized():
    ocr = "CITY UTILITIES\n2026-03-14\nAMOUNT DUE THIS PERIOD: $54.00\n"
    fields = extract_fields(ocr)
    assert fields.total == 54.00
    assert fields.total_trusted is True


def test_thermal_printer_decoration_around_total_label_still_matches():
    ocr = "CORNER STORE\n2026-03-14\n** TOTAL ** $54.00\n"
    fields = extract_fields(ocr)
    assert fields.total == 54.00
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


def test_correction_on_vendor_matching_a_longer_default_still_takes():
    # "The UPS Store #1234" normalizes (via learn()'s key-shortening) to
    # "the ups" -- a SHORTER key than the built-in default "ups store".
    # Tiered storage (learned checked before defaults) must make the
    # correction win regardless, since length-based tiebreaking across a
    # combined map would let the longer default win instead.
    if RULES_PATH.exists():
        RULES_PATH.unlink()
    category, _ = categorize("The UPS Store #1234")
    assert category == "Shipping & Postage"  # built-in default

    learn("The UPS Store #1234", "Office Supplies")
    category, _ = categorize("The UPS Store #1234")
    assert category == "Office Supplies", (
        f"correction on a vendor matching a longer default must still win -- got {category!r}"
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
