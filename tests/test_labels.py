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
    PARTY_LABELS,
    Printing,
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
