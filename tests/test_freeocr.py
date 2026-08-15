"""The free reading engine's adapter: what it refuses, and what it scores.

WHY THIS FILE EXISTS
--------------------
`accountant/extract/freeocr.py` is the piece between an engine that reports a
confidence per WORD and a decision layer whose bands are written per FIELD.
Everything dangerous about that piece is in the edges, not in the happy path:

    the marker            the engine writes -1 on every row that carries no
                          score. Read as a number it is a confidence below
                          zero. MEASURED on the engine installed here: 19 of 43
                          rows on a clean synthetic invoice were markers.

    the missing program   a machine with no engine installed must get a
                          sentence, not a traceback. `pipeline.build_draft`
                          has no try around `extract`, and an exception there
                          is an HTTP 503 telling a person the application broke
                          when the truth is that a program is not installed.

    the bounded wait      an engine that never returns must become a refusal at
                          a bounded time, not a request that hangs.

    the contradiction     three amounts that do not add up are not two-thirds
                          right, and the fields they came from carry no value.

So most of what is below is failure. That is the point: the happy path is one
`min()` and the rest of the file is the reasons it is allowed to run.

WHAT THIS FILE DOES NOT PROVE
-----------------------------
That the engine reads a bill correctly. Determinism is not accuracy - an engine
that misreads the same digit the same way ten times running is exactly as wrong
and perfectly reproducible. Accuracy needs the labelled corpus (`H-02`) this
repository does not have, and no test here claims otherwise.

That a high score means a right answer. Failure mode F-02 is the engine
reporting 96 on a digit it got wrong. Nothing computed from the engine's own
opinion of itself can see that.

That the engine reaches a customer's document today. CORRECTED 2026-08-13
TWICE. This backend WAS in `registry._NEEDS_WIRING` for want of a page reader;
`accountant/extract/pagereader.py` now supplies one, so `registry.available()`
is asserted to be exactly SEVEN names and this is one of them, and the picture
rung of `ladder.py` is wired to it.

CORRECTED AGAIN, 2026-08-13. This paragraph named two owner decisions that
stood between this backend and a real upload. BOTH HAVE SINCE BEEN MADE, and
this docstring went on describing the world before them:

- `registry.DEFAULT_BACKEND` is `ladder`, not `typed_text`, so the running
  application DOES route an uploaded document to the ladder, and every image to
  this backend.
- the container image installs `tesseract-ocr` and `tesseract-ocr-eng`
  (`Dockerfile`, asserted by `tests/test_deploy_artefacts.py`), so on the
  deployed machine this backend no longer answers `ENGINE_MISSING` for want of
  an engine.

What still stands between it and a bill somebody trusts is accuracy, which is
not a decision anyone can take: the corpus numbers are poor and are stated in
`docs/EXTRACTION_MEASURED.md`. On a machine with no binary this backend still
answers `ENGINE_MISSING` — a refusal in plain words, not a crash — and that is
the property the tests below measure.

NO NETWORK. The only test here that starts a program is the one that measures
the real engine, and it is SKIPPED WITH A STATED REASON when the engine is not
installed rather than passing quietly on nothing.
"""

from __future__ import annotations

import base64
import datetime
import pathlib
import shutil
import subprocess
from typing import Final

import pytesseract  # pyright: ignore[reportMissingTypeStubs]
import pytest

from accountant.cage.wall import Observation
from accountant.extract import freeocr
from accountant.extract.adapter import NOT_FOUND, ExtractedRecord, Extractor
from accountant.extract.freeocr import (
    ALL_REFUSALS,
    ENGINE_ARGUMENTS,
    ENGINE_FAILED,
    ENGINE_MISSING,
    ENGINE_NOT_ALLOWED,
    ENGINE_TIMED_OUT,
    MALFORMED_READING,
    NO_SCORE_MARKER,
    READABLE_MEDIA,
    UNOPENABLE_PICTURE,
    UNREADABLE_MEDIA,
    EngineMissing,
    EngineTimedOut,
    FreeReader,
    PageReader,
    Reading,
    Word,
    read_words,
    refusal_for,
)

PNG = "image/png"

#: A picture of the eight characters `56640.00`, 450 bytes, rendered once and
#: carried here as text. It is a FIXTURE and not a document: no person, no
#: supplier and no amount from anybody's books is in it. It is embedded rather
#: than kept as a file because `tests/test_no_reader.py` is the reason this
#: whole design is shaped the way it is, and a binary fixture beside the tests
#: is the first step towards one beside the package.
AMOUNT_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAASwAAABGAQAAAABeHpRQAAABiUlEQVR42u2VPW4bMRBGH5dE"
    "tEWA3dKd9gg6QBIxZW6hM6QWYCbyQfYmpn8OoCNwDRcuqY42VpwUTgLYIjaunGanYvH4fZzh"
    "DKmEN8Sh4k0xYzP2ztgPw5WjgnsOFh47BldWs/1RHIH9Hh4e6PsTORF0pkmsZSduIeK10MjL"
    "iAYgkxIBcBp8FlLBtBnHJXELjoUD7WkomDaSzndDbrLOzZXIIozL3WvTCiBhgwJoO6hbOvta"
    "zCCA45Z4nK5b+3s5kjtBWrY2F7DH7gC3gH3eUkNXwFJkcPw8rcJLLCZ6L+dEN4VlRtw+Y9WE"
    "moHmu8AIoZo0XW34kkjhelJN+4Gv354+YP7Rli1urCYzeO43hTVK6ukU/j6wnz/p9xqZo6UF"
    "C1CFAyqmC39XUjsIfhQlN37KNIAzmRaIAVIk+FOsin196ZZPgErXsB2qeFEcwJEmspYbQY9r"
    "pzKL0iwY/iThDNhKURfO5mpN9xEH2Bo6g2sLt7C5xK/RAKsVnJ2xOfkW1fxRztiM/T/sF/8C"
    "yorjBSqQAAAAAElFTkSuQmCC"
)

#: The engine binary, if this machine has one. Named rather than called inline
#: so the skip reason below can say what was looked for.
ENGINE_ON_PATH = shutil.which("tesseract")

#: A skip that says what is missing and what to do about it. A bare
#: `importorskip` prints nothing a person can act on, and a measurement that
#: silently does not run is a measurement nobody notices the absence of.
NEEDS_THE_ENGINE = pytest.mark.skipif(
    ENGINE_ON_PATH is None,
    reason=(
        "SKIPPED LOUDLY: no `tesseract` binary on PATH, so the engine's own "
        "determinism cannot be measured on this machine. Install it with "
        "`brew install tesseract` (macOS), `apt-get install -y tesseract-ocr` "
        "(Linux) or `choco install tesseract` (Windows). The numbers this "
        "test re-measures are recorded in docs/OCR.md; they were taken on "
        "engine 5.5.3 and they are NOT re-proved when this test skips."
    ),
)

REPO = pathlib.Path(__file__).resolve().parent.parent
OCR_DOC = REPO / "docs" / "OCR.md"


# ---- building a reading without a reading engine -----------------------------


def spoken(text: str, confidence: int) -> tuple[Word, ...]:
    """One word the engine claims to have seen, at this confidence."""
    return (Word(text=text, confidence=confidence),)


def a_clean_bill() -> Reading:
    """Every field read well, and three amounts that agree with each other."""
    return Reading(
        date=spoken("2026-08-11", 94),
        party=(Word("SHARMA", 96), Word("TRADERS", 96)),
        total=spoken("56640.00", 92),
        tax=spoken("8640.00", 91),
        net=spoken("48000.00", 93),
    )


def reader_saying(answer: object) -> PageReader:
    """A reading engine that always answers with this exact thing."""

    def read_page(_data: bytes, _media: str) -> object:
        return answer

    return read_page


def reader_raising(exc: BaseException) -> PageReader:
    """A reading engine that fails this way every time."""

    def read_page(_data: bytes, _media: str) -> object:
        raise exc

    return read_page


def sourced(record: ExtractedRecord) -> set[str]:
    """The fields that carry a value rather than a stated absence."""
    return {
        name
        for name, source in record.per_field_source.items()
        if not source.startswith(NOT_FOUND)
    }


# ---- the seam ----------------------------------------------------------------


def test_this_backend_satisfies_the_extractor_protocol_without_changing_it() -> None:
    """The whole design rests on plugging into the seam that already exists.
    A backend that needed the Protocol widened would be a redesign."""
    reader = FreeReader(reader_saying(a_clean_bill()))

    assert isinstance(reader, Extractor)


def test_a_record_from_this_backend_states_a_source_for_every_named_field() -> None:
    record = FreeReader(reader_saying(a_clean_bill())).extract(AMOUNT_PNG, PNG)

    assert record.complete is True
    assert set(record.per_field_source) == set(ExtractedRecord.FIELDS)


def test_the_record_never_carries_the_document_that_was_read() -> None:
    """`pipeline.build_draft` copies `raw_text` into `Voucher.narration`, which
    reaches the page, the durable action log and Tally. A backend that echoed
    what it read would put somebody's scanned bill in all three."""
    record = FreeReader(reader_saying(a_clean_bill())).extract(AMOUNT_PNG, PNG)

    assert record.raw_text == ""


def test_the_record_names_the_backend_that_produced_it() -> None:
    """A row that cannot say who wrote it is not evidence about anybody."""
    record = FreeReader(reader_saying(a_clean_bill()), name="free_ocr").extract(
        AMOUNT_PNG, PNG
    )

    assert record.backend == "free_ocr"
    assert record.backend != "unknown"


# ---- determinism -------------------------------------------------------------


def test_the_same_bytes_read_ten_times_running_give_an_identical_record() -> None:
    """Rule 11.2.10. A reading that is not reproducible is not evidence about a
    document: two people looking at the same bill would be told two things."""
    reader = FreeReader(reader_saying(a_clean_bill()))

    records = [reader.extract(AMOUNT_PNG, PNG) for _ in range(10)]

    assert len({repr(r) for r in records}) == 1
    assert all(r == records[0] for r in records)


def test_the_control_a_different_reading_does_not_produce_the_same_record() -> None:
    """THE CONTROL on the test above. An `extract` that ignored its input and
    returned one fixed record would pass the determinism test perfectly."""
    same = FreeReader(reader_saying(a_clean_bill())).extract(AMOUNT_PNG, PNG)
    other = FreeReader(
        reader_saying(Reading(total=spoken("999.00", 92), party=spoken("OTHER", 90)))
    ).extract(AMOUNT_PNG, PNG)

    assert same != other


@NEEDS_THE_ENGINE
def test_the_engine_itself_answers_identically_ten_times_on_the_same_bytes(
    tmp_path: pathlib.Path,
) -> None:
    """The claim that picked this engine over the neural ones, re-measured.

    Byte-identical, not merely equal after parsing: a difference in a
    confidence column that a parser rounds away is still a different reading.
    """
    page = tmp_path / "amount.png"
    page.write_bytes(AMOUNT_PNG)
    argv = [str(ENGINE_ON_PATH), str(page), "stdout", "tsv"]

    answers = {
        subprocess.run(argv, capture_output=True, check=True).stdout  # noqa: S603
        for _ in range(10)
    }

    assert len(answers) == 1


@NEEDS_THE_ENGINE
def test_the_control_the_engine_actually_found_a_word_to_be_identical_about(
    tmp_path: pathlib.Path,
) -> None:
    """THE CONTROL. An engine that returned an empty page ten times would pass
    the determinism test above, and would have measured nothing at all."""
    page = tmp_path / "amount.png"
    page.write_bytes(AMOUNT_PNG)
    argv = [str(ENGINE_ON_PATH), str(page), "stdout", "tsv"]

    rows = subprocess.run(  # noqa: S603
        argv, capture_output=True, check=True
    ).stdout.decode()
    words = [r.split("\t") for r in rows.splitlines()[1:] if r.split("\t")[11].strip()]

    assert [w[11] for w in words] == ["56640.00"]
    assert 0 <= float(words[0][10]) <= 100


@NEEDS_THE_ENGINE
def test_the_engine_marks_every_row_that_is_not_a_word_with_the_marker(
    tmp_path: pathlib.Path,
) -> None:
    """The marker is not an edge case the engine rarely emits. It is on the
    majority of rows, and a caller that averaged the column would be averaging
    mostly markers."""
    page = tmp_path / "amount.png"
    page.write_bytes(AMOUNT_PNG)
    argv = [str(ENGINE_ON_PATH), str(page), "stdout", "tsv"]

    rows = subprocess.run(  # noqa: S603
        argv, capture_output=True, check=True
    ).stdout.decode()
    scores = [r.split("\t")[10] for r in rows.splitlines()[1:] if r.strip()]

    assert scores.count(str(NO_SCORE_MARKER)) == len(scores) - 1


def test_the_measured_determinism_numbers_survive_where_a_skip_cannot_hide_them() -> (
    None
):
    """A measurement that only lives in a test which skips is a measurement
    that quietly stops existing on the machines that matter."""
    doc = OCR_DOC.read_text(encoding="utf-8")

    assert "10 consecutive runs" in doc
    assert "1 distinct output" in doc
    assert "5.5.3" in doc


# ---- the -1 marker is a marker ----------------------------------------------


def test_the_marker_is_never_read_as_a_confidence_score() -> None:
    """-1 is the engine saying "no score here". As a number it is below every
    band; averaged in it drags a field down; on the 0-100 scale it does not
    exist. A field holding one has no score, and says so."""
    reading = Reading(total=(Word("56640.00", 92), Word("x", NO_SCORE_MARKER)))

    record = FreeReader(reader_saying(reading)).extract(AMOUNT_PNG, PNG)

    assert record.total_paise is None
    assert "marker" in record.per_field_source["total_paise"]


def test_the_control_the_same_words_without_the_marker_do_carry_a_value() -> None:
    """THE CONTROL. A module that refused every total would pass the test above
    while proving nothing about the marker."""
    reading = Reading(total=(Word("56640.00", 92), Word("00", 90)))

    record = FreeReader(reader_saying(reading)).extract(AMOUNT_PNG, PNG)

    assert record.total_paise is None  # "56640.00 00" is not an amount
    assert "marker" not in record.per_field_source["total_paise"]


def test_a_field_whose_every_word_is_a_marker_is_not_found_rather_than_zero() -> None:
    reading = Reading(party=(Word("", NO_SCORE_MARKER),), total=spoken("56640.00", 92))

    observed = FreeReader(reader_saying(reading)).observe(AMOUNT_PNG, PNG)

    assert observed.party.value is None
    assert observed.party.confidence == 0.0


def test_a_confidence_on_no_scale_we_know_refuses_the_whole_reading() -> None:
    """101 is not a very good 100 and 1000 is not a percentage times ten.
    Guessing which scale a number is on would be inventing the evidence."""
    reading = Reading(total=spoken("56640.00", 101))

    record = FreeReader(reader_saying(reading)).extract(AMOUNT_PNG, PNG)

    assert sourced(record) == set()
    assert MALFORMED_READING in record.per_field_source["total_paise"]


def test_a_true_false_value_where_a_confidence_belongs_refuses_the_reading() -> None:
    """`isinstance(True, int)` is True in Python, so an unguarded check reads a
    flag as a confidence of 1 out of 100 - a terrible score that looks real."""
    reading = Reading(total=(Word("56640.00", True),))

    record = FreeReader(reader_saying(reading)).extract(AMOUNT_PNG, PNG)

    assert sourced(record) == set()
    assert "bool" in record.per_field_source["total_paise"]


def test_a_confidence_that_is_not_a_whole_number_refuses_the_reading() -> None:
    """The engine's own text output prints `92.372406`. The DICT output floors
    it to `92`. `field_confidence` refuses anything that is not an int, so the
    conversion has to have happened before this module is reached."""
    reading = Reading(total=(Word("56640.00", 92.372406),))  # type: ignore[arg-type]

    record = FreeReader(reader_saying(reading)).extract(AMOUNT_PNG, PNG)

    assert sourced(record) == set()
    assert "float" in record.per_field_source["total_paise"]


def test_a_confident_blank_word_is_a_contradiction_and_refuses_the_reading() -> None:
    """The engine only ever writes the marker on a row with no text. A word
    with no text and a confidence of 90 did not come from the engine."""
    reading = Reading(total=(Word("   ", 90),))

    record = FreeReader(reader_saying(reading)).extract(AMOUNT_PNG, PNG)

    assert sourced(record) == set()
    assert "confident blank" in record.per_field_source["total_paise"].lower()


# ---- a missing program, and a program that will not stop ---------------------


def test_the_module_can_be_imported_on_a_machine_with_no_engine_installed() -> None:
    """The strongest form of "the app starts": there is no import in the module
    that can fail, because there is no third-party import in it at all."""
    import accountant.extract.freeocr as under_test

    assert under_test.FreeReader is FreeReader


def test_a_missing_engine_is_a_plain_sentence_and_never_a_traceback() -> None:
    """Simulated absence. `pipeline.build_draft` has no try around `extract`,
    and `web/app.py` turns an escaping exception into a 503 saying the
    application broke - for a person whose only problem is missing software."""
    reader = FreeReader(reader_raising(EngineMissing("no binary")))

    record = reader.extract(AMOUNT_PNG, PNG)

    assert sourced(record) == set()
    assert all(ENGINE_MISSING in s for s in record.per_field_source.values())


def test_a_file_not_found_from_a_plain_runner_also_reads_as_a_missing_engine() -> None:
    """A runner that shells out gets `FileNotFoundError` when the binary is
    absent, and it will not know to raise this module's own exception."""
    record = FreeReader(reader_raising(FileNotFoundError(2, "no such file"))).extract(
        AMOUNT_PNG, PNG
    )

    assert ENGINE_MISSING in record.per_field_source["total_paise"]


def test_a_bounded_wait_that_ran_out_refuses_with_its_own_sentence() -> None:
    reader = FreeReader(reader_raising(EngineTimedOut("10s")))

    record = reader.extract(AMOUNT_PNG, PNG)

    assert sourced(record) == set()
    assert all(ENGINE_TIMED_OUT in s for s in record.per_field_source.values())


def test_the_control_a_timeout_and_a_missing_program_do_not_share_a_sentence() -> None:
    """THE CONTROL. One sentence for both failures is a sentence neither person
    can act on: one has to install software and the other has to try a smaller
    picture."""
    assert refusal_for(EngineTimedOut("x")) != refusal_for(EngineMissing("x"))
    assert refusal_for(TimeoutError()) == ENGINE_TIMED_OUT
    assert refusal_for(PermissionError()) == ENGINE_NOT_ALLOWED


@pytest.mark.parametrize(
    "exc",
    [
        ValueError("bad image"),
        RuntimeError("engine crashed"),
        OSError("disk went away"),
        KeyError("conf"),
        ZeroDivisionError(),
    ],
)
def test_nothing_a_runner_raises_escapes_this_module_as_an_exception(
    exc: Exception,
) -> None:
    record = FreeReader(reader_raising(exc)).extract(AMOUNT_PNG, PNG)

    assert sourced(record) == set()
    assert ENGINE_FAILED in record.per_field_source["date"]
    assert type(exc).__name__ in record.per_field_source["date"]


def test_somebody_stopping_the_process_is_not_answered_with_a_tidy_record() -> None:
    """`Exception` and not `BaseException`. A KeyboardInterrupt is a person
    pressing Ctrl-C, and turning that into a neat refusal would fight them."""
    reader = FreeReader(reader_raising(KeyboardInterrupt()))

    with pytest.raises(KeyboardInterrupt):
        reader.extract(AMOUNT_PNG, PNG)


# ---- no input of anybody's reaches a program ---------------------------------


def test_the_media_type_handed_to_the_engine_is_one_of_this_module_s_constants() -> (
    None
):
    """The whole of "no user input reaches a shell", in one assertion: whatever
    a caller wrote in the header, what crosses the boundary is a constant from
    this file or nothing crosses at all."""
    seen: list[str] = []

    def read_page(_data: bytes, media: str) -> object:
        seen.append(media)
        return a_clean_bill()

    FreeReader(read_page).extract(AMOUNT_PNG, "IMAGE/PNG; charset=utf-8")

    assert seen == ["image/png"]
    assert set(seen) <= set(READABLE_MEDIA)


@pytest.mark.parametrize(
    "hostile",
    [
        "image/png; rm -rf /",
        "application/pdf",
        "text/plain",
        "",
        "image/png/../../etc/passwd",
    ],
)
def test_a_media_type_this_module_does_not_read_never_reaches_the_engine(
    hostile: str,
) -> None:
    """Refused BEFORE the runner is called, not sanitised on the way through.
    `image/png; rm -rf /` is the case that matters: the charset split leaves
    `image/png`, so this one is READ, and the rest of the header is discarded
    rather than carried."""
    reached: list[str] = []

    def read_page(_data: bytes, media: str) -> object:
        reached.append(media)
        return a_clean_bill()

    FreeReader(read_page).extract(AMOUNT_PNG, hostile)

    assert reached in ([], ["image/png"])


def test_a_kind_of_file_this_module_cannot_read_is_refused_with_a_sentence() -> None:
    record = FreeReader(reader_saying(a_clean_bill())).extract(
        b"%PDF-1.7", "application/pdf"
    )

    assert sourced(record) == set()
    assert UNREADABLE_MEDIA in record.per_field_source["party"]
    assert "application/pdf" in record.per_field_source["party"]


def test_the_bytes_reach_the_engine_unchanged_and_nothing_else_does() -> None:
    """Two arguments, both of them data. This module builds no command, so
    there is no string for an argument to be interpolated into."""
    seen: list[tuple[bytes, str]] = []

    def read_page(data: bytes, media: str) -> object:
        seen.append((data, media))
        return a_clean_bill()

    FreeReader(read_page).extract(AMOUNT_PNG, PNG)

    assert seen == [(AMOUNT_PNG, "image/png")]


# ---- failing closed on the amounts ------------------------------------------


def test_a_clean_bill_carries_its_four_fields_with_real_confidences() -> None:
    """The one test that is not about failure. Everything else is the reasons
    this one is allowed to happen."""
    observed = FreeReader(reader_saying(a_clean_bill())).observe(AMOUNT_PNG, PNG)

    assert observed.date.value == datetime.date(2026, 8, 11)
    assert observed.party.value == "SHARMA TRADERS"
    assert observed.total_paise.value == 5_664_000
    assert observed.tax_paise.value == 864_000


def test_the_field_score_is_the_worst_word_and_never_the_average() -> None:
    """One misread digit ruins an amount. A mean of 0.99 and 0.40 is 0.70,
    which reads as "worth asking about" rather than "certainly wrong"."""
    reading = Reading(
        date=spoken("2026-08-11", 94),
        party=(Word("SHARMA", 99), Word("TRADERS", 40)),
        total=spoken("56640.00", 92),
        tax=spoken("8640.00", 91),
        net=spoken("48000.00", 93),
    )

    observed = FreeReader(reader_saying(reading)).observe(AMOUNT_PNG, PNG)

    assert observed.party.confidence == pytest.approx(0.40)
    assert observed.lowest_confidence == pytest.approx(0.40)


def test_three_amounts_that_contradict_each_other_carry_no_value_at_all() -> None:
    """Two numbers that disagree are not two-thirds right. `net_plus_tax_equals
    _gross` is the law, and its own sentence is what the person is shown."""
    reading = Reading(
        total=spoken("56640.00", 99),
        tax=spoken("8640.00", 99),
        net=spoken("47999.00", 99),
    )

    record = FreeReader(reader_saying(reading)).extract(AMOUNT_PNG, PNG)

    assert record.total_paise is None
    assert record.tax_paise is None
    assert "do not agree" in record.per_field_source["total_paise"]


def test_the_control_the_same_three_amounts_that_do_agree_are_carried() -> None:
    """THE CONTROL. A module that refused every amount would pass the test
    above and would be telling the person nothing."""
    reading = Reading(
        total=spoken("56640.00", 99),
        tax=spoken("8640.00", 99),
        net=spoken("48000.00", 99),
    )

    record = FreeReader(reader_saying(reading)).extract(AMOUNT_PNG, PNG)

    assert record.total_paise == 5_664_000
    assert record.tax_paise == 864_000


def test_a_bill_that_printed_no_net_is_unchecked_rather_than_contradicted() -> None:
    """INDETERMINATE is not a soft FAIL. Refusing every bill that did not print
    a net would make the product useless, and blocking on "could not check"
    belongs to the decision layer, which already does it."""
    reading = Reading(total=spoken("56640.00", 92), tax=spoken("8640.00", 91))

    record = FreeReader(reader_saying(reading)).extract(AMOUNT_PNG, PNG)

    assert record.total_paise == 5_664_000


def test_an_amount_that_cannot_be_held_exactly_in_paise_is_refused_not_rounded() -> (
    None
):
    """`adapter._to_paise` carries the measured case: `round(float(x) * 100)`
    put `10.005` into the books as ten rupees exactly, and the half-paise was
    gone before any conversion could object."""
    reading = Reading(total=spoken("10.005", 96))

    record = FreeReader(reader_saying(reading)).extract(AMOUNT_PNG, PNG)

    assert record.total_paise is None
    assert "whole paise" in record.per_field_source["total_paise"]


def test_a_negative_amount_is_refused_rather_than_carried() -> None:
    """A minus sign on a printed total is a misread character or a credit note.
    This system does corrections by reversal and never by sign."""
    reading = Reading(total=spoken("-500.00", 96))

    record = FreeReader(reader_saying(reading)).extract(AMOUNT_PNG, PNG)

    assert record.total_paise is None
    assert "negative" in record.per_field_source["total_paise"]


def test_letters_read_as_digits_are_refused_rather_than_partly_salvaged() -> None:
    """`1O0O00` with letter O's is the commonest engine error on a printed
    amount, and it is exactly the one a lenient parser turns into money."""
    reading = Reading(total=spoken("1O0O00", 88))

    record = FreeReader(reader_saying(reading)).extract(AMOUNT_PNG, PNG)

    assert record.total_paise is None


def test_a_date_that_is_not_a_real_date_is_not_a_low_confidence_date() -> None:
    """The engine can be entirely certain it read `2026-13-45`, and that
    certainty is about pixels rather than about whether the thing is a date."""
    reading = Reading(date=spoken("2026-13-45", 99))

    observed = FreeReader(reader_saying(reading)).observe(AMOUNT_PNG, PNG)

    assert observed.date.value is None
    assert observed.date.confidence == 0.0


def test_a_date_in_a_form_this_system_does_not_read_is_refused_not_guessed() -> None:
    """`11/08/2026` is the 11th of August in India and the 8th of November in
    America. Picking one would be inventing the evidence."""
    reading = Reading(date=spoken("11/08/2026", 97))

    record = FreeReader(reader_saying(reading)).extract(AMOUNT_PNG, PNG)

    assert record.date is None
    assert "year-month-day" in record.per_field_source["date"]


def test_a_party_of_nothing_but_spaces_becomes_not_found_and_not_a_blank() -> None:
    """A party field holding `"   "` is a silent blank, which is the one thing
    `ExtractedRecord` exists to make impossible."""
    reading = Reading(party=(Word("   ", NO_SCORE_MARKER),))

    record = FreeReader(reader_saying(reading)).extract(AMOUNT_PNG, PNG)

    assert record.party is None
    assert record.per_field_source["party"].startswith(NOT_FOUND)


def test_a_word_the_engine_was_not_sure_of_at_all_carries_no_value() -> None:
    """A confidence of 0 is the engine saying it read something and could not
    vouch for one character. That is not a value at a low score."""
    reading = Reading(party=(Word("SHARMA", 0),))

    record = FreeReader(reader_saying(reading)).extract(AMOUNT_PNG, PNG)

    assert record.party is None
    assert "not sure" in record.per_field_source["party"]


# ---- the one rule that keeps the two answers from disagreeing ---------------


@pytest.mark.parametrize(
    "reading",
    [
        Reading(),
        a_clean_bill(),
        Reading(total=spoken("10.005", 96), date=spoken("2026-08-11", 94)),
        Reading(party=(Word("A", NO_SCORE_MARKER),), total=spoken("1.00", 50)),
        Reading(
            total=spoken("5.00", 99), tax=spoken("1.00", 99), net=spoken("9.00", 99)
        ),
    ],
)
def test_a_field_with_no_value_always_carries_a_confidence_of_exactly_zero(
    reading: Reading,
) -> None:
    """ "We did not read it" and "we are unsure" are the same fact. Letting them
    disagree would allow a post on nothing at all (`wall.Field`)."""
    observed = FreeReader(reader_saying(reading)).observe(AMOUNT_PNG, PNG)

    for field in (
        observed.date,
        observed.party,
        observed.total_paise,
        observed.tax_paise,
    ):
        assert (field.value is None) == (field.confidence == 0.0), field


@pytest.mark.parametrize(
    "reading",
    [Reading(), a_clean_bill(), Reading(total=spoken("10.005", 96))],
)
def test_the_record_and_the_observation_never_disagree_about_what_was_read(
    reading: Reading,
) -> None:
    """Two outputs, one fact. A number on a screen with an invisible 0.0 beside
    it is how a wrong total gets typed into Tally by a person who trusted it."""
    reader = FreeReader(reader_saying(reading))

    record = reader.extract(AMOUNT_PNG, PNG)
    observed = reader.observe(AMOUNT_PNG, PNG)

    assert observed.date.value == record.date
    assert observed.party.value == record.party
    assert observed.total_paise.value == record.total_paise
    assert observed.tax_paise.value == record.tax_paise


def test_a_refusal_leaves_every_field_none_with_a_reason_on_each_of_them() -> None:
    reader = FreeReader(reader_raising(EngineMissing("gone")))

    record = reader.extract(AMOUNT_PNG, PNG)
    observed = reader.observe(AMOUNT_PNG, PNG)

    assert {getattr(record, f) for f in ExtractedRecord.FIELDS} == {None}
    assert observed.lowest_confidence == 0.0
    assert all(
        s.startswith(NOT_FOUND) and len(s) > 20
        for s in record.per_field_source.values()
    )


# ---- an answer we cannot account for is a failed answer, not a partial one ---


@pytest.mark.parametrize(
    "answer", [None, {}, "a reading", 42, [], Observation, object()]
)
def test_an_answer_that_is_not_a_reading_is_refused_whole(answer: object) -> None:
    """An annotation on a function somebody else wrote is a promise, not a
    fact, and returning `None` on failure is the commonest shape there is."""
    record = FreeReader(reader_saying(answer)).extract(AMOUNT_PNG, PNG)

    assert sourced(record) == set()
    assert MALFORMED_READING in record.per_field_source["total_paise"]


def test_a_word_group_that_is_not_a_tuple_of_words_is_refused_whole() -> None:
    reading = Reading(total="56640.00")  # type: ignore[arg-type]

    record = FreeReader(reader_saying(reading)).extract(AMOUNT_PNG, PNG)

    assert sourced(record) == set()
    assert "tuple" in record.per_field_source["total_paise"]


def test_a_bad_word_in_the_net_refuses_the_reading_although_net_is_never_shown() -> (
    None
):
    """`net` reaches the conservation law, so a wrong type in it would reach
    arithmetic. A field that is never displayed is still evidence."""
    reading = Reading(total=spoken("56640.00", 92), net=(Word("48000.00", 200),))

    record = FreeReader(reader_saying(reading)).extract(AMOUNT_PNG, PNG)

    assert sourced(record) == set()


def test_the_refusals_this_module_can_give_are_distinct_non_empty_sentences() -> None:
    """A message nobody can pin is a message that drifts into silence."""
    assert len(ALL_REFUSALS) == len(set(ALL_REFUSALS))
    assert all(len(r.strip()) > 20 for r in ALL_REFUSALS)


def test_the_control_the_refusal_map_is_not_answering_everything_the_same_way() -> None:
    """THE CONTROL on the test above. A `refusal_for` that returned one string
    for every exception would satisfy every "a sentence came back" assertion in
    this file."""
    given = {
        refusal_for(exc)
        for exc in (
            EngineMissing("x"),
            EngineTimedOut("x"),
            PermissionError(),
            ValueError(),
        )
    }

    assert len(given) == 4


# =============================================================================
# THE ENGINE CALL ITSELF - `read_words`. Everything here starts a real program,
# so everything here skips loudly when the program is not installed.
# =============================================================================


#: Every token `ENGINE_ARGUMENTS` is allowed to contain. EMPTY today, and the
#: set is kept rather than folded back into `== ""` on purpose: `--psm 6` was
#: measured, adopted and reverted within a day, and the next person to adopt it
#: should have to add the token here - one deliberate line with a diff on it -
#: rather than loosen an equality check.
#:
#: A LIST and not a pattern, because a pattern is how `--psm` becomes
#: `--psm {mode}` becomes a caller's string.
PERMITTED_ENGINE_TOKENS: Final[frozenset[str]] = frozenset()


def test_the_engine_is_handed_nothing_a_caller_can_reach() -> None:
    """The whole of "no user input reaches a shell", and the property is NOT
    "the config is empty".

    Until 2026-08-15 this asserted `ENGINE_ARGUMENTS == ""`, which guaranteed
    the property by having no string at all. `--psm 6` was then chosen on
    measured evidence, and the guarantee has to be re-proved rather than
    dropped: `pytesseract.run_tesseract` `shlex.split`s the config into further
    elements of a python LIST handed to `subprocess.Popen` with no shell, so
    what matters is that every element is a FIXED literal of ours.

    Asserting the exact string would pass just as well and prove less - it would
    still pass the day somebody writes an f-string. This checks the property:
    every token is one we named, and none of them can carry a value."""
    tokens = ENGINE_ARGUMENTS.split()

    assert set(tokens) <= PERMITTED_ENGINE_TOKENS, tokens
    for token in tokens:
        assert "{" not in token and "%" not in token and "$" not in token, token


def test_the_engine_arguments_are_a_constant_and_not_a_template() -> None:
    """THE CONTROL, and it is the one that survives a refactor. An f-string or a
    `.format` in `ENGINE_ARGUMENTS` would keep every assertion above true on the
    day it was written and become a shell-adjacent injection the day a caller's
    value reached it. The constant is read out of the SOURCE, not the module, so
    a value computed at import time cannot hide behind its own result."""
    source = pathlib.Path(freeocr.__file__).read_text(encoding="utf-8")
    declaration = next(
        line for line in source.splitlines() if line.startswith("ENGINE_ARGUMENTS")
    )

    assert declaration == 'ENGINE_ARGUMENTS: Final = ""', declaration


@pytest.mark.parametrize("bad", [0, -1, -0.5, "8", None, True])
def test_a_reading_deadline_that_is_not_a_positive_number_is_refused(
    bad: object,
) -> None:
    """An unbounded wait is a request that hangs. `True` is in this list on
    purpose: it is an `int` in Python and would pass as a one-second deadline
    that somebody meant as a flag."""
    with pytest.raises(ValueError, match="positive number of seconds"):
        read_words(AMOUNT_PNG, deadline_seconds=bad)  # type: ignore[arg-type]


@NEEDS_THE_ENGINE
def test_the_engine_returns_words_this_module_can_actually_score() -> None:
    """End to end against the real program: bytes in, one word out, with a
    confidence on the 0-100 scale that `field_confidence` will accept."""
    words = read_words(AMOUNT_PNG, deadline_seconds=30)

    assert [w.text for w in words] == ["56640.00"]
    assert type(words[0].confidence) is int
    assert 0 <= words[0].confidence <= 100


@NEEDS_THE_ENGINE
def test_the_engine_call_hands_back_no_marker_because_it_keeps_only_words() -> None:
    """The structural rows carry the marker and are dropped at the source.
    A caller that got them would have to know to filter, and the one that
    forgets does not fail - it silently scores the page as unreadable."""
    words = read_words(AMOUNT_PNG, deadline_seconds=30)

    assert words
    assert all(w.confidence != NO_SCORE_MARKER for w in words)


@NEEDS_THE_ENGINE
def test_ten_engine_calls_on_the_same_bytes_return_the_identical_words() -> None:
    """The determinism claim at the level this module actually consumes."""
    answers = {read_words(AMOUNT_PNG, deadline_seconds=30) for _ in range(10)}

    assert len(answers) == 1


@NEEDS_THE_ENGINE
def test_a_real_reading_scores_a_real_amount_through_the_whole_adapter() -> None:
    """The engine, the marker filter, the paise conversion and the confidence
    proxy, in one line each. Nothing here is a hand-built `Reading`."""
    words = read_words(AMOUNT_PNG, deadline_seconds=30)

    observed = FreeReader(reader_saying(Reading(total=words))).observe(AMOUNT_PNG, PNG)

    assert observed.total_paise.value == 5_664_000
    assert observed.total_paise.confidence > 0.9


@NEEDS_THE_ENGINE
def test_a_binary_that_is_not_there_is_a_sentence_and_never_a_traceback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Simulated absence against the REAL wrapper, not against a stub of it.
    `TesseractNotFoundError` subclasses `OSError` but not `FileNotFoundError`,
    so a map that only knew the latter would send this to the wrong sentence."""
    monkeypatch.setattr(
        pytesseract.pytesseract, "tesseract_cmd", "/nowhere/no-such-program"
    )

    def read_page(data: bytes, _media: str) -> object:
        return Reading(total=read_words(data, deadline_seconds=30))

    record = FreeReader(read_page).extract(AMOUNT_PNG, PNG)

    assert sourced(record) == set()
    assert all(ENGINE_MISSING in s for s in record.per_field_source.values())


@NEEDS_THE_ENGINE
def test_a_bounded_wait_that_runs_out_kills_the_program_and_refuses() -> None:
    """A real deadline against the real program, set small enough that it
    cannot be met. The wrapper kills the process before re-raising."""

    def read_page(data: bytes, _media: str) -> object:
        return Reading(total=read_words(data, deadline_seconds=0.000001))

    record = FreeReader(read_page).extract(AMOUNT_PNG, PNG)

    assert sourced(record) == set()
    assert all(ENGINE_TIMED_OUT in s for s in record.per_field_source.values())


@NEEDS_THE_ENGINE
def test_the_control_the_same_call_with_a_workable_deadline_does_read() -> None:
    """THE CONTROL on the test above. A `read_words` that refused everything
    would pass the timeout test and would have proved nothing about deadlines."""

    def read_page(data: bytes, _media: str) -> object:
        return Reading(total=read_words(data, deadline_seconds=30))

    record = FreeReader(read_page).extract(AMOUNT_PNG, PNG)

    assert record.total_paise == 5_664_000


def test_a_word_cannot_be_edited_after_the_engine_reported_it() -> None:
    """Frozen, like every other evidence type in this system. A confidence that
    can be changed after the fact by anything downstream is not evidence about
    what the engine said."""
    word = Word(text="56640.00", confidence=92)

    with pytest.raises(AttributeError):
        word.confidence = 99  # type: ignore[misc]


def test_a_reading_cannot_be_edited_after_it_is_made() -> None:
    reading = a_clean_bill()

    with pytest.raises(AttributeError):
        reading.total = ()  # type: ignore[misc]


def test_a_contradiction_between_amounts_leaves_the_party_alone() -> None:
    """The consistency multiplier is scoped to the arithmetic on purpose. A
    smudged letterhead does not become more or less legible because the tax
    line does not add up, and zeroing the party too would refuse a bill for a
    reason that has nothing to do with the party."""
    reading = Reading(
        party=(Word("SHARMA", 96), Word("TRADERS", 96)),
        total=spoken("56640.00", 99),
        tax=spoken("8640.00", 99),
        net=spoken("47999.00", 99),
    )

    record = FreeReader(reader_saying(reading)).extract(AMOUNT_PNG, PNG)

    assert record.party == "SHARMA TRADERS"
    assert record.total_paise is None


@NEEDS_THE_ENGINE
def test_bytes_that_are_not_a_picture_at_all_become_a_sentence() -> None:
    """A caller may declare `image/png` and send anything. Below the seam
    `read_words` raises; at the seam it has to be a sentence, because the
    person uploaded the wrong file and that is not an application failure.

    THE PINNED SENTENCE CHANGED 2026-08-13 and the pin did not loosen. It used
    to be `ENGINE_FAILED`, which was the catch-all: `Image.open` raises
    `UnidentifiedImageError` before the engine is reached, so a person was told
    the reading program could not read their file for a problem the reading
    program never saw. `UNOPENABLE_PICTURE` is the specific sentence for the
    specific failure, and it is what the twenty corpus JPEGs meet - every one
    of them is a header with no picture behind it."""

    def read_page(data: bytes, _media: str) -> object:
        return Reading(total=read_words(data, deadline_seconds=30))

    record = FreeReader(read_page).extract(b"this is not a picture", PNG)

    assert sourced(record) == set()
    said = record.per_field_source["total_paise"]
    assert said == f"{NOT_FOUND}: {UNOPENABLE_PICTURE}"
    assert ENGINE_FAILED not in said


# =============================================================================
# REVIEW NOTES - read back adversarially on 2026-08-13 by somebody who had to
# pretend they had not written it. Five things were wrong or missing across two
# passes; four are fixed here and the fifth is stated.
#
# 1. FIXED. `test_the_marker_is_never_read_as_a_confidence_score` originally
#    asserted only `record.total_paise is None`, and it passed for the wrong
#    reason: "56640.00 x" is not an amount, so the FORMAT check refused it and
#    the marker was never reached. The assertion on the word "marker" in the
#    source is what makes it about the marker, and
#    `test_the_control_the_same_words_without_the_marker_do_carry_a_value` is
#    the paired control that fails if the two paths get confused.
#
# 2. FIXED. There was no test that a refusal and a reading agree. `extract` and
#    `observe` are two public methods built from the same `_Answer`, and nothing
#    stopped a later change making one carry a value the other had dropped -
#    which is precisely what the "confidence 0.0 means no value" rule exists to
#    prevent. Both parametrised agreement tests were added for that.
#
# 3. FIXED. Nothing pinned the SCOPE of the consistency multiplier. Zeroing the
#    party as well as the amounts when the arithmetic contradicts would have
#    passed every other test in this file, and would refuse a bill for a reason
#    that has nothing to do with the party.
#    `test_a_contradiction_between_amounts_leaves_the_party_alone` is the pin.
#
# 4. FIXED. `Word` and `Reading` were never checked to be frozen, although
#    every other evidence type in this system is and `tests/test_conservation.py`
#    checks its own. A confidence that a later stage can edit is not evidence
#    about what the engine said.
#
# 5. NOT FIXED, and it is a real flake risk rather than a missing assertion.
#    `test_a_bounded_wait_that_runs_out_kills_the_program_and_refuses` uses a
#    deadline of one microsecond and relies on process startup taking longer
#    than that. It always will - MEASURED, a page takes 0.065 s to 0.131 s and
#    a bare `fork`/`exec` is milliseconds - but it is a race in principle, and
#    the only way to remove it entirely is a fake engine, which would stop the
#    test being about the real bounded wait. Recorded rather than papered over.
# =============================================================================


# =============================================================================
# A PHOTOGRAPH THAT CLAIMS EXACTNESS, 2026-08-13
# =============================================================================
#
# `field_confidence` is `min(word_conf) / 100` with no ceiling, and `_complaint`
# accepts any score `0 <= conf <= 100` as in-contract. So a reading whose every
# word carries 100 scores exactly 1.0, which is `confidence.EXACT` - and
# `ExtractedRecord.read_exactly` was a bare `== EXACT` with no question about
# WHO read it. `pipeline.py:320` is the one consumer that matters:
#
#     party = record.party if record.read_exactly("party") else None
#
# so a photograph could hand a name to `propose_account` and put it on a
# voucher. The only thing preventing it was an unmeasured, unpinned property of
# a third-party binary: measured on tesseract 5.5.3, the top word confidence is
# 96 across the twenty corpus PNGs and 97 across ~900 synthetic renders. The
# folklore "96 cap" is already wrong by one, and nothing in this repository
# asserts the engine cannot emit 100.
#
# The SCORE is not capped and must not be - `test_pagereader.py::
# test_the_control_the_same_words_at_full_confidence_do_reach_exactness` exists
# to prove this file reports what the engine claimed rather than a number of
# our own. What changed is the other half: exactness is a statement about the
# TIER, which is what `read_exactly`'s own docstring already said it meant, and
# the implementation now asks that question instead of reading a float.


def _all_at(score: int) -> Reading:
    """One legible bill where every word came back at the same confidence."""
    return Reading(
        date=(Word("2026-04-01", score),),
        party=(Word("SHARMA", score), Word("TRADERS", score)),
        total=(Word("1234.56", score),),
        tax=(Word("188.32", score),),
        net=(Word("1046.24", score),),
    )


def test_an_engine_reporting_a_hundred_still_never_reads_a_party_exactly() -> None:
    """THE REPRODUCER, built rather than waited for.

    Every word at 100 is in-contract for this module, and the record it makes
    states 1.0 on all four fields. None of them is read EXACTLY, because
    pixels were guessed at whatever the engine thinks of its own guessing."""
    record = FreeReader(lambda _d, _m: _all_at(100)).extract(b"", PNG)

    assert record.per_field_confidence["party"] == 1.0
    assert record.party == "SHARMA TRADERS"
    assert not record.read_exactly("party")
    assert not any(record.read_exactly(f) for f in ExtractedRecord.FIELDS)


def test_the_score_this_engine_reports_is_still_the_engine_s_own_number() -> None:
    """THE CONTROL, and the reason the fix is not a cap on the arithmetic.

    Clamping `field_confidence` below 1.0 would have made the test above pass
    while quietly making this module lie about what it was told. The number is
    reported unchanged; only the entitlement to be called exact is refused."""
    ninety_six = FreeReader(lambda _d, _m: _all_at(96)).extract(b"", PNG)
    hundred = FreeReader(lambda _d, _m: _all_at(100)).extract(b"", PNG)

    assert ninety_six.confidence_of("party") == 0.96
    assert hundred.confidence_of("party") == 1.0
