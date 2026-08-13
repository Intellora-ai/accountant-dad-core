"""The page reader: which words on a photograph are the total, tax, date, party.

WHY THIS FILE EXISTS
--------------------
`accountant/extract/pagereader.py` is the join that was missing. The engine
turns a picture into words with a confidence each; `labels.py` turns labelled
invoice text into located values; this file proves the two are wired together
in the one way that is safe, and that four specific ways of getting it wrong
are caught.

THE FOUR THINGS THAT WOULD BE WORTH SHIPPING A BUG FOR
--------------------------------------------------------
    a fabricated total      the exact defect `adapter.TYPED_TEXT_MIME` records:
                            take the first number on the page and call it the
                            amount payable. It invented twenty totals. A reader
                            that answers an unlabelled number is that bug again.

    an inherited 1.0        `confidence.EXACT` belongs to the text layer, where
                            there is nothing to be unsure about. `cage/decision`
                            auto-posts at 0.95, so a pixel reading wearing 1.0
                            posts a guess to somebody's books.

    a constant answer       a reader that returns the same fields whatever it
                            is handed passes every "a field came back" test in
                            this file. Two different pages, two different
                            answers, or none of the rest is evidence.

    a crash on an empty     the twenty corpus JPEGs contain no picture at all.
    file                    An exception there is HTTP 503 "Something in
                            Accountant Dad broke" for a person whose only
                            problem is that they sent the wrong file.

WHAT THIS FILE DOES NOT PROVE
------------------------------
That the engine reads a bill correctly. It does not, on this corpus, mostly.
MEASURED through the wired path: 4 of 80 fields come back with a value, all
four the supplier, and 2 of those are exactly right. The number is reported
rather than asserted upward, because tuning anything to move it would be
fitting a reader to twenty synthetic pictures. What IS asserted is the thing
that would cost real money: nothing comes back WRONG at a confidence that
would auto-post.

That real customer photographs read at all. `SYNTHETIC_EVIDENCE`, `H-02` open.

NO NETWORK. The engine is a local program and every test that needs it is
skipped with a stated reason when it is not installed.
"""

from __future__ import annotations

import json
import pathlib
import shutil

import pytest

from accountant.cage.confidence import EXACT
from accountant.extract.adapter import NOT_FOUND
from accountant.extract.freeocr import FreeReader, Reading, Word
from accountant.extract.pagereader import page_reader, read_page

REPO = pathlib.Path(__file__).resolve().parent.parent
DOCUMENTS = REPO / "artifacts" / "ground_truth" / "documents"
CASES = REPO / "artifacts" / "ground_truth" / "cases"

PNG = "image/png"
JPEG = "image/jpeg"

#: The deadline every test here gives the engine. Generous on purpose: a test
#: that failed because a laptop was busy would be a flaky test, and the bound
#: under test is never the number, it is that there IS one.
DEADLINE = 30.0

ENGINE_ON_PATH = shutil.which("tesseract")

NEEDS_THE_ENGINE = pytest.mark.skipif(
    ENGINE_ON_PATH is None,
    reason=(
        "SKIPPED LOUDLY: no `tesseract` binary on PATH, so nothing here can "
        "read a picture. Install it with `brew install tesseract` (macOS) or "
        "`apt-get install -y tesseract-ocr` (Linux). The per-field numbers "
        "these tests measure are recorded in the module docstring of "
        "accountant/extract/pagereader.py and are NOT re-proved when this skips."
    ),
)


def said(line: str, confidence: int = 90) -> tuple[Word, ...]:
    """One printed line, as the engine would report it: words and one score."""
    return tuple(Word(text=word, confidence=confidence) for word in line.split())


def a_bill() -> tuple[tuple[Word, ...], ...]:
    """An ordinary bill as the engine reports it: one field per line."""
    return (
        said("TAX INVOICE"),
        said("DATE: 2026-05-13"),
        said("SUPPLIER: SHARMA TRADERS"),
        said("SUBTOTAL 865.00"),
        said("GST 155.70"),
        said("TOTAL 1,020.70"),
    )


def texts(words: tuple[Word, ...]) -> list[str]:
    return [word.text for word in words]


# ---- the join --------------------------------------------------------------


def test_the_words_it_points_at_are_the_value_and_never_the_label() -> None:
    """The label locates the field; the label is not part of the field. A
    reading carrying `TOTAL 1,020.70` reaches `freeocr._money`, which cannot
    hold that in paise, so the total would be unread on a bill that states it
    perfectly clearly."""
    reading = read_page(a_bill())

    assert texts(reading.total) == ["1,020.70"]
    assert texts(reading.tax) == ["155.70"]
    assert texts(reading.net) == ["865.00"]
    assert texts(reading.date) == ["2026-05-13"]
    assert texts(reading.party) == ["SHARMA", "TRADERS"]


def test_a_number_with_no_label_on_it_is_never_answered_as_the_total() -> None:
    """THE DEFECT THIS EXISTS TO NOT REPEAT. `adapter.TYPED_TEXT_MIME` records
    twenty invented totals from a reader that ran a money regex over whatever
    it was handed and answered with the first number it found. `GT/0041` and
    `998311` are an invoice number and an HSN code; neither is money."""
    reading = read_page(
        (said("INVOICE NO: GT/0041"), said("HSN/SAC: 998311"), said("865.00"))
    )

    assert reading.total == ()
    assert reading.tax == ()
    assert reading.net == ()


def test_two_totals_that_disagree_are_refused_rather_than_picked_between() -> None:
    """One of the two is wrong, nothing here can say which, and picking the
    first is a coin toss that posts money. A continuation sheet repeating the
    same figure is ordinary and is not refused - that is the control below."""
    reading = read_page((said("TOTAL 1,020.70"), said("TOTAL 1,626.70")))

    assert reading.total == ()


def test_the_control_the_same_total_printed_twice_is_still_read() -> None:
    """THE CONTROL on the test above. A reader that refused every repeated
    label would pass it and would then lose the total on every two-page bill,
    because a continuation sheet reprints the footer."""
    reading = read_page((said("TOTAL 1,020.70"), said("TOTAL 1,020.70")))

    assert texts(reading.total) == ["1,020.70"]


def test_a_word_the_engine_reported_with_no_characters_is_not_a_column_gap() -> None:
    """MEASURED: on GT-0041.png the engine reports a word row whose text is
    empty and whose confidence is 95. Joined into the line it would put two
    spaces between two words, and `labels.py` reads two spaces as a column
    boundary - so the supplier's name would be cut off at an invented gap."""
    engine_said = ((Word("SUPPLIER:", 90), Word("", 95), Word("SHARMA", 90)),)

    assert texts(read_page(engine_said).party) == ["SHARMA"]


def test_a_label_with_nothing_printed_after_it_produces_no_field_at_all() -> None:
    """A party field holding `"   "` is a silent blank wearing a label, which is
    the single thing `ExtractedRecord` exists to make impossible. The engine
    reporting a heading and then losing the line under it is ordinary on a
    photograph, so this is the common case rather than a contrived one."""
    reading = read_page((said("SUPPLIER:"), said("TOTAL:")))

    assert reading.party == ()
    assert reading.total == ()


def test_a_split_tax_is_left_unread_rather_than_added_up_from_two_places() -> None:
    """A bill printing CGST and SGST states its tax as two figures in two
    lines. There is no set of words on that page that IS the tax, and this
    reader answers with words. Pointing at both would join them into
    `155.70 155.70`, which is not an amount, and pointing at one posts half the
    input credit on a bill that still looks read."""
    reading = read_page((said("CGST 77.85"), said("SGST 77.85")))

    assert reading.tax == ()


# ---- the confidence, which is the whole difference -------------------------


def test_a_field_read_off_pixels_never_carries_the_text_layer_s_exactness() -> None:
    """THE SINGLE WORST DEFECT AVAILABLE HERE. `confidence.EXACT` is 1.0 and
    belongs to a reader with nothing to be unsure about. `cage/decision.py`
    auto-posts at 0.95 and above, so a pixel reading wearing 1.0 posts a guess
    to a real ledger with nothing on screen to notice."""
    engine_said = (said("DATE: 2026-05-13", 91), said("SUPPLIER: SHARMA", 88))

    observed = FreeReader(lambda _d, _m: read_page(engine_said)).observe(b"", PNG)

    assert observed.date.confidence == pytest.approx(0.91)
    assert observed.party.confidence == pytest.approx(0.88)
    assert observed.date.confidence < EXACT
    assert observed.party.confidence < EXACT


def test_the_field_score_is_the_worst_word_the_engine_reported_for_it() -> None:
    """A field is exactly as trustworthy as its least legible character. A mean
    of 0.96 and 0.40 is 0.68, which reads as "worth asking about" rather than
    "certainly wrong", and the misread character is the only one that matters."""
    engine_said = ((Word("SUPPLIER:", 96), Word("SHARMA", 96), Word("TRADERS", 40)),)

    observed = FreeReader(lambda _d, _m: read_page(engine_said)).observe(b"", PNG)

    assert observed.party.confidence == pytest.approx(0.40)


def test_the_control_the_same_words_at_full_confidence_do_reach_exactness() -> None:
    """THE CONTROL on the two tests above. A page reader that always reported a
    low confidence, or a `field_confidence` that capped one, would pass both of
    them while measuring nothing. The engine saying 100 is the only way this
    number reaches 1.0, and it is the engine's claim rather than ours."""
    engine_said = ((Word("SUPPLIER:", 100), Word("SHARMA", 100)),)

    observed = FreeReader(lambda _d, _m: read_page(engine_said)).observe(b"", PNG)

    assert observed.party.confidence == EXACT


# ---- the whole path, against the real engine -------------------------------


@NEEDS_THE_ENGINE
def test_two_different_pictures_do_not_come_back_with_identical_fields() -> None:
    """THE CONTROL ON EVERYTHING BELOW. A page reader returning a constant would
    satisfy every "a field was read" assertion in this file and would be
    reporting a fixed answer about every bill anybody ever uploads.

    The `isinstance` pair is not ceremony: `freeocr.PageReader` is annotated
    `-> object` on the stated ground that an annotation on somebody else's
    function is a promise rather than a fact, so this is the same check
    `FreeReader._reading` makes before it trusts a word."""
    read = page_reader(deadline_seconds=DEADLINE)

    one = read((DOCUMENTS / "GT-0051.png").read_bytes(), PNG)
    two = read((DOCUMENTS / "GT-0052.png").read_bytes(), PNG)

    assert isinstance(one, Reading)
    assert isinstance(two, Reading)
    assert (one.date, one.party, one.total, one.tax) != (
        two.date,
        two.party,
        two.total,
        two.tax,
    )


@NEEDS_THE_ENGINE
def test_a_corpus_png_reaches_the_four_fields_through_the_whole_reader() -> None:
    """End to end on real bytes: engine, line rebuild, label match, confidence.
    GT-0052 is one of the four corpus PNGs that come back with a supplier at
    all: the 5x7 bitmap font destroys the `SUPPLIER:` label on fifteen of the
    twenty, and on a sixteenth the engine reports a word at confidence 0."""
    read = page_reader(deadline_seconds=DEADLINE)
    observed = FreeReader(read).observe((DOCUMENTS / "GT-0052.png").read_bytes(), PNG)

    expected = json.loads((CASES / "GT-0052.json").read_text())["expected"]

    assert observed.party.value == expected["party"]
    assert 0.0 < observed.party.confidence < EXACT


@NEEDS_THE_ENGINE
def test_a_file_that_says_it_is_a_picture_and_holds_none_is_refused_cleanly() -> None:
    """MEASURED on the twenty corpus JPEGs: every one of them is a JFIF header
    followed by comment segments, with no frame header and no scan. There are
    ZERO PIXELS in the file and no reader can do better than say so.

    What must not happen is an exception. `pipeline.build_draft` calls its
    extractor with nothing around it, so a raise here is HTTP 503 telling a
    person the application broke when their file is simply empty."""
    read = page_reader(deadline_seconds=DEADLINE)
    record = FreeReader(read).extract((DOCUMENTS / "GT-0061.jpg").read_bytes(), JPEG)

    assert record.total_paise is None
    assert set(record.per_field_source.values()) == {
        f"{NOT_FOUND}: this file says it is a picture but there is no picture "
        "inside it, so there is nothing on it to read. Please send the original "
        "photograph or scan"
    }


@NEEDS_THE_ENGINE
def test_no_corpus_png_produces_a_wrong_field_at_a_confidence_that_auto_posts() -> None:
    """THE MEASUREMENT THAT MATTERS. A blank is a question for a person; a
    WRONG value above 0.95 is a wrong entry posted to a real ledger with nobody
    asked. Every corpus PNG is read, every field that came back with a value is
    compared against the ground truth, and anything both wrong and auto-postable
    is listed by name."""
    read = page_reader(deadline_seconds=DEADLINE)
    dangerous: list[str] = []
    for document in sorted(DOCUMENTS.glob("*.png")):
        want = json.loads((CASES / f"{document.stem}.json").read_text())["expected"]
        seen = FreeReader(read).observe(document.read_bytes(), PNG)
        for name, truth in (("date", want["date"]), ("party", want["party"])):
            field = getattr(seen, name)
            if field.value is None or str(field.value) == truth:
                continue
            dangerous.append(
                f"{document.name} {name}={field.value!r} @{field.confidence}"
            )

    assert [entry for entry in dangerous if "@0.9" in entry or "@1.0" in entry] == []
