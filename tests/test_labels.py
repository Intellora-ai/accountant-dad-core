"""The label matcher, and the one character an engine cannot be trusted with.

WHY THIS FILE EXISTS
--------------------
`accountant/extract/labels.py` holds the one label vocabulary this repository
has, and until 2026-08-13 it matched `SUPPLIER:` and nothing else. That was
correct for the tier it was written for and wrong for the tier that joined it.

MEASURED, and this is the whole of the defect:

    tesseract artifacts/ground_truth/documents/GT-0041.png -

    SUPPLIER? AQUANCED PROPULSION CENTRE UK LTO

The colon came back as a question mark. The label word survived, the VALUE was
mangled, and the field was thrown away over one character of punctuation.

WHAT IS BEING TRADED, AND WHAT MUST NOT BE
-------------------------------------------
The text-layer tier measures 20/20 party, 20/20 total, 20/20 tax and 14/20 date
on the twenty corpus PDFs with ZERO wrong. That is its best property and it is
worth more than any number of extra photographs read. So the tolerance is
scoped by a `Printing` the caller states, and the control that matters most in
this file is not the one that proves the tolerance works - it is the one that
proves a PDF never gets it.

THREE THINGS THAT WOULD BE WORTH SHIPPING A BUG FOR
-----------------------------------------------------
    a looser text layer     `SUPPLIER?` matching on a PDF means the bytes said
                            one thing and the reader read another. Nothing on a
                            text layer is ambiguous, so nothing on it needs
                            tolerating.

    a tolerated WORD        `SUPPLIERS OF FINE GOODS` is a sentence, not a
                            labelled field. MEASURED on the corpus PNGs: the
                            engine turns `SUPPLIER:` into `SUPPLIERS` eight
                            times out of twenty, which is exactly why a letter
                            must never be accepted as a separator - the two are
                            indistinguishable, and guessing reads a heading as
                            a supplier.

    a corrected VALUE       `AQUANCED` is not `ADVANCED` and nothing here may
                            make it one. Correcting a value invents data; the
                            page said `AQUANCED` and the record says `AQUANCED`
                            at a confidence that asks a person.

WHAT THIS FILE DOES NOT PROVE
------------------------------
That the pictures now read CORRECTLY. They mostly do not, and that is the
point: what the tolerance buys is fields that come back READ-BUT-WRONG at a low
confidence instead of unread, where `cage/decision.py` can argue with them.
`tests/test_pagereader.py` holds the assertion that none of them is both wrong
and auto-postable.
"""

from __future__ import annotations

import json
import pathlib
import shutil
from decimal import Decimal

import pytest

from accountant.cage.confidence import EXACT
from accountant.extract.adapter import NOT_FOUND, ExtractedRecord
from accountant.extract.freeocr import FreeReader
from accountant.extract.labels import (
    DATE_LABEL,
    NET_LABELS,
    PARTY_LABELS,
    TAX_WHOLE,
    TOTAL_LABELS,
    Printing,
    amounts_for,
    values_for,
)
from accountant.extract.pagereader import page_reader
from accountant.extract.registry import build

REPO = pathlib.Path(__file__).resolve().parent.parent
DOCUMENTS = REPO / "artifacts" / "ground_truth" / "documents"
CASES = REPO / "artifacts" / "ground_truth" / "cases"

PNG = "image/png"

#: Generous on purpose, for the reason `tests/test_pagereader.py` gives: a test
#: that failed because a laptop was busy would be flaky, and what is under test
#: is never the number, it is that there IS one.
DEADLINE = 30.0

NEEDS_THE_ENGINE = pytest.mark.skipif(
    shutil.which("tesseract") is None,
    reason=(
        "SKIPPED LOUDLY: no `tesseract` binary on PATH, so nothing here can "
        "read a picture. Install it with `brew install tesseract` (macOS) or "
        "`apt-get install -y tesseract-ocr` (Linux). The per-field numbers "
        "these tests measure are recorded in the module docstring of "
        "accountant/extract/pagereader.py and are NOT re-proved when this skips."
    ),
)


def printed(lines: tuple[str, ...], printing: Printing) -> list[str]:
    """Every party value on this page, as the page printed it."""
    return [one.printed for one in values_for(lines, PARTY_LABELS, printing=printing)]


# ---- the separator, tolerated ------------------------------------------------


def test_a_colon_the_engine_read_as_a_question_mark_still_finds_the_party() -> None:
    """THE MEASURED DEFECT. `tesseract` reads GT-0041's `SUPPLIER:` as
    `SUPPLIER?`, and one character of punctuation threw away a supplier the
    engine had otherwise located on the page."""
    line = ("SUPPLIER? AQUANCED PROPULSION CENTRE UK LTO",)

    assert printed(line, Printing.READ_OFF_A_PHOTOGRAPH) == [
        "AQUANCED PROPULSION CENTRE UK LTO"
    ]


@pytest.mark.parametrize("printing", list(Printing))
def test_a_colon_that_survived_the_page_is_matched_under_either_printing(
    printing: Printing,
) -> None:
    """The tolerance ADDS readings; it never removes one. A colon is a colon on
    both tiers, and a change that read a photograph by no longer reading a PDF
    would be a trade rather than a fix."""
    line = ("SUPPLIER: SHARMA TRADERS",)

    assert printed(line, printing) == ["SHARMA TRADERS"]


def test_the_marks_the_corpus_engine_actually_produced_are_all_tolerated() -> None:
    """MEASURED over the twenty corpus PNGs: where the truth prints `SUPPLIER:`
    the engine produced `:` five times, `S` eight times, `!` twice, `®` twice,
    `?` once and `'` once. Every one of those that is a MARK is here. The eight
    `S` are not, and the test below is why."""
    marks = ("?", "!", "®", "'", ";", ".", "”")

    for mark in marks:
        assert printed(
            (f"SUPPLIER{mark} SHARMA TRADERS",), Printing.READ_OFF_A_PHOTOGRAPH
        ) == ["SHARMA TRADERS"], mark


def test_the_value_comes_back_exactly_as_the_page_said_it_and_never_mended() -> None:
    """`AQUANCED` is a misread `ADVANCED` and this reader must not know that.
    Correcting a value is inventing data - the whole defect
    `adapter.TYPED_TEXT_MIME` records, in a politer costume. The label is
    matched; whatever followed it is handed back character for character."""
    line = ("SUPPLIER? AQUANCED PROPULSION CENTRE UK LTO",)

    assert printed(line, Printing.READ_OFF_A_PHOTOGRAPH) == [
        "AQUANCED PROPULSION CENTRE UK LTO"
    ]
    assert "ADVANCED" not in printed(line, Printing.READ_OFF_A_PHOTOGRAPH)[0]


# ---- the controls, which are the point ---------------------------------------


def test_the_control_a_mangled_separator_is_refused_on_a_text_layer() -> None:
    """THE CONTROL THAT SCOPES THE TOLERANCE. A PDF's bytes say `SUPPLIER:`
    exactly, so a question mark there is a document that says something else.
    If this ever passes, the tolerance has gone global and the text-layer tier
    has been made to guess."""
    line = ("SUPPLIER? AQUANCED PROPULSION CENTRE UK LTO",)

    assert printed(line, Printing.EXACT_CHARACTERS) == []


@pytest.mark.parametrize("printing", list(Printing))
def test_a_longer_word_that_starts_with_the_label_is_never_a_label(
    printing: Printing,
) -> None:
    """THE CONTROL ON HOW WIDE THE TOLERANCE IS. A separator may be a mark and
    may never be a letter, because a letter after the label is part of a longer
    word. MEASURED: the engine turns `SUPPLIER:` into `SUPPLIERS` on eight of
    the twenty corpus PNGs, so accepting a letter here would read those eight
    AND read this heading as a supplier. Eight readings are not worth it."""
    assert printed(("SUPPLIERS OF FINE GOODS",), printing) == []


@pytest.mark.parametrize("printing", list(Printing))
def test_a_mark_inside_a_word_does_not_split_the_word_into_a_field(
    printing: Printing,
) -> None:
    """A separator separates. `SUPPLIER-MANAGED STOCK` is one word carrying a
    hyphen, and reading `MANAGED STOCK` off it would put a stock heading in the
    supplier field of somebody's ledger."""
    assert printed(("SUPPLIER-MANAGED STOCK",), printing) == []
    assert printed(("SUPPLIER/CUSTOMER DETAILS",), printing) == []


def test_the_printing_has_no_default_and_is_never_inferred() -> None:
    """`cage/decision.Moment` makes this argument and it is the same argument: a
    tier that can be guessed at will be guessed at wrongly, and the wrong guess
    here is a PDF quietly reading like a photograph. A caller who does not say
    gets a TypeError, not a default."""
    with pytest.raises(TypeError):
        values_for(("SUPPLIER: SHARMA TRADERS",), PARTY_LABELS)  # type: ignore[call-arg]


# ---- the control that matters most: the twenty corpus PDFs -------------------


def corpus(kind: str) -> list[dict[str, object]]:
    """Every committed case of one input type, in case-id order."""
    cases = [json.loads(path.read_text()) for path in sorted(CASES.glob("*.json"))]
    return [case for case in cases if case["input_type"] == kind]


def scored(kind: str, backend: str) -> dict[str, tuple[int, int, int]]:
    """`exact, read-but-wrong, unread` per named field, for one rung.

    The comparison is `scripts/run_ground_truth.field_matches`: exact, no
    tolerance and no normalisation. A near miss is a miss, and a value with a
    source on it that is not the truth is counted apart from a refusal - those
    are different events and merging them hides which one happened.
    """
    reader = build(backend)
    tally = {name: [0, 0, 0] for name in ExtractedRecord.FIELDS}
    for case in corpus(kind):
        want: dict[str, object] = case["expected"]  # type: ignore[assignment]
        record = reader.extract(
            (DOCUMENTS / str(case["document"])).read_bytes(), str(case["mime"])
        )
        for name in ExtractedRecord.FIELDS:
            got = getattr(record, name)
            spoke = not record.per_field_source.get(name, NOT_FOUND).startswith(
                NOT_FOUND
            )
            tally[name][0 if truthful(name, got, want) else 1 if spoke else 2] += 1
    return {name: (a, b, c) for name, (a, b, c) in tally.items()}


def truthful(name: str, got: object, want: dict[str, object]) -> bool:
    if got is None:
        return False
    if name == "date":
        return str(got) == str(want["date"])
    if name == "party":
        return got == want["party"]
    stated = want["total_amount"] if name == "total_paise" else want["tax_amount"]
    return got == int(Decimal(str(stated)) * 100)


def test_the_control_the_twenty_corpus_pdfs_read_exactly_as_they_did() -> None:
    """THE CONTROL THAT MATTERS MOST. The text-layer tier's value is that it is
    never wrong, and a change made for photographs that cost one PDF field
    would be a bad trade at any exchange rate.

    MEASURED 2026-08-13 before the tolerance landed and again after, with these
    same numbers both times. The six unread dates are `GT-0034` and its five
    siblings printing an ambiguous `06/10/2026`, refused rather than guessed -
    `tests/test_textlayer.py` owns that argument."""
    assert scored("PDF", "pdf_text_layer") == {
        "date": (14, 0, 6),
        "party": (20, 0, 0),
        "total_paise": (20, 0, 0),
        "tax_paise": (20, 0, 0),
    }


# ---- through the whole reader, on the picture that started this --------------


@NEEDS_THE_ENGINE
def test_gt_0041_comes_back_read_and_wrong_rather_than_unread() -> None:
    """THE OUTCOME THIS CHANGE IS FOR, and it is a WRONG answer on purpose.

    The page says `AQUANCED PROPULSION CENTRE UK LTO`; the truth is `ADVANCED
    PROPULSION CENTRE UK LTD`. Nothing here mends it. What changed is that the
    misreading is now VISIBLE - a value with a confidence on it that
    `cage/decision.py` can block or ask about - instead of a silent nothing."""
    seen = FreeReader(page_reader(deadline_seconds=DEADLINE)).observe(
        (DOCUMENTS / "GT-0041.png").read_bytes(), PNG
    )
    truth = json.loads((CASES / "GT-0041.json").read_text())["expected"]["party"]

    assert seen.party.value == "AQUANCED PROPULSION CENTRE UK LTO"
    assert seen.party.value != truth


@NEEDS_THE_ENGINE
def test_a_field_the_tolerance_recovered_never_carries_the_text_layer_exactness() -> (
    None
):
    """THE SINGLE MOST DANGEROUS THING THAT COULD BREAK HERE. `cage/decision.py`
    auto-posts at 0.95, so a field the tolerance recovered wearing
    `confidence.EXACT` would post `AQUANCED PROPULSION CENTRE UK LTO` to a real
    ledger with nothing on screen to notice. It is the engine's own per-word
    score, and the engine is not sure."""
    seen = FreeReader(page_reader(deadline_seconds=DEADLINE)).observe(
        (DOCUMENTS / "GT-0041.png").read_bytes(), PNG
    )

    assert 0.0 < seen.party.confidence < EXACT


# =============================================================================
# The four rules that refused every real bill, 2026-08-15
# =============================================================================
#
# Traced in docs/OCR_0_CHARS_DIAGNOSIS.md. `amount_on` used a bare `re.match`
# with no case flag while `_values_on` three lines above already used
# `_LABEL_AT`. One reader, two rules about where a label may sit, and only the
# money path had the strict one. The synthetic corpus never caught it because
# its generator prints `TOTAL:` in capitals at column 0.


def test_a_lowercase_label_is_read() -> None:
    """RULE 1. `Total: 500.00` is how a real bill prints it; capitals are the
    exception. This refused, and the field came back not_found rather than
    unparseable, so nothing downstream could tell the two apart."""
    assert amounts_for(("Total: 500.00",), TOTAL_LABELS)[0].paise == 50000
    assert amounts_for(("total: 500.00",), TOTAL_LABELS)[0].paise == 50000


def test_a_label_indented_into_a_column_is_read() -> None:
    """RULE 2. `re.match` anchors at position 0, so two leading spaces refused
    the line. Every bill that prints its totals inside a table indents them."""
    assert amounts_for(("  TOTAL: 500.00",), TOTAL_LABELS)[0].paise == 50000


def test_rupees_spelled_the_way_india_spells_it_is_read() -> None:
    """RULE 3. `CURRENCY` is written in capitals and `_ONLY_AMOUNT` carried no
    IGNORECASE, so `Rs.` failed the whole line while `RS.` passed. `Rs.` is the
    ordinary spelling and `RS.` is the rare one."""
    assert amounts_for(("TOTAL Rs. 500.00",), TOTAL_LABELS)[0].paise == 50000
    assert amounts_for(("Total Rs. 1,23,456.00",), TOTAL_LABELS)[0].paise == 12345600


def test_the_column_gap_rule_is_reused_and_not_loosened() -> None:
    """THE CONTROL FOR RULES 1-3. A single space before the label still refuses.
    `_LABEL_AT` is what stops the second field on a two-field line being read
    into the first one's value, and widening it to `\\s+` would trade a missing
    total for a wrong one. A wrong total is the worse failure."""
    assert amounts_for(("X TOTAL: 500.00",), TOTAL_LABELS) == ()


def test_subtotal_is_still_never_read_as_the_total() -> None:
    """THE CONTROL THAT MATTERS MOST, and its first version was VACUOUS.

    Every string it tried failed at the LABEL stage, so the amount rule it
    claimed to guard was never reached, and it therefore did not notice that one
    extra space defeated the whole thing. An adversarial sweep found it. The
    double-spaced spellings below are the ones that actually exercise the rule -
    they came back as TOTAL until `amount_on` was re-anchored:

        'Sub  Total  278.61'   on a bill whose real total is 319.00

    posts ₹278.61 and loses exactly the ₹40.39 of tax. A control that cannot
    fail is not a control, and this one could not."""
    single_spaced = ("SUBTOTAL: 100.00", "Subtotal: 100.00", "Sub Total: 100.00")
    column_gapped = (
        "SUB  TOTAL: 100.00",
        "Sub  Total  100.00",
        "SUB   TOTAL   1,000.00",
        "sub  total 100.00",
        "Item  Total: 100.00",
        "Bill  Total 250.00",
        "Food  Total : 525.00",
    )

    for line in single_spaced + column_gapped:
        assert amounts_for((line,), TOTAL_LABELS) == (), line


def test_a_registration_number_after_a_column_gap_is_not_tax() -> None:
    """MEASURED on data/real_invoices/gov-and-open-data-092.pdf, which prints
    `(FEIN) 132932696          GST 895524239`. Read as an amount that is
    ₹8,95,52,439 of tax on a document that states no such figure. GSTIN, HSN and
    FEIN codes are long bare integers and they sit after column gaps constantly,
    so a label that may match mid-line turns every one of them into money."""
    assert amounts_for(("(FEIN) 132932696          GST 895524239",), TAX_WHOLE) == ()
    assert amounts_for(("Invoice 4417        TAX 998311",), TAX_WHOLE) == ()
    assert amounts_for(("HSN 998311        GST 18",), TAX_WHOLE) == ()


def test_a_number_that_is_not_an_amount_is_still_refused() -> None:
    """THE OTHER CONTROL. `TAX INVOICE 2026` must not report 2026 rupees of
    tax. Relaxing the label rules must not relax what counts as an amount."""
    assert amounts_for(("TAX INVOICE 2026",), TOTAL_LABELS) == ()
    assert amounts_for(("TOTAL PAGES 3 OF 4",), TOTAL_LABELS) == ()


def test_the_indian_whole_rupee_suffix_is_read() -> None:
    """`₹45,61,546/-` is how an Indian document writes a whole-rupee sum, and it
    was refused outright - the trailing two characters failed `$`, so the line
    reported NO amount rather than an unparseable one. MEASURED in this
    repository's own corpus: data/real_invoices_indian/gst-portal-and-govt-002.pdf
    prints `total liability of ₹45,61,546/-`."""
    assert amounts_for(("Total Rs 2,076.76 /-",), TOTAL_LABELS)[0].paise == 207676
    assert amounts_for(("Total ₹45,61,546/-",), TOTAL_LABELS)[0].paise == 456154600


def test_the_suffix_is_discarded_and_never_read_into_the_figure() -> None:
    """`/-` says only "and no paise", which the digits already said. The same
    figure written both ways must give the same paise, or the suffix has become
    part of the number."""
    bare = amounts_for(("Total 2,076.76",), TOTAL_LABELS)[0].paise
    suffixed = amounts_for(("Total 2,076.76/-",), TOTAL_LABELS)[0].paise
    assert bare == suffixed == 207676


def test_a_lone_slash_is_not_the_rupee_suffix() -> None:
    """THE CONTROL. `/-` is one token; a bare `/` between two numbers is a range
    or a ratio and states no single amount. Accepting it would read `500 / 600`
    as five hundred rupees."""
    assert amounts_for(("Total 500 / 600",), TOTAL_LABELS) == ()


def test_a_total_row_of_a_table_is_refused_rather_than_guessed() -> None:
    """MEASURED on data/real_invoices/vendor-samples-050.pdf, a real Indian
    retail invoice, whose total row reads `Total 1 278.61 40.39 319.00` -
    quantity, net, tax, gross. Four numbers and no way to tell which is the
    total without reading the column headings. Picking one would be F-02, a
    confident wrong amount, which is the failure this repository exists to
    prevent. A refusal here is the correct answer and must stay one."""
    assert amounts_for(("Total 1 278.61 40.39 319.00",), TOTAL_LABELS) == ()
    assert amounts_for(("Grand Total",), TOTAL_LABELS) == ()


# =============================================================================
# DATE_LABEL IS A FAMILY NOW, AND A STRING IS ALSO A FAMILY OF ITS LETTERS
# =============================================================================
#
# `DATE_LABEL` was a bare string until 2026-08-15 and is now a tuple, so a bill
# printing `Invoice Date:` is read the same way as one printing `Date:`. Every
# other label constant in this file was already a tuple; the date was the odd
# one out.
#
# THE HAZARD IS NARROW AND IT IS MEASURED, NOT IMAGINED. Three call sites
# (`ladder.BILL_LABELS`, `pagereader.read_page`, `textlayer._read_date`) hand
# this constant to something that wants an ITERABLE OF LABELS. A string is one -
# of characters - so a regression to `DATE_LABEL = "DATE"` raises nowhere. It
# silently becomes the four labels `D`, `A`, `T`, `E`.
#
# What that actually costs, measured rather than assumed. The separator rule
# already refuses a letter after a label, so `D` does NOT match every line:
#
#     values_for(('Delivered to Rao Traders',), ('D',))   -> ()      both tiers
#     values_for(('D: 28/01/26',),              ('D',))   -> '28/01/26'
#     values_for(('Rao D. Traders 500',),       ('D',))   -> 'Traders 500'
#                                                            PHOTOGRAPH tier only
#
# The third line is the damage: a middle initial in a supplier's name reads a
# company as a date. Narrower than "matches everything", and still a wrong read
# produced from a page that said nothing of the kind.
#
# WHAT THE WIDENING BUYS TODAY: NOTHING, AND THAT IS NOT THE VOCABULARY'S FAULT.
# All four new spellings find their value and then lose it one layer down -
# `looks_like_a_date` is ISO-only, so every `01/04/2026` it is handed comes back
# refused as ambiguous. Measured through `textlayer._read_date`, all four:
#
#     'Invoice Date: 01/04/2026' -> (None, "...is ambiguous (could be 1 April
#                                    or 4 January)...")
#
# So these labels are correct and currently inert. That is worth writing down
# rather than discovering later as a surprise: the date family is not what is
# holding dates back, and widening it further will not move the number.


def test_date_label_is_a_tuple_of_whole_labels_and_never_a_bare_string() -> None:
    """THE GUARD. A string satisfies every `Iterable[str]` annotation in this
    package and then behaves as its own characters, so the container is the
    thing to assert - not just the contents."""
    assert isinstance(DATE_LABEL, tuple)
    assert not isinstance(DATE_LABEL, str)
    assert DATE_LABEL, "an empty family matches nothing and reads no dates"
    for label in DATE_LABEL:
        assert isinstance(label, str)
        assert len(label) > 1, (
            f"{label!r} is one character. Either a real single-letter label was "
            "added, or DATE_LABEL became a string again and this is one of its "
            "letters."
        )


def test_the_date_label_that_shipped_before_the_family_still_reads() -> None:
    """COMPATIBILITY, in the direction that matters. `DATE` is what the
    text-layer tier has matched since the beginning and what its 14/20 on the
    corpus PDFs is made of. Widening a vocabulary must never narrow it."""
    assert "DATE" in DATE_LABEL
    found = values_for(
        ("Date : 28/01/26",), DATE_LABEL, printing=Printing.EXACT_CHARACTERS
    )
    assert [one.printed for one in found] == ["28/01/26"]


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("Invoice Date: 01/04/2026", "01/04/2026"),
        ("Bill Date: 01/04/2026", "01/04/2026"),
        ("Dated: 01/04/2026", "01/04/2026"),
        ("Supply Date: 01/04/2026", "01/04/2026"),
    ],
)
def test_each_spelling_the_family_was_widened_for_locates_its_value(
    line: str, expected: str
) -> None:
    """Each of the four that joined `DATE` finds the figure. This is a
    vocabulary test and NOT a claim that the date is then read - see the test
    below, which is the one that says what this actually buys."""
    found = values_for((line,), DATE_LABEL, printing=Printing.EXACT_CHARACTERS)
    assert expected in [one.printed for one in found]


def test_a_longer_spelling_matches_twice_and_the_reader_survives_it() -> None:
    """`Invoice Date:` matches BOTH `DATE` and `INVOICE DATE`, so the same
    figure is located twice. MEASURED, and worth pinning: `the_one` treats two
    copies of one value as one answer rather than as a contradiction. If that
    ever changes, every long date spelling starts refusing itself."""
    found = values_for(
        ("Invoice Date: 01/04/2026",), DATE_LABEL, printing=Printing.EXACT_CHARACTERS
    )
    assert [one.printed for one in found] == ["01/04/2026", "01/04/2026"]


def test_the_widened_family_buys_no_extra_dates_until_the_parser_changes() -> None:
    """WHAT IT IS WORTH TODAY, WRITTEN DOWN SO NOBODY CLAIMS OTHERWISE.

    Every spelling above locates its value and then loses it: `looks_like_a_date`
    reads ISO only, so `01/04/2026` - the form every Indian bill prints - comes
    back refused as ambiguous. The labels are correct and currently inert.

    This test asserts the CURRENT rule, not a desirable one. The day the owner
    rules on DD/MM/YYYY it is expected to fail, and that failure is the reminder
    to re-measure the date count rather than assume it moved.
    """
    from accountant.extract.textlayer import (
        _read_date,  # pyright: ignore[reportPrivateUsage]
    )

    for line in (
        "Invoice Date: 01/04/2026",
        "Bill Date: 01/04/2026",
        "Dated: 01/04/2026",
        "Supply Date: 01/04/2026",
    ):
        read, why = _read_date((line,))
        assert read is None, line
        assert "ambiguous" in why, why


def test_the_control_a_single_letter_label_reads_a_name_as_a_value() -> None:
    """THE CONTROL, and the reason the type test above is not ceremony.

    If `DATE_LABEL` ever unpacks to characters, `D` becomes a label. MEASURED:
    it does not match everything, because a letter may not be a separator - but
    on the photograph tier a middle initial IS a separator, and a supplier
    called `Rao D. Traders` reads as a date of `Traders 500`.

    Both halves are asserted, so the control cannot go vacuous: the letters are
    absent from the family AND a letter label demonstrably reads something.
    """
    for letter in ("D", "A", "T", "E"):
        assert letter not in DATE_LABEL

    damage = values_for(
        ("Rao D. Traders 500",), ("D",), printing=Printing.READ_OFF_A_PHOTOGRAPH
    )
    assert [one.printed for one in damage] == ["Traders 500"], (
        "the control is vacuous: a single-letter label now reads nothing, so "
        "the assertions above no longer guard anything"
    )


def test_a_family_nested_inside_another_tuple_is_named_rather_than_crashed() -> None:
    """THE GUARD THAT WOULD HAVE SAVED 98 TESTS.

    `DATE_LABEL` became a tuple and one of its three call sites still wrapped it:
    `values_for(lines, (DATE_LABEL,), ...)`. Correct for a string, a nested tuple
    after, and invisible to the type checker - the parameter says
    `tuple[str, ...]` and a `tuple[tuple[str, ...]]` is still a tuple.

    MEASURED, what it did before this check: `TypeError: decoding to str: need a
    bytes-like object, tuple found`, raised inside `re.escape`, six frames below
    the call and naming nothing the caller wrote. Every date on every tier
    stopped reading. The message now says which argument and what to write.
    """
    with pytest.raises(TypeError, match="family of labels"):
        values_for(
            ("Date: 28/01/26",),
            (DATE_LABEL,),  # pyright: ignore[reportArgumentType]
            printing=Printing.EXACT_CHARACTERS,
        )


def test_the_three_call_sites_pass_the_family_whole() -> None:
    """The check above fires at RUNTIME, so it only helps on a line somebody
    runs. This is the static half: no module may wrap `DATE_LABEL` in a tuple.

    Grepping source is a blunt instrument and it is the right one here - the
    defect was a two-character edit that no import, no signature and no type
    survives being asked about.

    A CALL, not a mention. Both halves must be on the line, and a comment does
    not count - the first version of this test failed on the paragraph in
    `labels.py` that explains the bug, which is a guard measuring its own
    documentation rather than the code.
    """
    package = REPO / "accountant"
    wrapped = [
        f"{path.relative_to(REPO)}:{number}"
        for path in package.rglob("*.py")
        for number, line in enumerate(path.read_text().splitlines(), start=1)
        if "values_for(" in line
        and "(DATE_LABEL,)" in line
        and not line.lstrip().startswith("#")
    ]
    assert wrapped == [], (
        f"DATE_LABEL is a family and these sites wrap it in another tuple: "
        f"{wrapped}. Pass it whole."
    )

    reading = [
        f"{path.relative_to(REPO)}"
        for path in package.rglob("*.py")
        if "DATE_LABEL" in path.read_text()
    ]
    assert len(reading) >= 3, (
        f"the control is vacuous: only {reading} read DATE_LABEL, so this test "
        "is scanning a package that no longer has the call sites it guards"
    )


# =============================================================================
# ONE SPACE WAS HIDING A LABEL THE PAGE PRINTED
# =============================================================================
#
# MEASURED 2026-08-15 by `scripts/classify_unmatched_slots.py` over the 60 real
# image documents: of 287 field slots that matched no label,
# LABEL_NORMALIZATION_FAILURE accounts for 17 - the label's own characters ARE
# on the page and the matcher refused them. The evidence, verbatim:
#
#     gst-portal-and-govt-004.jpg   net   page says 'SUB TOTAL',
#                                         and NET_LABELS holds 'SUBTOTAL'
#
# One space. The page and the vocabulary say the same word and the reader called
# it nothing.
#
# WHAT THIS IS NOT. It is not fuzzy matching. Every character of the label must
# still be present, in order, with nothing between them but whitespace. `SUBTOT`
# does not match `SUBTOTAL`, `SUBXTOTAL` does not, and no new label is invented.


def test_a_label_split_by_a_space_still_matches() -> None:
    """THE MEASURED DEFECT. `SUB TOTAL` on the page, `SUBTOTAL` in the
    vocabulary."""
    found = amounts_for(("SUB TOTAL: 1,046.24",), NET_LABELS)

    assert [one.paise for one in found] == [104624]


@pytest.mark.parametrize(
    "printed",
    [
        "SUBTOTAL: 1,046.24",
        "SUB TOTAL: 1,046.24",
        "SUB  TOTAL: 1,046.24",
        "SUB\tTOTAL: 1,046.24",
        "S U B T O T A L: 1,046.24",
    ],
)
def test_every_spacing_of_one_label_reads_the_same_figure(printed: str) -> None:
    """Repeated spaces, a tab, and a fully spaced-out heading. All of them are
    the same word printed by an engine that guessed differently about gaps."""
    found = amounts_for((printed,), NET_LABELS)

    assert [one.paise for one in found] == [104624], printed


def test_the_labels_that_already_matched_still_match() -> None:
    """THE CONTROL ON THE FIX. Widening how a label may be spaced must not
    change what any existing label reads."""
    assert amounts_for(("TOTAL 1,020.70",), TOTAL_LABELS)[0].paise == 102070
    assert amounts_for(("GRAND TOTAL: 1,234.56",), TOTAL_LABELS)[0].paise == 123456
    assert amounts_for(("GST 188.32",), TAX_WHOLE)[0].paise == 18832
    found = values_for(
        ("SUPPLIER: SHARMA TRADERS",), PARTY_LABELS, printing=Printing.EXACT_CHARACTERS
    )
    assert [one.printed for one in found] == ["SHARMA TRADERS"]


def test_a_subtotal_is_still_never_the_total() -> None:
    """THE ONE THAT MATTERS MOST, and the reason this fix is scoped to spacing.

    `SUB TOTAL` must reach the NET family and never the TOTAL family. A bill
    whose subtotal was read as its total posts short by exactly its tax - the
    defect `cage/gate._lines_add_up_to` was written against, and the thing a
    looser matcher would reintroduce here.
    """
    net = amounts_for(("SUB TOTAL: 1,046.24",), NET_LABELS)
    assert [one.paise for one in net] == [104624]

    both = ("SUB TOTAL: 1,046.24", "GRAND TOTAL: 1,234.56")
    assert amounts_for(both, NET_LABELS)[0].paise == 104624
    assert 104624 not in [one.paise for one in amounts_for(both, TOTAL_LABELS)]


def test_the_spacing_rule_does_not_match_a_word_that_is_not_the_label() -> None:
    """THE CONTROL THAT KEEPS THIS FROM BEING FUZZY MATCHING.

    Every character of the label must be present, in order, separated by nothing
    but whitespace. A missing character, an extra character, or a different
    character is a different word and must stay unmatched.
    """
    assert amounts_for(("SUBTOT: 1,046.24",), NET_LABELS) == ()
    assert amounts_for(("SUBXTOTAL: 1,046.24",), NET_LABELS) == ()
    assert amounts_for(("SUB-TOTAL-ISH: 1,046.24",), NET_LABELS) == ()
