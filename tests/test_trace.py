"""The diagnostic must not say the engine failed when the vocabulary missed.

WHAT IS BEING PROVED HERE
--------------------------
`accountant/extract/trace.py` exists because `freeocr._judge` prints one
sentence for two unrelated situations - see that module's docstring for the
measurement. These tests hold the line on the distinction:

    a page that returned NO rows at all
        -> Where.NO_OCR_WORDS
    a page that returned rows, and matched no label this reader knows
        -> Where.OCR_PRESENT_FIELD_CANDIDATES_MISSING

and, harder, that the SENTENCE for the second one cannot be read as the first.
`test_a_read_page_is_never_reported_as_a_reading_failure` is the regression that
matters: MEASURED across this repository's corpus on 2026-08-15, 595 of the 675
dead field slots were the second case and 80 were the first, so a wording that
blames the engine is wrong 88.1% of the time it is printed.

EVERY ASSERTION HERE IS ON AN EXACT VALUE. An exact `Where` member, an exact
sentence, an exact count, an exact tuple of field names. A test that checked
`assert trace.said` would pass against a sentence saying the opposite of the
truth, which is the failure this whole module is a response to.

NO FIXTURES, NO IO, NO NETWORK, NO IMAGES, NO TESSERACT. Every test is integers
and strings, which is what makes them runnable on a machine that has never seen
an invoice - the property `tests/test_conservation.py` argues for at length. The
one test that touches `labels.py` uses three lines of typed text and the real
matchers, so the claim "words present, no label matched" is checked against the
shipping vocabulary rather than against a number this file made up.
"""

from __future__ import annotations

import ast
import dataclasses
from pathlib import Path

import pytest

from accountant.extract import trace as trace_module
from accountant.extract.labels import TOTAL_LABELS, amounts_for
from accountant.extract.trace import (
    FieldTrace,
    PageTrace,
    Where,
    sentence_for,
    where_from,
)

TRACE_FILE = Path(str(trace_module.__file__))


# =============================================================================
# the shape of the API
# =============================================================================


def test_the_enum_has_exactly_the_five_rungs_and_exactly_these_wire_values() -> None:
    """Names AND values, both pinned.

    The value is what reaches a log line and a stored record, so renaming a
    member without renaming its value would silently split every historical
    diagnostic from every new one.
    """
    assert [(member.name, member.value) for member in Where] == [
        ("NO_OCR_WORDS", "no_ocr_words"),
        (
            "OCR_PRESENT_FIELD_CANDIDATES_MISSING",
            "ocr_present_field_candidates_missing",
        ),
        ("LABEL_MATCHED_NO_VALUE", "label_matched_no_value"),
        ("CANDIDATE_REJECTED_BY_VALIDATION", "candidate_rejected_by_validation"),
        ("CANDIDATE_SCORED", "candidate_scored"),
    ]


def test_a_rung_is_never_equal_to_its_own_wire_string() -> None:
    """`Where` is a plain `enum.Enum` and not a `StrEnum`, on purpose.

    `trace.where == "no_ocr_words"` must be False, so that a comparison written
    against a string - including a MISSPELLED string - fails loudly instead of
    quietly answering about nothing.
    """
    assert Where.NO_OCR_WORDS != "no_ocr_words"
    assert Where.NO_OCR_WORDS.value == "no_ocr_words"


def test_the_field_trace_carries_exactly_these_fields_in_this_order() -> None:
    assert tuple(f.name for f in dataclasses.fields(FieldTrace)) == (
        "field",
        "where",
        "rows_received",
        "rows_with_characters",
        "labels_considered",
        "labels_matched",
        "candidates_generated",
        "candidates_rejected",
        "candidates_judged",
        "score",
        "value",
        "rejection_reasons",
        "said",
    )


def test_the_page_trace_carries_exactly_these_fields_in_this_order() -> None:
    assert tuple(f.name for f in dataclasses.fields(PageTrace)) == (
        "rows_received",
        "rows_with_characters",
        "fields",
    )


def test_both_traces_are_frozen() -> None:
    """A diagnostic that can be edited after the fact is not evidence."""
    field = a_scored_slot()
    page = PageTrace(rows_received=40, rows_with_characters=38, fields=(field,))
    with pytest.raises(dataclasses.FrozenInstanceError):
        field.where = Where.CANDIDATE_SCORED  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        page.rows_received = 1  # type: ignore[misc]


# =============================================================================
# one case per rung
# =============================================================================

#: One row of the table below. The counts are on the left, the exact rung and
#: the exact sentence on the right, and nothing in between is inferred.
RUNGS: tuple[tuple[str, dict[str, object], Where, str], ...] = (
    (
        "a page that returned nothing at all",
        {
            "field": "total_paise",
            "rows_received": 0,
            "rows_with_characters": 0,
            "labels_considered": 10,
            "labels_matched": 0,
        },
        Where.NO_OCR_WORDS,
        "Nothing at all was read off this page - the reader was handed 0 word "
        "rows - so total_paise was never looked for.",
    ),
    (
        "the widest page in the measured sample, and no label on it",
        {
            "field": "total_paise",
            "rows_received": 699,
            "rows_with_characters": 612,
            "labels_considered": 10,
            "labels_matched": 0,
        },
        Where.OCR_PRESENT_FIELD_CANDIDATES_MISSING,
        "This page was read - 612 of 699 word rows carry characters - and "
        "total_paise is missing only because not one of the 10 labels this "
        "reader knows for it appears anywhere on the page.",
    ),
    (
        "a label with nothing printed after it",
        {
            "field": "party",
            "rows_received": 40,
            "rows_with_characters": 38,
            "labels_considered": 4,
            "labels_matched": 1,
            "candidates_generated": 1,
        },
        Where.LABEL_MATCHED_NO_VALUE,
        "A label for party was found on this page 1 time, and nothing was "
        "printed after it, so there is no value to take.",
    ),
    (
        "two candidates, both refused by a check",
        {
            "field": "tax_paise",
            "rows_received": 40,
            "rows_with_characters": 38,
            "labels_considered": 10,
            "labels_matched": 2,
            "candidates_generated": 2,
            "candidates_rejected": 2,
            "rejection_reasons": ("a sub-paise figure will not convert",),
        },
        Where.CANDIDATE_REJECTED_BY_VALIDATION,
        "Every one of the 2 candidates found under a tax_paise label was "
        "refused by a validation check (2 refusals, 1 distinct reason recorded "
        "beside this trace and deliberately not repeated in words), so "
        "tax_paise stays unread.",
    ),
    (
        "one candidate, judged and scored",
        {
            "field": "date",
            "rows_received": 40,
            "rows_with_characters": 38,
            "labels_considered": 5,
            "labels_matched": 1,
            "candidates_generated": 1,
            "candidates_judged": 1,
            "score": 0.87,
            "value": "2026-04-17",
        },
        Where.CANDIDATE_SCORED,
        "date was read and scored 0.87, from 1 candidate judged out of the 1 "
        "candidate found under a label on this page.",
    ),
)


@pytest.mark.parametrize(
    ("name", "counts", "expected_where", "expected_said"),
    RUNGS,
    ids=[row[0] for row in RUNGS],
)
def test_every_rung_is_reached_by_its_counts_and_says_its_own_sentence(
    name: str,
    counts: dict[str, object],
    expected_where: Where,
    expected_said: str,
) -> None:
    built = FieldTrace.at(**counts)  # type: ignore[arg-type]
    assert built.where is expected_where, name
    assert built.said == expected_said, name


def test_the_table_covers_every_rung_exactly_once() -> None:
    """The parametrisation above is only a coverage claim if this holds.

    A member added to `Where` with no row in `RUNGS` fails here rather than
    being silently untested, which is how an enum grows a member nobody has
    ever seen a sentence for.
    """
    assert [row[2] for row in RUNGS] == list(Where)


def test_said_is_never_empty_on_any_rung() -> None:
    for name, counts, _, _ in RUNGS:
        built = FieldTrace.at(**counts)  # type: ignore[arg-type]
        assert built.said.strip() != "", name
        assert built.said.endswith("."), name


def test_no_two_rungs_produce_the_same_sentence() -> None:
    """The whole defect in one assertion.

    `freeocr._judge` prints ONE sentence for two of these rungs. Five distinct
    sentences for five rungs is the thing this module adds.
    """
    sentences = [FieldTrace.at(**counts).said for _, counts, _, _ in RUNGS]  # type: ignore[arg-type]
    assert len(set(sentences)) == len(RUNGS)


# =============================================================================
# the rule that is not negotiable
# =============================================================================

#: Phrases that are true only when the page returned nothing. MEASURED on
#: 2026-08-15 over this repository's `data/` corpus: 595 of 675 dead field slots
#: had rows on the page, so a sentence carrying either of these would be wrong
#: 88.1% of the times it was printed.
NEVER_ON_A_READ_PAGE = ("OCR failed", "could not read")


@pytest.mark.parametrize("rows", [1, 2, 40, 699, 2026])
def test_a_read_page_with_no_candidates_is_never_called_a_reading_failure(
    rows: int,
) -> None:
    """BOTH halves of the critical rule, on the same object.

    The enum, because that is what a machine reads and what
    `cage/decision.py`-shaped code would branch on. The absence of the phrases,
    because that is what the OWNER reads, and the owner is the one who would go
    and buy a scanner for a problem that is four strings in `labels.py`.
    """
    built = FieldTrace.at(
        field="total_paise",
        rows_received=rows,
        rows_with_characters=rows,
        labels_considered=len(TOTAL_LABELS),
        labels_matched=0,
        candidates_generated=0,
    )
    assert built.rows_received > 0
    assert built.candidates_generated == 0
    assert built.where is Where.OCR_PRESENT_FIELD_CANDIDATES_MISSING
    spoken = built.said.lower()
    for phrase in NEVER_ON_A_READ_PAGE:
        assert phrase.lower() not in spoken, (phrase, built.said)


def test_the_control_refuses_such_a_sentence_at_construction() -> None:
    """`_MISREADS_A_READ_PAGE` is enforced in `__post_init__`.

    A guard that lives only in a test file protects only the code paths the
    test file happens to build. This one refuses the object, so a future caller
    hand-writing `said` cannot produce a trace that lies about a read page -
    including in a different casing, which is why the check lower-cases.
    """
    for phrase in ("OCR failed here", "the reader could not read this page"):
        with pytest.raises(ValueError, match="defect this module was built to end"):
            FieldTrace(
                field="total_paise",
                where=Where.OCR_PRESENT_FIELD_CANDIDATES_MISSING,
                rows_received=699,
                rows_with_characters=612,
                labels_considered=10,
                labels_matched=0,
                candidates_generated=0,
                candidates_rejected=0,
                candidates_judged=0,
                score=None,
                value=None,
                rejection_reasons=(),
                said=phrase,
            )


def test_the_two_situations_freeocr_conflates_now_say_different_things() -> None:
    """The regression, stated as the two documents that produced it.

    Same field, same reader, same vocabulary. One page came back empty and one
    came back with 699 word rows - the widest page in the measured sample. Under
    `freeocr._judge` both print *"reported no word here that carries a
    confidence"*. Here they are two rungs and two sentences, and only the empty
    one is allowed to sound like a reading failure.
    """
    blank = FieldTrace.at(
        field="total_paise",
        rows_received=0,
        rows_with_characters=0,
        labels_considered=len(TOTAL_LABELS),
        labels_matched=0,
    )
    busy = FieldTrace.at(
        field="total_paise",
        rows_received=699,
        rows_with_characters=612,
        labels_considered=len(TOTAL_LABELS),
        labels_matched=0,
    )
    assert blank.where is Where.NO_OCR_WORDS
    assert busy.where is Where.OCR_PRESENT_FIELD_CANDIDATES_MISSING
    assert blank.said != busy.said
    assert "never looked for" in blank.said
    assert "This page was read" in busy.said
    assert "699" in busy.said and "699" not in blank.said


def test_the_label_rung_is_what_the_real_vocabulary_actually_does() -> None:
    """Grounded in `labels.py`, not in a number this file invented.

    Three lines of a real-shaped bus ticket. The shipping matcher finds no total
    label on them, and the trace built from that answer is the label rung - so
    the claim "words present, no label matched" is checked against the
    vocabulary that ships rather than against a count typed in here.
    """
    lines = (
        "MAHARASHTRA STATE ROAD TRANSPORT CORPORATION",
        "Ticket 4412 Seat 21 Pune to Nashik",
        "Fare 183.00 Paid by cash",
    )
    located = amounts_for(lines, TOTAL_LABELS)
    assert located == ()
    built = FieldTrace.at(
        field="total_paise",
        rows_received=len(lines),
        rows_with_characters=len(lines),
        labels_considered=len(TOTAL_LABELS),
        labels_matched=len(located),
        candidates_generated=len(located),
    )
    assert built.where is Where.OCR_PRESENT_FIELD_CANDIDATES_MISSING
    assert f"{len(TOTAL_LABELS)} labels" in built.said
    assert "3 word rows" in built.said


# =============================================================================
# the document never reaches the sentence
# =============================================================================

#: Strings that exist nowhere but in this file and in the traces built below.
#: A party, an amount, a tax number, a document number and a date - one of each
#: kind of thing a bill carries that must never reach a log.
DOCUMENT_CONTENTS: tuple[str, ...] = (
    "ZORBLAX PROPULSION PVT LTD",
    "47,183.29",
    "27AAQCZ9931Q1ZV",
    "INV-QQ-9931",
    "2026-04-17",
)


def a_page_carrying_the_document() -> PageTrace:
    """A page whose traces hold every string in `DOCUMENT_CONTENTS`.

    Two of them arrive as `value` on a scored slot, three inside
    `rejection_reasons` - which is where the real leak would come from, because
    `labels.the_one` interpolates the disagreeing values straight into its own
    refusal text.
    """
    return PageTrace(
        rows_received=40,
        rows_with_characters=38,
        fields=(
            FieldTrace.at(
                field="date",
                rows_received=40,
                rows_with_characters=38,
                labels_considered=5,
                labels_matched=1,
                candidates_generated=1,
                candidates_judged=1,
                score=0.91,
                value="2026-04-17",
            ),
            FieldTrace.at(
                field="party",
                rows_received=40,
                rows_with_characters=38,
                labels_considered=4,
                labels_matched=1,
                candidates_generated=1,
                candidates_judged=1,
                score=0.44,
                value="ZORBLAX PROPULSION PVT LTD",
            ),
            FieldTrace.at(
                field="total_paise",
                rows_received=40,
                rows_with_characters=38,
                labels_considered=10,
                labels_matched=2,
                candidates_generated=2,
                candidates_rejected=2,
                rejection_reasons=(
                    "not_found: this document states a total more than once "
                    "and the statements disagree (47,183.29, 47,183.92), so "
                    "nothing is read from it",
                    "not_found: 47,183.29 was printed under a heading and not "
                    "under a label",
                ),
            ),
            FieldTrace.at(
                field="tax_paise",
                rows_received=40,
                rows_with_characters=38,
                labels_considered=10,
                labels_matched=1,
                candidates_generated=1,
                candidates_rejected=1,
                rejection_reasons=(
                    "not_found: 27AAQCZ9931Q1ZV on document INV-QQ-9931 is a "
                    "registration number after a column gap, not an amount",
                ),
            ),
            FieldTrace.at(
                field="net_paise",
                rows_received=40,
                rows_with_characters=38,
                labels_considered=5,
                labels_matched=0,
            ),
        ),
    )


def test_the_page_actually_holds_the_document_contents() -> None:
    """The control on the test below, and it is not decoration.

    If the traces did not carry these strings at all, the privacy assertion
    would pass against an empty object and prove nothing. This test is what
    makes the next one a real check.
    """
    page = a_page_carrying_the_document()
    carried = "".join(
        (field.value or "") + "".join(field.rejection_reasons) for field in page.fields
    )
    for secret in DOCUMENT_CONTENTS:
        assert secret in carried, secret


def test_no_document_contents_reach_any_sentence() -> None:
    """Not the page's paragraph, and not any field's sentence either."""
    page = a_page_carrying_the_document()
    spoken = "\n".join([page.said(), *(field.said for field in page.fields)])
    for secret in DOCUMENT_CONTENTS:
        assert secret not in spoken, secret


def test_the_page_paragraph_says_the_counts_and_names_the_fields() -> None:
    """What it says INSTEAD of the contents, pinned exactly.

    Written out in full rather than checked with `in`, because a sentence that
    counted wrong would still contain every fragment a partial check looks for.
    """
    page = a_page_carrying_the_document()
    assert page.said().splitlines()[0] == (
        "This page returned 40 word rows, 38 of them carrying characters. "
        "Of 5 field slots: 2 were read and scored, 1 stopped because no label "
        "this reader knows appeared on a page it had read, 0 found a label "
        "with nothing printed after it, 2 were refused by a validation check, "
        "and 0 never got a page to look at."
    )
    assert page.counted(Where.CANDIDATE_SCORED) == 2
    assert page.counted(Where.CANDIDATE_REJECTED_BY_VALIDATION) == 2
    assert page.counted(Where.OCR_PRESENT_FIELD_CANDIDATES_MISSING) == 1
    assert page.counted(Where.LABEL_MATCHED_NO_VALUE) == 0
    assert page.counted(Where.NO_OCR_WORDS) == 0
    assert len(page.said().splitlines()) == 6
    for field in page.fields:
        assert field.field in page.said(), field.field


# =============================================================================
# the counts must hang together
# =============================================================================


def a_scored_slot() -> FieldTrace:
    return FieldTrace.at(
        field="date",
        rows_received=40,
        rows_with_characters=38,
        labels_considered=5,
        labels_matched=1,
        candidates_generated=1,
        candidates_judged=1,
        score=0.87,
        value="2026-04-17",
    )


#: Each row is counts that contradict themselves, and the words the refusal must
#: contain. The message is asserted because a `ValueError` saying nothing useful
#: is one the next person deletes.
INCONSISTENT: tuple[tuple[str, dict[str, object], str], ...] = (
    (
        "more rows carry characters than arrived",
        {"rows_received": 10, "rows_with_characters": 11, "labels_matched": 0},
        "more rows than arrived",
    ),
    (
        "more labels matched than this reader knows",
        {"labels_considered": 3, "labels_matched": 4, "candidates_generated": 4},
        "more labels than this reader knows",
    ),
    (
        "more candidates accounted for than generated",
        {
            "labels_matched": 1,
            "candidates_generated": 1,
            "candidates_rejected": 1,
            "candidates_judged": 1,
            "rejection_reasons": ("refused",),
            "score": 0.5,
        },
        "more than the 1 generated",
    ),
    (
        "a label matched and located nothing",
        {"labels_matched": 3, "candidates_generated": 0},
        "zero together or not at all",
    ),
    (
        "a negative count",
        {"labels_matched": 0, "labels_considered": -1},
        "cannot be negative",
    ),
    (
        "a refusal with no reason",
        {
            "labels_matched": 1,
            "candidates_generated": 1,
            "candidates_rejected": 1,
            "rejection_reasons": (),
        },
        "a refusal with no reason",
    ),
    (
        "a reason with no refusal",
        {"labels_matched": 0, "rejection_reasons": ("refused",)},
        "a refusal with no reason",
    ),
    (
        "more distinct reasons than refusals",
        {
            "labels_matched": 1,
            "candidates_generated": 2,
            "candidates_rejected": 1,
            "rejection_reasons": ("one", "two"),
        },
        "more reasons than refusals",
    ),
    (
        "a blank reason",
        {
            "labels_matched": 1,
            "candidates_generated": 1,
            "candidates_rejected": 1,
            "rejection_reasons": ("   ",),
        },
        "a refusal that explains nothing",
    ),
    (
        "a score with nothing judged",
        {"labels_matched": 0, "score": 0.9},
        "has no score rather than a score of zero",
    ),
    (
        "a value on a slot that was never scored",
        {"labels_matched": 0, "value": "1,234.00"},
        "took no characters off the page",
    ),
    (
        "candidates on a page that returned no rows",
        {
            "rows_received": 0,
            "rows_with_characters": 0,
            "labels_matched": 1,
            "candidates_generated": 1,
        },
        "nowhere for them to have come from",
    ),
)


@pytest.mark.parametrize(
    ("name", "override", "message"),
    INCONSISTENT,
    ids=[row[0] for row in INCONSISTENT],
)
def test_counts_that_contradict_each_other_are_refused_at_construction(
    name: str, override: dict[str, object], message: str
) -> None:
    counts: dict[str, object] = {
        "field": "total_paise",
        "rows_received": 40,
        "rows_with_characters": 38,
        "labels_considered": 10,
        "labels_matched": 0,
    }
    counts.update(override)
    # `in` on the rendered message rather than `pytest.raises(match=...)`,
    # which is a `re.search` and would quietly pass on a message that merely
    # happened to contain the pattern's regex meaning.
    with pytest.raises(ValueError) as refused:
        FieldTrace.at(**counts)  # type: ignore[arg-type]
    assert message in str(refused.value), (name, str(refused.value))


def test_a_hand_written_rung_that_disagrees_with_the_counts_is_refused() -> None:
    """`where` is derived, never asserted beside the numbers.

    This is the structural version of the whole module: a caller cannot label a
    page-with-rows as a reading failure, because the label is computed from the
    rows.
    """
    with pytest.raises(ValueError, match="derived from the numbers"):
        FieldTrace(
            field="total_paise",
            where=Where.NO_OCR_WORDS,
            rows_received=699,
            rows_with_characters=612,
            labels_considered=10,
            labels_matched=0,
            candidates_generated=0,
            candidates_rejected=0,
            candidates_judged=0,
            score=None,
            value=None,
            rejection_reasons=(),
            said="something",
        )


def test_a_blank_sentence_is_refused() -> None:
    with pytest.raises(ValueError, match="may not explain nothing"):
        FieldTrace(
            field="total_paise",
            where=Where.NO_OCR_WORDS,
            rows_received=0,
            rows_with_characters=0,
            labels_considered=10,
            labels_matched=0,
            candidates_generated=0,
            candidates_rejected=0,
            candidates_judged=0,
            score=None,
            value=None,
            rejection_reasons=(),
            said="   ",
        )


def test_a_judged_slot_with_no_score_raises_rather_than_printing_zero() -> None:
    """A missing confidence stays missing. It is never rendered as 0.00.

    `cage/decision.py` reads this number to decide whether a voucher posts by
    itself, and a fabricated zero is the exact shape of the defect this
    repository refuses everywhere else.
    """
    with pytest.raises(ValueError, match="inventing a confidence"):
        sentence_for(
            field="total_paise",
            where=Where.CANDIDATE_SCORED,
            rows_received=40,
            rows_with_characters=38,
            labels_considered=10,
            candidates_generated=1,
            candidates_rejected=0,
            candidates_judged=1,
            distinct_reasons=0,
            score=None,
        )


def test_a_page_refuses_a_field_that_was_handed_a_different_page() -> None:
    """The conflation again, one level up.

    A `FieldTrace` reporting 0 rows inside a page that returned 40 would print
    "nothing at all was read off this page" beside four sentences saying it was.
    """
    with pytest.raises(ValueError, match="read as a reading failure"):
        PageTrace(
            rows_received=40,
            rows_with_characters=38,
            fields=(
                a_scored_slot(),
                FieldTrace.at(
                    field="party",
                    rows_received=0,
                    rows_with_characters=0,
                    labels_considered=4,
                    labels_matched=0,
                ),
            ),
        )


def test_a_page_refuses_the_same_field_slot_twice() -> None:
    with pytest.raises(ValueError, match="died in two places died in neither"):
        PageTrace(
            rows_received=40,
            rows_with_characters=38,
            fields=(a_scored_slot(), a_scored_slot()),
        )


def test_a_page_refuses_more_rows_with_characters_than_rows() -> None:
    with pytest.raises(ValueError, match="more rows than arrived"):
        PageTrace(rows_received=3, rows_with_characters=4, fields=())


# =============================================================================
# the classifier is total
# =============================================================================


def test_where_from_answers_for_every_combination_it_can_be_handed() -> None:
    """No hole in the ladder, and the top rung is unconditional.

    Every combination of small counts, including the impossible ones a
    `FieldTrace` would refuse. `where_from` is a pure classification and must
    never fall through; and for every combination with rows and no candidates
    the answer is the label rung, whatever the other numbers say.
    """
    seen: set[Where] = set()
    for rows in range(3):
        for generated in range(3):
            for rejected in range(3):
                for judged in range(3):
                    answer = where_from(
                        rows_received=rows,
                        candidates_generated=generated,
                        candidates_rejected=rejected,
                        candidates_judged=judged,
                    )
                    assert isinstance(answer, Where)
                    seen.add(answer)
                    if rows > 0 and generated == 0:
                        assert answer is Where.OCR_PRESENT_FIELD_CANDIDATES_MISSING
                    if rows == 0:
                        assert answer is Where.NO_OCR_WORDS
    assert seen == set(Where)


def test_a_judged_candidate_outranks_a_refused_one() -> None:
    """One good total beside one unparseable one is a slot that was READ."""
    assert (
        where_from(
            rows_received=40,
            candidates_generated=2,
            candidates_rejected=1,
            candidates_judged=1,
        )
        is Where.CANDIDATE_SCORED
    )


def test_the_same_counts_give_the_same_sentence_every_time() -> None:
    """Determinism, from the outside: build each rung twice and compare.

    The syntax-tree guard below proves no clock or randomness is NAMED. This
    proves the output is stable, which is what a diff between two diagnostic
    runs depends on.
    """
    for name, counts, _, _ in RUNGS:
        first = FieldTrace.at(**counts)  # type: ignore[arg-type]
        second = FieldTrace.at(**counts)  # type: ignore[arg-type]
        assert first == second, name
        assert first.said == second.said, name
    assert a_page_carrying_the_document().said() == (
        a_page_carrying_the_document().said()
    )


# =============================================================================
# what this module may not do
# =============================================================================

#: The only top-level modules `trace.py` may reach for. Asserted as an exact set
#: rather than a subset: a new import here is a decision, and the point of this
#: module is that it evaluates identically on any machine with no fixtures.
ALLOWED_IMPORTS = frozenset({"__future__", "dataclasses", "enum", "typing"})

#: Identifiers that make an answer depend on WHEN or WHERE it was computed.
#: Checked as whole identifiers - `ast.Name.id` and `ast.Attribute.attr` - and
#: not as substrings, so a variable named `known` does not trip on `now`.
NON_DETERMINISTIC_NAMES = frozenset(
    {
        "choice",
        "environ",
        "getcwd",
        "getenv",
        "monotonic",
        "now",
        "perf_counter",
        "putenv",
        "random",
        "randint",
        "sample",
        "shuffle",
        "time",
        "today",
        "token_bytes",
        "token_hex",
        "uniform",
        "urandom",
        "utcnow",
        "uuid1",
        "uuid4",
    }
)

#: Anything that touches a disk, a socket or a subprocess.
IMPURE_NAMES = frozenset(
    {
        "PurePath",
        "Path",
        "communicate",
        "connect",
        "eval",
        "exec",
        "input",
        "open",
        "popen",
        "read_bytes",
        "read_text",
        "recv",
        "send",
        "system",
        "write_bytes",
        "write_text",
    }
)


def parsed() -> ast.Module:
    return ast.parse(TRACE_FILE.read_text(encoding="utf-8"))


def identifiers() -> set[str]:
    """Every name and every attribute this module mentions in code.

    String literals are deliberately excluded. The sentences this module builds
    contain the words `time` and `read`, and a substring scan over the source
    text would fail on its own prose - which is how a purity guard gets deleted
    for crying wolf.
    """
    names: set[str] = set()
    for node in ast.walk(parsed()):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
    return names


def imported_roots() -> set[str]:
    """The top-level module every import in this file reaches for.

    The same scan `tests/test_conservation.py::imported_roots` runs over the
    safety cage. Copied rather than imported across test files on purpose: a
    test that depends on another test file's helper fails for two reasons at
    once.
    """
    roots: set[str] = set()
    for node in ast.walk(parsed()):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            roots.add(node.module.split(".")[0])
    return roots


def test_the_module_is_there_to_inspect() -> None:
    """The control on every scan below.

    A misspelled path would make `identifiers()` return an empty set and every
    purity test would pass against nothing.
    """
    assert TRACE_FILE.name == "trace.py"
    assert len(identifiers()) > 20


def test_it_imports_exactly_the_standard_library_it_declares() -> None:
    assert imported_roots() == ALLOWED_IMPORTS


def test_it_names_no_clock_and_no_randomness() -> None:
    assert identifiers() & NON_DETERMINISTIC_NAMES == set()


def test_it_names_no_environment_variable_no_open_and_no_path() -> None:
    assert identifiers() & IMPURE_NAMES == set()


def test_the_guard_would_catch_a_clock_if_one_appeared() -> None:
    """The disconfirming check: prove the scan can fail.

    A guard nobody has watched fail is a guard that may be scanning the wrong
    thing. This parses a module that DOES name a clock and a file and shows both
    sets catch it.
    """
    guilty = ast.parse(
        "import datetime\n"
        "from pathlib import Path\n"
        "def when() -> str:\n"
        "    return str(datetime.datetime.now()) + Path('x').read_text()\n"
    )
    names: set[str] = set()
    for node in ast.walk(guilty):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
    assert names & NON_DETERMINISTIC_NAMES == {"now"}
    assert names & IMPURE_NAMES == {"Path", "read_text"}
