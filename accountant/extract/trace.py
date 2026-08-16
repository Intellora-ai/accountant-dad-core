"""Where a field slot died: at the page, at the label, or at the value.

WHY THIS FILE EXISTS
--------------------
`freeocr._judge` computes a field's confidence from the words handed to it. When
`_scores` returns None it produces ONE sentence, at `freeocr.py:863`:

    not_found: {backend} reported no word here that carries a confidence. -1 is
    its marker for a row with no score, and a marker is not a low score

That sentence blames the reading engine. It is printed for two situations that
have nothing to do with each other, because `_scores` returns None in two cases
and `_judge` cannot tell them apart:

    the engine returned no words at all         a reading failure
    the engine returned words, and none of      a VOCABULARY failure, on a page
    them were assigned to this slot             the engine read perfectly

`freeocr._scores` says so itself - *"None in two cases that are one fact"* - and
for its own purpose that is right: it is deciding whether it has numbers to
average. For the person reading the log it is not one fact, it is the difference
between "buy a better scanner" and "add a label to `labels.py`", and today they
read identically.

HOW OFTEN, MEASURED
--------------------
Measured on 2026-08-15 against this repository's own corpus, `data/`, using
`labels.values_for` and `labels.amounts_for` - the same matchers the shipping
readers use - over five field slots per document (date, party, total_paise,
tax_paise, net_paise).

MEASURED, the text-layer tier, `pypdf` text over all 80 corpus PDFs. 77 parsed;
3 were refused by `pypdf` itself and are excluded. 385 field slots:

      7   a label matched
    328   rows on the page, and no label this reader knows matched
     50   no rows at all (10 scanned PDFs carrying no text layer)

MEASURED, the reading-engine tier, `freeocr.read_lines` over the first 60 `.jpg`
documents in `data/`, tesseract 5.5.3. 300 field slots:

      3   a label matched
    267   word rows on the page, and no label matched
     30   no word rows at all (6 documents)

The widest page in that sample returned 699 word rows and matched a label for
ZERO of its five slots. Under today's sentence that document is logged as the
engine having reported nothing.

Across both tiers: 675 slots died, 595 of them (88.1%) on a page that had been
read, 80 of them (11.9%) on a page that had not. BOTH populations are real -
which is the point. A diagnostic that reported only the common case would be as
useless as one that reports only the rare one; what was wrong was reporting one
sentence for both.

WHAT THIS FILE IS
------------------
The ladder those counts came off, written down as a type. A `FieldTrace` carries
the counts at every rung and the ONE rung the slot fell off, and `Where` is
derived from the counts rather than asserted beside them - see `where_from`. A
`where` that could be set independently of the numbers it describes is a label
that drifts from its evidence, which is the defect this file exists to end.

THE RULE THAT IS NOT NEGOTIABLE
--------------------------------
`rows_received > 0` and `candidates_generated == 0` is
`WORDS_PRESENT_NO_CANDIDATES`, and its sentence may not contain
`OCR failed` or `could not read`. This is not a style preference: those 595
slots are pages the engine read correctly, and a log that calls them a reading
failure sends the owner to buy hardware for a problem that is four strings in
`labels.py`. The control is `_MISREADS_A_READ_PAGE`, it is enforced in
`FieldTrace.__post_init__` rather than only in a test, and `tests/test_trace.py`
asserts both the enum and the absence of the phrases.

NEVER THE DOCUMENT'S CONTENTS
------------------------------
`said` is built from counts and from field NAMES. It never interpolates a value,
a party, an amount or a rejection reason. `rejection_reasons` is carried on the
dataclass because a caller debugging one document needs it, and it is
deliberately NOT reachable from any sentence: the reasons come from
`labels.the_one`, which interpolates the disagreeing values straight into its
own text, so a sentence that quoted a reason would leak the document through the
diagnostic. `tests/test_trace.py` builds a page from distinctive values and
proves none of them reaches the output.

NO CLOCK, NO RANDOMNESS, NO ENVIRONMENT, NO FILESYSTEM, NO NETWORK, NO
THIRD-PARTY IMPORT. Counts in, sentences out, and the same counts give the same
sentences on any machine on any day. `tests/test_trace.py` walks this module's
own syntax tree and proves it, the way `tests/test_conservation.py` proves the
same about the safety cage.

WHAT THIS FILE DOES NOT PROVE
------------------------------
That a slot which reached `CANDIDATE_SCORED` is CORRECT. It says a value was
found under a label and scored; whether those characters are the right ones is
`freeocr`'s confidence and `cage/` decision's problem, and nothing here argues
with either.

That the counts handed in are TRUE. This module cannot see the page. It checks
that the numbers are consistent with one another - a slot cannot have judged
more candidates than it generated, and cannot have generated one on a page with
no rows - and it refuses at construction when they are not. It cannot check that
a caller which reports 40 rows was really handed 40.

That the vocabulary in `labels.py` should be WIDER. It says 595 slots died at
the label rung. Which of those labels are worth adding is a measurement on the
documents themselves, and `labels.PARTY_LABELS` carries the record of seven
labels proposed on 2026-08-15 that produced seven values and seven wrong ones.
Naming the rung is not an argument for climbing it.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Final

# =============================================================================
# the rungs
# =============================================================================


class Where(enum.Enum):
    """The rung a field slot fell off, and there is exactly one per slot.

    ORDERED FROM THE PAGE INWARDS, and the order is the whole diagnostic: each
    member describes strictly more work having been done than the one above it.
    A caller reading the member alone learns which of five completely different
    people has to do something about it - the person holding the scanner, the
    person holding `labels.py`, the person holding the document, the person
    holding the validation rules, or nobody.

    NOT A `StrEnum`, unlike `labels.Printing` next door. A `Where` is compared
    to a `Where` and never to a string: `StrEnum` would make
    `trace.where == "no_ocr_words"` quietly true, and a diagnostic that answers
    True to a misspelled comparison is a diagnostic that stops being checked.
    The wire value is still a plain string on `.value` for anything that logs.
    """

    #: Nothing came back. The engine reported zero word rows for this page, so
    #: no label could be looked for and no vocabulary is at fault. MEASURED: 80
    #: of 675 dead slots across both tiers, and every one of them came from a
    #: document with no text layer and no legible ink - never from a page that
    #: was read.
    NO_WORDS_AT_ALL = "no_ocr_words"

    #: The page was read and the vocabulary missed. Rows came back carrying
    #: characters, and not one label this reader knows for this field appeared
    #: among them. MEASURED: 595 of 675 dead slots, 88.1%. This is the common
    #: case by a wide margin and the one today's single sentence hides.
    WORDS_PRESENT_NO_CANDIDATES = "ocr_present_field_candidates_missing"

    #: The label was there and nothing followed it. A candidate site was
    #: located under a label this reader knows, and the characters at that site
    #: were empty - which is a fact about the DOCUMENT, not about the engine or
    #: the vocabulary.
    LABEL_MATCHED_NO_VALUE = "label_matched_no_value"

    #: Characters were found and a rule refused them. Every candidate reached a
    #: validation check - `paise_or_none` refusing a sub-paise figure,
    #: `fromisoformat` refusing a date, `the_one` refusing a disagreement - and
    #: none survived. The reasons are on `FieldTrace.rejection_reasons` and
    #: never in a sentence.
    CANDIDATE_REJECTED_BY_VALIDATION = "candidate_rejected_by_validation"

    #: At least one candidate survived to be scored. This is the only member
    #: that is not a death, and the only one under which `score` and `value` may
    #: be anything but None.
    CANDIDATE_SCORED = "candidate_scored"


#: Phrases that are TRUE only when nothing was read, and are therefore forbidden
#: everywhere else. This is the control for the defect at the top of this file:
#: a page carrying 699 word rows, logged as a reading failure because one label
#: was missing. Checked case-insensitively in `FieldTrace.__post_init__`, so the
#: guard holds against a sentence that says `OCR Failed`, and it is a
#: `ValueError` at construction rather than a warning nobody reads.
#:
#: `NO_WORDS_AT_ALL` is exempt because there the phrases are simply accurate. It
#: does not use them - the sentence below says what happened in plainer words -
#: but a future wording that did would not be lying.
_MISREADS_A_READ_PAGE: Final[tuple[str, ...]] = ("ocr failed", "could not read")


# =============================================================================
# deriving the rung from the counts
# =============================================================================


def _refuse_a_score_that_is_not_a_confidence(field: str, score: float | None) -> None:
    """A score is `None` or a confidence between 0.0 and 1.0. Nothing else.

    THE ASYMMETRY THIS CLOSES. `__post_init__` range-checks all seven integer
    counts and applied ZERO checks to the one float - and the float is the
    quantity `cage/decision.py` gates auto-posting on at 0.95. Measured before
    this existed: -1.0, 87.0, NaN and inf all constructed cleanly and printed as
    "scored -1.00", "scored 87.00", "scored nan", "scored inf".

    -1 IS THE ONE TO NAME. `freeocr.NO_SCORE_MARKER` is -1 and it means "no text
    here"; `cage/confidence.field_confidence` already raises on it with the
    reason that guessing which scale a number outside the range is on would be
    inventing data. A diagnostic that printed it as a confidence would be the
    same invention with a friendlier font.

    NaN is refused separately because it fails every comparison silently - it is
    neither below 0.0 nor above 1.0, so a range check alone lets it through.
    """
    if score is None:
        return
    # The ignore is the evidence, not a workaround: pyright calls this check
    # unnecessary BECAUSE it trusts the annotation, and a caller handing this a
    # string or a bool is exactly the case the annotation cannot prevent. A
    # checker that cannot see the defect is not a reason to delete the guard.
    if not isinstance(  # pyright: ignore[reportUnnecessaryIsInstance]
        score, float | int
    ) or isinstance(score, bool):
        raise TypeError(
            f"{field}: a score is a confidence or None, not {type(score).__name__}."
        )
    if score != score:  # NaN, which fails every comparison including this one
        raise ValueError(
            f"{field}: a score of NaN is not a confidence. It is neither below "
            "0.0 nor above 1.0, so a range check alone would let it reach the "
            "auto-post band unnoticed."
        )
    if not 0.0 <= score <= 1.0:
        raise ValueError(
            f"{field}: a score of {score} is outside 0.0-1.0. Tesseract reports "
            "-1 for 'no text here', which is a marker and not a score, and a "
            "0-100 word confidence is a different scale entirely; guessing "
            "which one a number outside the range is on would be inventing data."
        )


def where_from(
    *,
    rows_received: int,
    candidates_generated: int,
    candidates_rejected: int,
    candidates_judged: int,
    score: float | None = None,
) -> Where:
    """The one rung these counts describe.

    A FUNCTION AND NOT A FIELD THE CALLER FILLS IN. `FieldTrace.__post_init__`
    calls this and refuses a `where` that disagrees with it, so the enum cannot
    drift from the numbers underneath it. The alternative - trusting the caller
    - is how `freeocr` ended up with one sentence for two situations in the
    first place: the sentence was written where the counts were not in scope.

    THE ORDER OF THESE FOUR TESTS IS THE DIAGNOSTIC AND IS NOT ARBITRARY.

    1. No rows means nothing was looked at, whatever else is true.
    2. No candidates on a page that HAS rows is the label rung, and it is
       unconditional - there is no reading of the counts under which a page with
       rows and no candidates was a reading failure. This is the rule the
       docstring at the top of this file calls not negotiable.
    3. Anything judged means the slot survived, even if other candidates on the
       same page were refused. A slot with one good total and one unparseable
       one was read.
    4. Only then does a rejection get to name the rung.

    WHY `candidates_generated == 0` AND NOT `labels_matched == 0` AT STEP 2,
    given that `FieldTrace` requires the two to be zero together. Because the
    rungs below count candidates, and a ladder that switched which quantity it
    tested halfway down is a ladder with a seam in it. The invariant tying them
    is stated once, in `__post_init__`, where it can be read as one line.
    """
    if rows_received == 0:
        return Where.NO_WORDS_AT_ALL
    if candidates_generated == 0:
        return Where.WORDS_PRESENT_NO_CANDIDATES
    if candidates_judged > 0 and score != 0.0:
        return Where.CANDIDATE_SCORED
    # A JUDGED CANDIDATE SCORING EXACTLY 0.0 WAS NOT READ, AND THIS FILE HAD IT
    # THE OTHER WAY ROUND. Corrected 2026-08-15 after an adversarial review.
    #
    # This module's own field docstring used to say "a zero score is a real
    # reading the engine had no confidence in". In THIS repository that is
    # false, and `freeocr.py:108-111` states the rule it contradicts:
    # "confidence 0.0 means no value - ONE rule, so the record and the
    # observation cannot disagree about what was read. A field is carried only
    # when a confidence above zero can be stated for it."
    #
    # `freeocr._judge` returns 0.0 together with a NOT_FOUND sentence on all
    # four of its failure branches, and `freeocr` then writes
    # `total_paise=total if total_score > 0.0 else None`. So the natural wiring
    # - hand this the score `_judge` returned - produced a trace saying
    # "total_paise was read and scored 0.00" for a slot the reader had set to
    # None. A fabricated success, and the exact conflation this file exists to
    # end, reintroduced one layer up.
    if candidates_rejected > 0 or candidates_judged > 0:
        return Where.CANDIDATE_REJECTED_BY_VALIDATION
    return Where.LABEL_MATCHED_NO_VALUE


def _count_of(number: int, thing: str) -> str:
    """`1 candidate`, `3 candidates`, `0 candidates`.

    Written here rather than reached for in a library, because the runtime
    dependencies of this project are exactly `pypdf`, `pytesseract` and
    `Pillow`, and a fourth fails
    `test_the_project_declares_exactly_the_dependencies_the_owner_approved`.
    Every word this module pluralises takes a plain `s`; the day one does not,
    this function gets a table and not an import.
    """
    return f"{number} {thing}" if number == 1 else f"{number} {thing}s"


def _verb(number: int) -> str:
    """`was` for one of something, `were` for none or many.

    Here for the same reason as `_count_of`: a diagnostic that reads
    *"1 were read and scored"* is a diagnostic the owner stops trusting, and
    trust is the only thing this module produces.
    """
    return "was" if number == 1 else "were"


def sentence_for(
    *,
    field: str,
    where: Where,
    rows_received: int,
    rows_with_characters: int,
    labels_considered: int,
    candidates_generated: int,
    candidates_rejected: int,
    candidates_judged: int,
    distinct_reasons: int,
    score: float | None,
) -> str:
    """One plain sentence saying where this slot died, for a non-programmer.

    ONE SENTENCE, and the field NAME appears in every one of them. A page trace
    is five of these in a row, and a reader who has to carry "which field is
    this about" from a heading three lines up will get it wrong on the document
    where it matters.

    BUILT FROM COUNTS AND FROM `field` ONLY. No value, no party, no amount, no
    rejection reason. `rejection_reasons` is reported here as a NUMBER because
    the reasons themselves are written by `labels.the_one`, which interpolates
    the disagreeing values into its own text - quoting one would put the
    document's contents in the log through the back door. That is why
    `distinct_reasons` is an `int` in this signature and not the tuple.

    `score` IS FORMATTED TO TWO PLACES rather than printed raw. `repr(0.1+0.2)`
    is the reason: a confidence rendered as 0.30000000000000004 reads as a bug
    in the reader to the person the sentence is written for.

    A SCORED SLOT WITH NO SCORE RAISES RATHER THAN PRINTING 0.00. Substituting
    a zero for a number that was never taken is the one thing this repository
    forbids everywhere - a missing figure stays missing and says so - and a
    confidence is the figure `cage/decision.py` reads to decide whether a
    voucher posts by itself.
    """
    if where is Where.NO_WORDS_AT_ALL:
        return (
            f"Nothing at all was read off this page - the reader was handed "
            f"{_count_of(rows_received, 'word row')} - so {field} was never "
            f"looked for."
        )
    if where is Where.WORDS_PRESENT_NO_CANDIDATES:
        return (
            f"This page was read - {rows_with_characters} of "
            f"{_count_of(rows_received, 'word row')} carry characters - and "
            f"{field} is missing only because not one of the "
            f"{_count_of(labels_considered, 'label')} this reader knows for it "
            f"appears anywhere on the page."
        )
    if where is Where.LABEL_MATCHED_NO_VALUE:
        return (
            f"A label for {field} was found on this page "
            f"{_count_of(candidates_generated, 'time')}, and nothing was "
            f"printed after it, so there is no value to take."
        )
    if where is Where.CANDIDATE_REJECTED_BY_VALIDATION:
        return (
            f"Every one of the {_count_of(candidates_generated, 'candidate')} "
            f"found under a {field} label was refused by a validation check "
            f"({_count_of(candidates_rejected, 'refusal')}, "
            f"{_count_of(distinct_reasons, 'distinct reason')} recorded "
            f"beside this trace and deliberately not repeated in words), so "
            f"{field} stays unread."
        )
    if score is None:
        raise ValueError(
            f"{field}: {candidates_judged} candidates were judged and no score "
            f"came with them; a sentence that filled that in as 0.00 would be "
            f"inventing a confidence nothing measured"
        )
    return (
        f"{field} was read and scored {score:.2f}, from "
        f"{_count_of(candidates_judged, 'candidate')} judged out of the "
        f"{_count_of(candidates_generated, 'candidate')} found under a label "
        f"on this page."
    )


# =============================================================================
# one field slot
# =============================================================================


@dataclass(frozen=True)
class FieldTrace:
    """One field slot on one page, every count it passed, and where it stopped.

    FROZEN, for the reason `labels.Found` and the nine types in `schema.py` are
    frozen: a diagnostic that can be edited after the fact is not evidence about
    what happened, and this one exists specifically to be quoted back at a
    reader that lied.

    EVERY INVARIANT BELOW IS CHECKED AT CONSTRUCTION AND RAISES. A `FieldTrace`
    that is internally inconsistent is worse than no trace at all, because it
    will be believed - the whole complaint against `freeocr._judge` is that its
    sentence was believed. `ValueError` and not a warning, at the site that
    built the wrong numbers, naming the field.
    """

    #: The slot's name, as the rest of this repository spells it - `total_paise`
    #: and not `total`. `adapter.ExtractedRecord.FIELDS` is the vocabulary; this
    #: is deliberately a plain `str` rather than that tuple's members, because
    #: `net_paise` is traced and is not one of them, for the reason
    #: `freeocr._Answer.refused` writes out.
    field: str

    #: The rung, derived from the counts by `where_from` and re-derived here.
    where: Where

    #: Word rows the reader was handed for this page. ZERO IS THE ONLY VALUE
    #: THAT MEANS A READING FAILURE, and that is the entire content of this
    #: module.
    rows_received: int

    #: Of those rows, how many carry at least one character. A row that came
    #: back empty is still a row the engine reported, and the gap between these
    #: two numbers is how a caller sees a page that was scanned blank rather
    #: than not scanned.
    rows_with_characters: int

    #: How many labels this reader knows for this field - `len(TOTAL_LABELS)`
    #: and so on. Reported because "no label matched" means nothing without it:
    #: no label matching out of ten is a vocabulary that missed, and no label
    #: matching out of zero is a reader that was never taught this field.
    labels_considered: int

    #: How many of those labels appeared on the page.
    labels_matched: int

    #: Candidate sites located under a matched label. A candidate is a label hit
    #: that produced a span, which is exactly what `labels.values_for` returns -
    #: it drops a blank value rather than reporting it, so a label followed by
    #: nothing never becomes a candidate here either.
    candidates_generated: int

    #: Of those, how many a validation rule refused.
    candidates_rejected: int

    #: Of those, how many survived to be scored. `rejected + judged` may be less
    #: than `generated`: a candidate whose characters were empty is neither
    #: refused by a rule nor judged, and that gap is `LABEL_MATCHED_NO_VALUE`.
    candidates_judged: int

    #: The confidence, when something was judged. `None` when nothing was.
    #:
    #: 0.0 IS NOT A LOW CONFIDENCE IN THIS REPOSITORY, IT IS THE ABSENCE OF A
    #: VALUE. This comment used to say the opposite - "a zero score is a real
    #: reading the engine had no confidence in" - and that was contradicted one
    #: module away. `freeocr.py:108-111`: "confidence 0.0 means no value - ONE
    #: rule, so the record and the observation cannot disagree about what was
    #: read." `freeocr._judge` returns 0.0 alongside a NOT_FOUND sentence on all
    #: four of its failure branches, and `freeocr` then writes
    #: `total_paise=total if total_score > 0.0 else None`.
    #:
    #: So `where_from` treats a judged candidate scoring exactly 0.0 as refused
    #: rather than scored. Without that, the obvious wiring - hand this the
    #: score `_judge` returned - produced "total_paise was read and scored 0.00"
    #: for a slot the reader had set to `None`: a fabricated success, and the
    #: very conflation this file exists to end, reintroduced one layer up.
    #:
    #: RANGE-CHECKED AT CONSTRUCTION, like the seven counts beside it. It was
    #: the only unvalidated field here, and it is the one `cage/decision.py`
    #: reads at `AUTO_POST_FLOOR`.
    score: float | None

    #: The characters that were taken, when a slot was scored. None otherwise.
    #: Present so a caller debugging one document has it; it is never reachable
    #: from `said`.
    value: str | None

    #: Why each refused candidate was refused, deduplicated. Carried, never
    #: spoken - see the module docstring.
    rejection_reasons: tuple[str, ...]

    #: The sentence. Never empty, and refused at construction if it is: a trace
    #: whose explanation is blank is the silent-blank defect
    #: `adapter.ExtractedRecord` exists to make impossible, in the one field
    #: whose whole job is to explain.
    said: str

    def __post_init__(self) -> None:
        _refuse_a_score_that_is_not_a_confidence(self.field, self.score)
        where = where_from(
            rows_received=self.rows_received,
            candidates_generated=self.candidates_generated,
            candidates_rejected=self.candidates_rejected,
            candidates_judged=self.candidates_judged,
            score=self.score,
        )
        counts = {
            "rows_received": self.rows_received,
            "rows_with_characters": self.rows_with_characters,
            "labels_considered": self.labels_considered,
            "labels_matched": self.labels_matched,
            "candidates_generated": self.candidates_generated,
            "candidates_rejected": self.candidates_rejected,
            "candidates_judged": self.candidates_judged,
        }
        for name, number in counts.items():
            if number < 0:
                raise ValueError(
                    f"{self.field}: {name} is {number}, and a count of "
                    f"something that happened cannot be negative"
                )
        if self.rows_with_characters > self.rows_received:
            raise ValueError(
                f"{self.field}: {self.rows_with_characters} rows carry "
                f"characters out of {self.rows_received} rows received, which "
                f"is more rows than arrived"
            )
        if self.labels_matched > self.labels_considered:
            raise ValueError(
                f"{self.field}: {self.labels_matched} labels matched out of "
                f"{self.labels_considered} considered, which is more labels "
                f"than this reader knows"
            )
        accounted = self.candidates_rejected + self.candidates_judged
        if accounted > self.candidates_generated:
            raise ValueError(
                f"{self.field}: {self.candidates_rejected} candidates refused "
                f"plus {self.candidates_judged} judged is more than the "
                f"{self.candidates_generated} generated"
            )
        # A candidate is a located label hit, so the two are zero together or
        # neither is. Without this, a slot could report `labels_matched=3,
        # candidates_generated=0` and `where_from` would call it
        # WORDS_PRESENT_NO_CANDIDATES - whose sentence says no label
        # appeared, which would be false on that page. The critical rule at the
        # top of this file is unconditional ONLY because this invariant holds.
        if (self.labels_matched == 0) != (self.candidates_generated == 0):
            raise ValueError(
                f"{self.field}: {self.labels_matched} labels matched and "
                f"{self.candidates_generated} candidates were generated, and "
                f"those two are zero together or not at all - a label that "
                f"located no span is not a match, which is what "
                f"labels.values_for already does with a blank value"
            )
        if self.rows_received == 0 and self.candidates_generated > 0:
            raise ValueError(
                f"{self.field}: {self.candidates_generated} candidates were "
                f"generated on a page that returned no rows at all, and there "
                f"is nowhere for them to have come from"
            )
        if where is not self.where:
            raise ValueError(
                f"{self.field}: these counts are {where.value} and the trace "
                f"says {self.where.value}; `where` is derived from the numbers "
                f"by `where_from` and is never set beside them"
            )
        if self.candidates_judged == 0 and self.score is not None:
            raise ValueError(
                f"{self.field}: a score of {self.score} was recorded with no "
                f"candidate judged, and a slot nothing was judged for has no "
                f"score rather than a score of zero"
            )
        if where is Where.CANDIDATE_SCORED and self.score is None:
            raise ValueError(
                f"{self.field}: {self.candidates_judged} candidates were "
                f"judged and no score was recorded"
            )
        if where is not Where.CANDIDATE_SCORED and self.value is not None:
            raise ValueError(
                f"{self.field}: a value was recorded on a slot that stopped at "
                f"{where.value}, and a slot that was never scored took no "
                f"characters off the page"
            )
        if (self.candidates_rejected == 0) != (not self.rejection_reasons):
            raise ValueError(
                f"{self.field}: {self.candidates_rejected} candidates were "
                f"refused and {len(self.rejection_reasons)} reasons were "
                f"recorded, and a refusal with no reason is the thing this "
                f"module exists to stop"
            )
        if len(self.rejection_reasons) > self.candidates_rejected:
            raise ValueError(
                f"{self.field}: {len(self.rejection_reasons)} distinct reasons "
                f"were recorded for {self.candidates_rejected} refusals, which "
                f"is more reasons than refusals"
            )
        if any(not reason.strip() for reason in self.rejection_reasons):
            raise ValueError(
                f"{self.field}: one of the rejection reasons is blank, which "
                f"is a refusal that explains nothing"
            )
        if not self.said.strip():
            raise ValueError(
                f"{self.field}: `said` is blank, and a trace whose whole job "
                f"is to explain where a field died may not explain nothing"
            )
        # THE CONTROL. See `_MISREADS_A_READ_PAGE` and the module docstring.
        if where is not Where.NO_WORDS_AT_ALL:
            spoken = self.said.lower()
            for phrase in _MISREADS_A_READ_PAGE:
                if phrase in spoken:
                    raise ValueError(
                        f"{self.field}: the sentence for {where.value} says "
                        f"{phrase!r}, on a page that returned "
                        f"{self.rows_received} rows. That phrase is true only "
                        f"when nothing was read, and saying it here is exactly "
                        f"the defect this module was built to end"
                    )

    @classmethod
    def at(
        cls,
        *,
        field: str,
        rows_received: int,
        rows_with_characters: int,
        labels_considered: int,
        labels_matched: int,
        candidates_generated: int = 0,
        candidates_rejected: int = 0,
        candidates_judged: int = 0,
        score: float | None = None,
        value: str | None = None,
        rejection_reasons: tuple[str, ...] = (),
    ) -> FieldTrace:
        """The canonical constructor: counts in, rung and sentence derived.

        THE ONLY WAY A CALLER SHOULD BUILD ONE. Constructing `FieldTrace`
        directly is legal and every invariant still holds, because
        `__post_init__` runs either way - but then the caller has to state
        `where` and `said` itself, and those are the two things this module
        exists to stop anybody stating by hand.

        KEYWORD-ONLY, WITH NO POSITIONAL FORM. Seven of these arguments are
        `int`, adjacent, and mean different things; a positional call site is
        one edit away from swapping `candidates_rejected` and
        `candidates_judged`, which turns a refused slot into a scored one in a
        log nobody re-reads. `labels.values_for` makes `printing` keyword-only
        for the same reason and says so.
        """
        where = where_from(
            rows_received=rows_received,
            candidates_generated=candidates_generated,
            candidates_rejected=candidates_rejected,
            candidates_judged=candidates_judged,
            score=score,
        )
        return cls(
            field=field,
            where=where,
            rows_received=rows_received,
            rows_with_characters=rows_with_characters,
            labels_considered=labels_considered,
            labels_matched=labels_matched,
            candidates_generated=candidates_generated,
            candidates_rejected=candidates_rejected,
            candidates_judged=candidates_judged,
            score=score,
            value=value,
            rejection_reasons=rejection_reasons,
            said=sentence_for(
                field=field,
                where=where,
                rows_received=rows_received,
                rows_with_characters=rows_with_characters,
                labels_considered=labels_considered,
                candidates_generated=candidates_generated,
                candidates_rejected=candidates_rejected,
                candidates_judged=candidates_judged,
                distinct_reasons=len(rejection_reasons),
                score=score,
            ),
        )


# =============================================================================
# one page
# =============================================================================


@dataclass(frozen=True)
class PageTrace:
    """Every field slot on one page, and the paragraph a person reads.

    THE PAGE COUNTS ARE HELD HERE AND CHECKED AGAINST EVERY FIELD. All the
    slots on a page were handed the same rows, so a `FieldTrace` claiming zero
    rows inside a `PageTrace` reporting 699 is precisely the conflation this
    module exists to end - and it would be reported by that field's own sentence
    as a reading failure. It raises instead.
    """

    #: Word rows the engine returned for this page, once, for all fields.
    rows_received: int

    #: How many of them carry at least one character.
    rows_with_characters: int

    #: One per field slot. Ordered as the caller built them, because a diagnostic
    #: that re-sorts its own output cannot be diffed between two runs.
    fields: tuple[FieldTrace, ...]

    def __post_init__(self) -> None:
        if self.rows_received < 0 or self.rows_with_characters < 0:
            raise ValueError(
                f"a page cannot have {self.rows_received} rows and "
                f"{self.rows_with_characters} of them carrying characters; "
                f"neither count can be negative"
            )
        if self.rows_with_characters > self.rows_received:
            raise ValueError(
                f"{self.rows_with_characters} of this page's rows carry "
                f"characters out of {self.rows_received} received, which is "
                f"more rows than arrived"
            )
        names = [trace.field for trace in self.fields]
        if len(set(names)) != len(names):
            raise ValueError(
                f"this page traces the same field slot more than once "
                f"({', '.join(sorted(names))}), and a field that died in two "
                f"places died in neither"
            )
        for trace in self.fields:
            if trace.rows_received != self.rows_received:
                raise ValueError(
                    f"{trace.field} reports {trace.rows_received} rows on a "
                    f"page that received {self.rows_received}; every slot on a "
                    f"page was handed the same rows, and a slot that says "
                    f"otherwise will be read as a reading failure"
                )
            if trace.rows_with_characters != self.rows_with_characters:
                raise ValueError(
                    f"{trace.field} reports {trace.rows_with_characters} rows "
                    f"carrying characters on a page where "
                    f"{self.rows_with_characters} do"
                )

    def counted(self, where: Where) -> int:
        """How many slots on this page stopped at that rung."""
        return sum(1 for trace in self.fields if trace.where is where)

    def said(self) -> str:
        """The page's headline, then one sentence per field slot.

        A METHOD AND NOT A FIELD, unlike `FieldTrace.said`. A field's sentence
        is the thing being asserted about and has to be storable and frozen; a
        page's is a rendering of the fields it already holds, and storing it
        would create a second place where the page's story lives.

        BUILT FROM COUNTS AND FIELD NAMES ONLY, like every sentence here. The
        privacy test in `tests/test_trace.py` runs against this output, so a
        future line that interpolated `trace.value` fails there.

        Newline-separated rather than one paragraph, because five sentences
        about five different fields run together is the format the owner has to
        read at the moment a bill did not post.
        """
        scored = self.counted(Where.CANDIDATE_SCORED)
        no_label = self.counted(Where.WORDS_PRESENT_NO_CANDIDATES)
        no_value = self.counted(Where.LABEL_MATCHED_NO_VALUE)
        refused = self.counted(Where.CANDIDATE_REJECTED_BY_VALIDATION)
        no_rows = self.counted(Where.NO_WORDS_AT_ALL)
        head = (
            f"This page returned "
            f"{_count_of(self.rows_received, 'word row')}, "
            f"{self.rows_with_characters} of them carrying characters. "
            f"Of {_count_of(len(self.fields), 'field slot')}: "
            f"{scored} {_verb(scored)} read and scored, "
            f"{no_label} stopped because no label this reader knows appeared "
            f"on a page it had read, "
            f"{no_value} found a label with nothing printed after it, "
            f"{refused} {_verb(refused)} refused by a validation check, and "
            f"{no_rows} never got a page to look at."
        )
        return "\n".join([head, *(f"  - {trace.said}" for trace in self.fields)])
