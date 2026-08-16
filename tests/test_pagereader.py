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

                            TRIED AND REVERTED 2026-08-15. The owner asked for
                            exactly that guess, capped at `BY_POSITION` so the
                            cage would block it. Measured over the twenty corpus
                            PNGs it produced 15 wrong totals and 0 right ones,
                            so it is gone and the guard is back.

    an UNMARKED guess       what survives is the DATE, found by position when no
                            label named one. A guess is allowed; a guess that
                            reaches a person looking identical to a labelled read
                            is not. The mark is `Reading.at_most`, it caps the
                            score at 0.5, and it renames the source.

                            The PARTY had the same fallback and lost it on
                            2026-08-15: measured, it added three answers to the
                            ground truth and three of them were wrong. A party is
                            an identity, and the ceiling that made it safe did
                            not make it useful.

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

import datetime
import json
import pathlib
import shutil

import pytest

from accountant.cage.confidence import EXACT
from accountant.cage.decision import ASK_FLOOR, AUTO_POST_ALLOWED_TIERS, AUTO_POST_FLOOR
from accountant.extract.adapter import ENTITLED_TO_EXACT, NOT_FOUND
from accountant.extract.freeocr import (
    A_GUESS,
    FreeReader,
    Reading,
    Word,
    _scored,  # pyright: ignore[reportPrivateUsage]
)
from accountant.extract.pagereader import BY_POSITION, page_reader, read_page

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
    `998311` are an invoice number and an HSN code; neither is money.

    THIS GUARD WENT RED FOR ABOUT AN HOUR ON 2026-08-15 AND WAS PUT BACK BY
    MEASUREMENT, NOT BY OPINION. The owner asked for a positional total: the
    largest amount in the last ten lines, when no label matched. It was built,
    and `scripts/run_ground_truth.py` scored it over the twenty corpus PNGs:

        before   total_paise   0 exact,  0 WRONG, 20 refused
        after    total_paise   0 exact, 15 WRONG,  5 refused

    Fifteen wrong money answers and not one right one. `pagereader.read_page`
    carries the revert and the reasoning; this line is what it restores.

    THE PARTY FALLBACK WENT THE SAME WAY, LATER THE SAME DAY. It survived the
    total's measurement and then failed its own: party WRONG rather than unread
    went 5 -> 8, three answers added and three of them wrong. The date is the
    only positional read still consulted. See
    `test_a_party_is_never_guessed_from_where_it_sits_on_the_page`.
    """
    reading = read_page(
        (said("INVOICE NO: GT/0041"), said("HSN/SAC: 998311"), said("865.00"))
    )

    assert reading.total == ()
    assert reading.tax == ()
    assert reading.net == ()
    assert "total" not in reading.at_most


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


# =============================================================================
# THE POSITIONAL FALLBACK - owner decision 2026-08-15
# =============================================================================
# A field found by POSITION is a guess. These prove the four things that have to
# be true for a guess to be allowed anywhere near somebody's books: it is only
# ever a fallback, it is marked, the mark cannot be turned into a better score
# than the engine gave, and no tier that can auto-post can produce one.


def test_a_positional_find_never_replaces_a_labelled_one() -> None:
    """FALLBACK, NOT OVERRIDE. If a guess could beat a label, this change would
    make a bill that reads correctly today read worse tomorrow - which is the
    only way it could cost anything on a document that already works."""
    reading = read_page(a_bill())

    assert texts(reading.total) == ["1,020.70"]
    assert texts(reading.party) == ["SHARMA", "TRADERS"]
    assert texts(reading.date) == ["2026-05-13"]
    assert reading.at_most == {}


def test_a_labelled_total_that_disagrees_with_itself_is_not_then_guessed_at() -> None:
    """THE MEASURED DEFECT IN THE FIRST DRAFT OF THIS FALLBACK.

    `the_one` refuses two disagreeing printings of a labelled total on purpose.
    Written as `if not total`, the fallback read that refusal as an absence and
    filled it with the LARGER of the two - the coin toss that
    `test_two_totals_that_disagree_are_refused_rather_than_picked_between`
    exists to forbid, arriving through a new door. The condition is "no label
    matched", and a label that matched and contradicted itself is not that.
    """
    reading = read_page((said("TOTAL 1,020.70"), said("TOTAL 1,626.70")))

    assert reading.total == ()
    assert "total" not in reading.at_most


def test_a_date_split_across_words_comes_back_whole() -> None:
    """`15 Aug 2026` is THREE words to the engine. Answering with the word the
    match starts in hands `freeocr._joined` the single word `15`, and the field
    becomes a fragment wearing the confidence of one legible character."""
    reading = read_page((said("SUNIL TRADING COMPANY"), said("15 Aug 2026")))

    assert texts(reading.date) == ["15", "Aug", "2026"]


def test_a_non_iso_date_reads_when_arithmetic_settles_its_order() -> None:
    """REQUIREMENT CHANGED 2026-08-15, by owner ruling, and this test with it.

    It used to assert that `13/05/2026` is FOUND and then REFUSED, and gave as
    its reason that "`freeocr._read_date` is `date.fromisoformat` and nothing
    else". That was an accurate description of the code and a bad rule: it
    refused every Indian bill date on the grounds that it was not American
    ISO. The owner's requirement is `16-11-2023` reads as `2023-11-16`.

    NOTHING IS NORMALISED HERE, which is the part of the old docstring that was
    right and still is. `read_page` does not rewrite `13/05/2026`; it hands the
    characters to `extract.dates.read_date`, which reads them because 13 IS NOT
    A MONTH. Arithmetic settles the order. No convention was applied and none
    was needed - see `_read_date`, which passes `DateLocale.UNKNOWN` precisely
    so that a date only two orders could read stays refused.

    THE EVIDENCE SAYS SOMETHING DIFFERENT ON EACH PATH, and the first draft of
    this test got it wrong. On a REFUSAL the source quotes the characters,
    because the refusal reason is about them. On a READ the source names HOW
    the field was found. Here that is `guessed from where it sits on the page,
    not from a label` - this fake page prints no DATE label, so the positional
    fallback found it, and the ceiling holds the score at 0.5, below
    `ASK_FLOOR`. A date read this way cannot post and cannot even spend a
    question, which is the cage doing its job on a widened reader.
    """
    scored = _scored(read_page((said("SUNIL TRADING"), said("13/05/2026"))), "free_ocr")

    assert scored.date == datetime.date(2026, 5, 13)
    assert scored.confidences["date"] == 0.5
    assert "not from a label" in scored.sources["date"]


def test_a_date_both_orders_could_read_is_still_refused_not_picked() -> None:
    """THE HALF OF THE OLD TEST THAT MUST SURVIVE THE WIDENING.

    `11/08/2026` is the 11th of August and the 8th of November, both real days.
    Widening which SHAPES are read must not widen how sure the reader has to
    be, and this is the assertion that says so. It fails the moment someone
    passes a locale into `_read_date` to lift the corpus number.
    """
    scored = _scored(read_page((said("SUNIL TRADING"), said("11/08/2026"))), "free_ocr")

    assert scored.date is None
    assert "11/08/2026" in scored.sources["date"]


def test_a_stated_ceiling_can_lower_a_score_and_can_never_raise_one() -> None:
    """DOWNWARD ONLY. A ceiling that could also raise would be a way for a
    reader to launder a bad read into a good score, which is the one thing
    `field_confidence` already refuses when it takes `min(word_confidences)`."""
    words = (Word("865.00", 40),)
    engine_alone = _scored(Reading(total=words), "free_ocr")
    with_a_high_ceiling = _scored(Reading(total=words, at_most={"total": 0.99}), "x")

    assert engine_alone.confidences["total_paise"] == 0.4
    assert with_a_high_ceiling.confidences["total_paise"] == 0.4


def test_a_positional_find_cannot_reach_the_auto_post_band() -> None:
    """THE ONE THAT DECIDES WHETHER THE POSITIONAL READ IS SAFE.

    THREE independent walls, and the test asserts all three rather than
    trusting any of them. `BY_POSITION` is below `AUTO_POST_FLOOR`, so the band
    refuses it; it is below `ASK_FLOOR` too, so the product does not even spend
    a question on it; and `free_ocr` is in neither `ENTITLED_TO_EXACT` nor
    `AUTO_POST_ALLOWED_TIERS`, so the TIER refuses it whatever the number says.

    THE TIER WALLS ARE ASSERTED BECAUSE THE BAND IS A NUMBER SOMEBODY CAN MOVE.
    The day `free_ocr` is added to either allowlist - one line, one set - a
    guessed field would become evidence with nobody asked, which is failure mode
    F-03 and costs one supplier's balance for ever. This is the line that goes
    red on that day.

    IT ASKS THE DATE AND NOT THE PARTY, AS OF 2026-08-15. The positional PARTY
    was measured and disabled the same day - see
    `test_a_party_is_never_guessed_from_where_it_sits_on_the_page` below - and
    the date is the one positional read still consulted. The ceiling mechanism
    is what this test is about, and it is unchanged.
    """
    scored = _scored(read_page((said("2026-05-13"),)), "free_ocr")

    assert scored.date == datetime.date(2026, 5, 13)
    assert scored.confidences["date"] == BY_POSITION
    assert BY_POSITION < AUTO_POST_FLOOR
    assert BY_POSITION < ASK_FLOOR
    assert "free_ocr" not in ENTITLED_TO_EXACT
    assert "free_ocr" not in AUTO_POST_ALLOWED_TIERS


def test_a_positional_find_says_so_in_the_source_a_person_reads() -> None:
    """The score is what the cage reads; the source is what a PERSON reads, on
    the page and in the durable action log. A guess that looked identical to a
    labelled read in the audit line would be a guess nobody could audit.

    NOT A REFUSAL AND MUST NOT READ AS ONE: `cage/gate._was_read` treats any
    source beginning `NOT_FOUND` as an absence, so the mark is a suffix.
    """
    scored = _scored(read_page((said("2026-05-13"),)), "free_ocr")
    guessed, labelled = (
        scored.sources["date"],
        _scored(read_page(a_bill()), "free_ocr").sources["date"],
    )

    assert guessed != labelled
    assert labelled == "free_ocr"
    assert guessed.startswith("free_ocr") and A_GUESS in guessed
    assert not guessed.startswith(NOT_FOUND)


def test_a_party_is_never_guessed_from_where_it_sits_on_the_page() -> None:
    """THE PARTY FALLBACK, MEASURED AND TURNED OFF. Owner instruction 2026-08-15.

    A party is an IDENTITY, and it is the one field where being confidently
    wrong costs a supplier's balance for ever rather than one bill. Measured
    through `scripts/run_ground_truth.py` when the fallback was live:

        party WRONG rather than unread    5  ->  8
        party EXACT                       unchanged

    Three answers added, three wrong, none right. The ceiling held - at 0.5
    nothing it produced could post - so no money was ever at risk. The cost was
    that 31 documents stopped saying "I read nothing" and started offering OCR
    noise as a supplier, spending the owner's five daily questions on it.

    THE THREE MEASURED STRINGS ARE THE FIXTURE. They are the actual output of
    the corpus 5x7 bitmap font coming apart, and they are here so that
    re-enabling the fallback fails on the exact evidence that closed it.
    """
    for garbage in ("TNoIte Noe eTvan42", "TNoIte Noe eTvonas", "Nolte Noe eTan6o"):
        scored = _scored(read_page((said(garbage),)), "free_ocr")

        assert scored.party is None, (
            f"{garbage!r} came back as a supplier. The positional party fallback "
            "is live again; it was measured at 3 wrong answers and 0 right ones."
        )
        assert scored.confidences["party"] == 0.0
        assert scored.sources["party"].startswith(NOT_FOUND)


def test_a_real_looking_name_is_refused_too_and_that_is_the_point() -> None:
    """THE CONTROL ON THE TEST ABOVE, and it is not decoration.

    Asserting only that garbage is refused would pass just as well if the
    fallback still ran and happened to reject those three strings for some
    unrelated reason - a length rule, a digit rule. `SUNIL TRADING COMPANY` is
    the fixture the fallback was BUILT to accept and did accept. Refusing it
    proves the mechanism is off rather than merely fussy.

    It also states the cost honestly: this is a real supplier name on a page,
    and the reader now says nothing about it. That is the trade the measurement
    bought, not a free win.
    """
    scored = _scored(read_page((said("SUNIL TRADING COMPANY"),)), "free_ocr")

    assert scored.party is None
    assert scored.confidences["party"] == 0.0


def test_a_ceiling_stated_for_a_field_that_does_not_exist_is_refused() -> None:
    """A ceiling keyed by a typo would be silently ignored, and a silently
    ignored ceiling is a guess whose marking was lost on the way in."""
    refused = FreeReader(lambda _d, _m: Reading(at_most={"totl": 0.5})).extract(
        b"", PNG
    )

    assert refused.total_paise is None
    assert "totl" in refused.per_field_source["total_paise"]


def test_a_ceiling_that_is_not_a_confidence_is_refused_rather_than_used() -> None:
    """`isinstance(True, int)` is True and `True` is 1.0 in arithmetic, so a
    flag passed where a ceiling belonged would read as `confidence.EXACT` - the
    one score a photograph may never claim."""
    refused = FreeReader(lambda _d, _m: Reading(at_most={"total": True})).extract(  # type: ignore[dict-item]
        b"", PNG
    )

    assert refused.total_paise is None
    assert refused.per_field_source["total_paise"].startswith(NOT_FOUND)
