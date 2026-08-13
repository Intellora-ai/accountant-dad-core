"""The free reading engine's half of the job that is not reading.

WHY THIS FILE EXISTS
--------------------
The decision bands are written per field and auto-post needs 0.95 or better on
every one of them. A paid model hands you that number. The free engine this
product is built around does not: it reports a confidence per WORD, 0 to 100,
and something has to turn a handful of word scores into one field score that a
person can be shown and a gate can be run against.

`accountant/cage/confidence.py::field_confidence` is that arithmetic and it
already exists. What did not exist is the thing that FEEDS it: something that
takes what the engine reported, throws out everything that is not a score,
proves the text is the shape the field claims to be, checks the amounts against
each other, and then either states a confidence or refuses in a sentence.

That is this file. It is the reading engine's policy, not its plumbing.

WHY TESSERACT IS THE ENGINE THIS IS SHAPED FOR
----------------------------------------------
Decided, and the reasons are testable rather than tasteful:

    it is deterministic     the same bytes give byte-identical output. MEASURED
                            on this machine, engine 5.5.3, 10 consecutive runs
                            each of a clean synthetic invoice, the same page
                            scaled down to a third, and the same page under
                            heavy speckle: 1 distinct output in all three cases.
                            The neural engines sample, and a reading that is not
                            reproducible cannot be evidence about a document.

    it reports per-word     `image_to_data` gives one row per word with a
    confidence              confidence 0-100. That is the input `field_confidence`
                            was written for. An engine that reports one number
                            for the page cannot answer "how sure are you about
                            the total", which is the only question being asked.

    it is 30KB of wrapper   `pytesseract` is a subprocess wrapper. The
                            alternatives bring a 2GB tensor runtime along, which
                            is a supply chain, an install failure and a machine
                            requirement, in exchange for accuracy nobody here
                            has measured.

THE ENGINE CALL IS HERE, AND IT IS HERE BY OWNER DECISION
----------------------------------------------------------
`read_words` below is the call. Until 2026-08-13 it could not have been:
`tests/test_no_reader.py` forbade every third-party import in this package and
required `pyproject.toml` to declare `dependencies = []`. `D-30` in
`docs/DECISIONS.md` changed that, by name and by module - `freeocr.py` may
import `pytesseract` and `PIL` and may name `subprocess`, and nothing else may.
Widening that list is still an owner decision and still not a test change.

Two seams, not one, and they are different jobs:

    read_words      bytes -> every word on the page, with its confidence. This
                    is the engine, and it is real.
    PageReader      bytes -> a `Reading`, which says WHICH words make up the
                    total, the tax, the date and the party. Injected.

WHAT IS DELIBERATELY NOT BUILT, AND WHY IT IS NOT AN OVERSIGHT
---------------------------------------------------------------
The second seam is empty. Nothing in this repository turns a list of words into
"this one is the total", and this file does not either.

That is field detection, and it is the one part of reading a bill that cannot
be checked without labelled data. `H-02` - a pile of invoices where somebody
already knows the right answer - does not exist here. A heuristic written
without it would be unmeasured, unfalsifiable and confident, which is the exact
combination this whole cage exists to keep away from a customer's books. The
engine's own confidence would not catch it either: `field_confidence` scores
how legible a word was, not whether it was the right word to look at.

So the gap is stated rather than filled. `docs/OCR.md` carries it as the next
piece of work and names what it needs first.

WHAT IS ACTUALLY GUARDED HERE, SO THE INJECTION IS NOT AN EXCUSE
-----------------------------------------------------------------
    no user input reaches   this module builds no command and no string that a
    a program               shell could see. The bytes go across as bytes, and
                            the only other thing that crosses is a media type
                            that has already been matched against the fixed
                            tuple `READABLE_MEDIA` - so the value handed on is
                            one of five constants written in this file, never
                            whatever a caller typed into an HTTP header.

    -1 is a marker, never   the engine writes -1 on every row that carries no
    a score                 score, which is every structural row and every word
                            it found no text in. MEASURED: 19 of 43 rows on the
                            clean invoice. Read as a number it is a confidence
                            below zero; averaged in it drags a field down; taken
                            as 0-100 it is out of range. It is none of those. A
                            field with a marker among its words has no score.

    a missing engine and    both arrive here as an exception from the injected
    a timeout are refusals  reader and leave as an all-`not_found` record with
                            the sentence on every field. `extract` never raises,
                            because `pipeline.build_draft` has no try around it
                            and an exception there is an HTTP 503 telling a
                            person the application broke when the truth is that
                            a program is not installed.

    confidence 0.0 means    ONE rule, so the record and the observation cannot
    no value                disagree about what was read. A field is carried
                            only when a confidence above zero can be stated for
                            it. Everything else is `None`, 0.0, and a sentence.

WHAT THIS FILE DOES NOT PROVE
-----------------------------
That anything was read. Nothing here reads. Handed a `PageReader` that invents
its answer, this produces a confident record about an invented bill, and no
arithmetic in this file could tell. `S2 = NOT_MEASURED` is untouched by it.

That the engine is accurate. Determinism is not accuracy: an engine that
misreads the same digit the same way ten times out of ten is exactly as wrong
and perfectly reproducible. Accuracy needs the labelled corpus (`H-02`) that
this repository does not have.

That a high confidence means a right answer. Failure mode F-02 is the engine
reporting 96 on a digit it got wrong, and no score computed from the engine's
own opinion can see that. This is one input to the decision layer, which also
requires every conservation law and an open period before anything is written.

That handwriting works. It does not, it is not claimed, and `docs/OCR.md` says
so in the same words.
"""

from __future__ import annotations

import datetime
import io
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final, cast

# `D-30`, owner decision 2026-08-13, names this module and these two libraries.
# Neither ships type stubs, so strict mode is told once, here, rather than at
# every call site - and the run-time checks in `_complaint` are what actually
# hold, because a stub would only have been a promise anyway.
import pytesseract  # pyright: ignore[reportMissingTypeStubs]
from PIL import Image
from pytesseract import Output  # pyright: ignore[reportMissingTypeStubs]

from accountant.cage.confidence import field_confidence, looks_like_a_date
from accountant.cage.conservation import Verdict, net_plus_tax_equals_gross
from accountant.cage.wall import Field, Observation

# The two private names below are imported rather than copied, and pyright is
# right to object: they are private and this is another module. It is the
# lesser of two wrongs, and the greater one is measured. `_to_paise` is the
# ONE rupees-to-paise rule on the reading side; `adapter.py` carries the case
# where a second, more lenient conversion put `10.005` into the books as ten
# rupees exactly, and a third copy here is how that happens again. `_media_type`
# is the ONE answer to what `text/plain; charset=utf-8` means. Copying either to
# satisfy the checker would trade a warning for a divergence.
#
# `tallyio.real.paise_from_rupees` is public and was considered instead. It is
# the wrong rule HERE: it strips spaces, so two words the engine reported
# separately - `5664` and `0.00` - would silently glue into one amount. On this
# side of the system a space between digits is evidence, not noise.
from accountant.extract.adapter import (
    NOT_FOUND,
    ExtractedRecord,
    UnavailableExtractor,
    _media_type,  # pyright: ignore[reportPrivateUsage]
    _to_paise,  # pyright: ignore[reportPrivateUsage]
)

#: The five media types this adapter will hand on to a reader, and the whole of
#: the reason a caller's header never travels: the value passed across is one of
#: these constants or the document is refused. PDF is deliberately absent - the
#: engine does not take one, and a page-splitter is a second component nobody
#: has chosen.
READABLE_MEDIA: Final[tuple[str, ...]] = (
    "image/png",
    "image/jpeg",
    "image/tiff",
    "image/bmp",
    "image/webp",
)

#: What the engine writes in the confidence column of a row that has no score.
#: It is a MARKER. It is not a low confidence, it is not zero, and it is not on
#: the 0-100 scale at all. `confidence.field_confidence` refuses it with a
#: ValueError for exactly that reason; this module never lets one get that far,
#: because a raised exception in the reading path is a 503 page.
NO_SCORE_MARKER: Final = -1

# ---- the sentences a person reads when nothing was read ---------------------
#
# Named, so a test can prove a scenario produced THIS refusal and not merely
# some refusal. A message nobody can pin is a message that drifts into silence.

ENGINE_MISSING: Final = "the text reading program is not installed on this machine"
ENGINE_TIMED_OUT: Final = "the text reading program did not finish in time"
ENGINE_NOT_ALLOWED: Final = "this machine will not let us run the text reading program"
ENGINE_FAILED: Final = "the text reading program could not read this file"
UNREADABLE_MEDIA: Final = "this file is not a kind of picture we can read"
MALFORMED_READING: Final = (
    "the text reading program answered with something we cannot use"
)

#: Every refusal above, so a test can prove they are five distinct sentences and
#: that none of them is empty.
ALL_REFUSALS: Final[tuple[str, ...]] = (
    ENGINE_MISSING,
    ENGINE_TIMED_OUT,
    ENGINE_NOT_ALLOWED,
    ENGINE_FAILED,
    UNREADABLE_MEDIA,
    MALFORMED_READING,
)


class EngineMissing(Exception):
    """The program is not installed, or not where we were told to look.

    `read_words` raises this when the wrapper reports the binary absent. A
    deployment that drives the engine some other way raises it too, and a
    plain `FileNotFoundError` from a hand-rolled runner is mapped to the same
    sentence below - a person with no engine installed should be told the same
    thing however the call was made.
    """


class EngineTimedOut(Exception):
    """The program was still going when the bounded wait ran out.

    A separate class from `EngineMissing` because the two lead a person to
    completely different next actions: install something, or try a smaller
    picture. One sentence for both would be a sentence neither can act on.
    """


class EngineFailed(Exception):
    """The program ran, and came back saying it could not do it.

    Distinct from a timeout although the wrapper expresses both as a
    `RuntimeError`: an engine that exited with an error code has a problem with
    the file, and an engine that ran out of time has a problem with the size.
    Telling the second person to try a smaller picture is useful; telling the
    first person that is a wild goose chase.
    """


#: Exception -> sentence. ORDER MATTERS and the two OSError siblings below are
#: why: a `FileNotFoundError` from a raw subprocess IS the missing binary, and a
#: `PermissionError` is a binary that exists and will not run. Collapsed into
#: one entry, a person with a permissions problem is told to install software
#: they already have.
_REFUSAL_FOR: Final[tuple[tuple[type[BaseException], str], ...]] = (
    (EngineMissing, ENGINE_MISSING),
    # `TesseractNotFoundError` subclasses `OSError` but NOT `FileNotFoundError`,
    # so without this line it would fall through to the catch-all and a person
    # with no engine installed would be told the engine could not read the file.
    (pytesseract.TesseractNotFoundError, ENGINE_MISSING),
    (EngineTimedOut, ENGINE_TIMED_OUT),
    (EngineFailed, ENGINE_FAILED),
    (TimeoutError, ENGINE_TIMED_OUT),
    (FileNotFoundError, ENGINE_MISSING),
    (PermissionError, ENGINE_NOT_ALLOWED),
)


def refusal_for(exc: BaseException) -> str:
    """Plain words for one failure. Never empty, never a stack message.

    `service.reason_for` is deliberately NOT reused. Its sentences all say "the
    reading service", which is a machine somewhere else that a person can only
    wait for. This engine is a program on their own computer that they can
    install, and telling them to wait for it would be telling them to wait for
    something that is never going to arrive.
    """
    for kind, refusal in _REFUSAL_FOR:
        if isinstance(exc, kind):
            return refusal
    return f"{ENGINE_FAILED} ({type(exc).__name__})"


@dataclass(frozen=True)
class Word:
    """One word the engine reported, and how sure it said it was.

    Two columns of `image_to_data` and nothing else. Not the position, not the
    size, not the block it sat in - this module decides nothing from where a
    word was on the page, and carrying the geometry would be the first line of
    something that did.
    """

    text: str
    confidence: int


@dataclass(frozen=True)
class Reading:
    """What the engine reported about one document, grouped by field.

    The grouping is the READER's, not ours. Which words on a page make up the
    total is field detection, it is the third-party's job by this package's
    oldest rule, and doing it here would be writing the reader the guard exists
    to keep out.

    `net` is the odd one and it earns its place: it never reaches the record and
    is never shown. It exists so that `net_plus_tax_equals_gross` has three
    numbers to compare instead of two, which is the difference between a real
    check and one that passes by construction. A bill that prints a net, a tax
    and a total is the ordinary Indian GST bill, so the figure is usually there.
    """

    date: tuple[Word, ...] = ()
    party: tuple[Word, ...] = ()
    total: tuple[Word, ...] = ()
    tax: tuple[Word, ...] = ()
    net: tuple[Word, ...] = ()


#: `(bytes, media type) -> Reading`. The media type is always one of
#: `READABLE_MEDIA`, never a caller's string. The answer is checked rather than
#: trusted: an annotation on a function somebody else wrote is a promise.
PageReader = Callable[[bytes, str], object]

#: `image_to_data` reports a hierarchy, and only one level of it is a word.
#: Levels 1 to 4 are the page, the block, the paragraph and the line, and every
#: one of them carries the marker in the confidence column. MEASURED: 19 of the
#: 43 rows on a clean synthetic invoice, and 21 of 52 on
#: `artifacts/ground_truth/documents/GT-0041.png`. Keeping only level 5 is what
#: stops the marker being the majority of what a caller treats as a score.
WORD_ROW: Final = 5

#: What this module adds to the engine's argument list: NOTHING.
#:
#: Read off `pytesseract.run_tesseract` rather than assumed: it builds a python
#: LIST and hands it to `subprocess.Popen` with no shell, and a `config` string
#: is `shlex.split` into further list elements. An empty config is the
#: strongest available form of "no caller input reaches the program" - there is
#: no string for anything to be interpolated into, so the claim needs no
#: escaping and no sanitising to be true.
#:
#: It also means no page segmentation mode is forced. Choosing one would be a
#: decision about how a page is read that nobody has made and nothing here has
#: measured, so the engine's own default stands.
ENGINE_ARGUMENTS: Final = ""


def _whatever_the_engine_returned(page: object, deadline_seconds: float) -> object:
    """`image_to_data`, typed as what we actually know about it: nothing.

    `pytesseract` ships no type stubs, so strict mode reads its answer as
    Unknown and is right to. Declaring `object` here is what makes
    `_words_from` the ONE place the answer's shape is asserted, instead of the
    shape being half-assumed at the call site and half-checked afterwards. It
    is the move `registry._whatever_it_returned` already makes, for the same
    reason: an annotation on somebody else's function is a promise, not a fact.

    The `timeout` suppression is not a shrug either. The wrapper annotates that
    parameter `int` and then hands it straight to
    `subprocess.Popen.communicate(timeout=...)`, which takes a float. MEASURED:
    a deadline of 0.000001 raises the wrapper's own timeout rather than being
    truncated to 0 - and 0 is what the wrapper reads as "no bound at all", so
    obeying the wrong annotation here would silently unbound the wait.
    """
    call = cast(
        "Callable[..., object]",
        pytesseract.image_to_data,  # pyright: ignore[reportUnknownMemberType]
    )
    return call(
        page,
        output_type=Output.DICT,
        config=ENGINE_ARGUMENTS,
        timeout=deadline_seconds,
    )


def read_words(data: bytes, *, deadline_seconds: float) -> tuple[Word, ...]:
    """Every word the engine found on this page, with its own confidence.

    THIS IS THE ENGINE CALL. `image_to_data` and not `image_to_string`, because
    the string form throws the confidence column away and the confidence is the
    entire reason this engine was chosen.

    `output_type=DICT` and not the default text, and it is not a style
    preference. MEASURED: the text form prints the confidence as `92.372406`
    and the DICT form floors it to the integer `92`. `field_confidence` refuses
    anything that is not an `int`, so a caller using the text form would meet a
    refusal it could not act on. Flooring is also the safe direction - rounding
    a confidence up is inventing certainty.

    `deadline_seconds` HAS NO DEFAULT, on purpose. A bounded wait is required
    and the bound is a number nobody has set: it belongs to the deployment,
    which knows its own page sizes and its own patience. Picking one here would
    be this module inventing a production setting.

    Raises rather than returning a refusal, because it is one step below the
    `Extractor` seam. `FreeReader.extract` is where an exception stops.
    """
    # `type(...) in` and not `isinstance`, so a `bool` is refused: `True` is an
    # int in Python and would become a one-second deadline that looks deliberate.
    if type(deadline_seconds) not in (int, float) or deadline_seconds <= 0:
        raise ValueError(
            f"a reading deadline must be a positive number of seconds, not "
            f"{deadline_seconds!r}. An unbounded wait is a request that hangs, "
            "and this system refuses rather than hangs."
        )
    page = Image.open(io.BytesIO(data))
    try:
        reported = _whatever_the_engine_returned(page, deadline_seconds)
    except pytesseract.TesseractNotFoundError as exc:
        raise EngineMissing(str(exc)) from exc
    except pytesseract.TesseractError as exc:
        # BEFORE the RuntimeError clause, and the order is the whole of it:
        # `TesseractError` subclasses `RuntimeError`, so reversed, an engine
        # that exited with an error code would be reported as a timeout and a
        # person would be told to try a smaller picture for a problem that has
        # nothing to do with size.
        raise EngineFailed(str(exc)) from exc
    except RuntimeError as exc:
        # The only bare `RuntimeError` the wrapper raises is from its own
        # `timeout_manager`, which has already killed the process. Read off its
        # source rather than matched on the message: matching on a third
        # party's wording is a test that passes until they fix a typo.
        raise EngineTimedOut(str(exc)) from exc

    return _words_from(reported)


def _words_from(reported: object) -> tuple[Word, ...]:
    """The word rows of an `image_to_data` answer, unchecked and uncoerced.

    `cast` and not `int(...)`. Coercing the confidence column here would turn
    the engine's float form into a plausible integer with nobody the wiser, and
    would hide exactly the difference the docstring above measures. `_complaint`
    is the one place that checks a word, and it refuses a float by name.
    """
    columns = cast("dict[str, list[object]]", reported)
    levels = columns["level"]
    texts = columns["text"]
    scores = columns["conf"]
    return tuple(
        Word(text=cast("str", texts[i]), confidence=cast("int", scores[i]))
        for i in range(len(levels))
        if levels[i] == WORD_ROW
    )


def _every_group(reading: Reading) -> tuple[tuple[str, object], ...]:
    """Every word group in the reading, named, typed as what we actually know.

    `object` and not `tuple[Word, ...]`, for the reason `registry.
    _whatever_it_returned` gives: pyright narrows to the annotation and then
    calls the run-time check provably dead code, and strict mode is right to
    reject dead code. The check is not dead - this object was built by a
    function written outside this repository, and an annotation is a promise
    rather than a fact.

    `net` is included although it is never carried, because a wrong type in it
    would reach the conservation law.
    """
    return (
        ("date", reading.date),
        ("party", reading.party),
        ("total", reading.total),
        ("tax", reading.tax),
        ("net", reading.net),
    )


def _complaint(reading: Reading) -> str:
    """The sentence saying why this reading cannot be used, or "" when it can.

    Types are checked at run time although they are annotated, for the reason
    `wall.py` gives about `amount_paise` and `vouchers.py` gives about the same
    hazard: annotations are not enforced, and this object arrives from a
    function written outside this repository.
    """
    for name, words in _every_group(reading):
        if not isinstance(words, tuple):
            return (
                f"{MALFORMED_READING}: the {name} words arrived as "
                f"{type(words).__name__} instead of a tuple"
            )
        # `isinstance` against a bare `tuple` leaves the parameters Unknown and
        # strict mode is right to object. The cast states what a tuple of
        # anything is; the `Word` check below is what narrows it.
        for word in cast("tuple[object, ...]", words):
            if not isinstance(word, Word):
                return (
                    f"{MALFORMED_READING}: a {name} word arrived as "
                    f"{type(word).__name__}"
                )
            # `type(...) is not int` rather than isinstance, and the reason is
            # the one this repository writes down everywhere money is checked:
            # `isinstance(True, int)` is True, so a flag passed where a
            # confidence belonged would read as a confidence of 1 out of 100 -
            # a plausible-looking terrible score rather than an obvious error.
            if type(word.confidence) is not int:
                return (
                    f"{MALFORMED_READING}: a {name} word carries a confidence "
                    f"of type {type(word.confidence).__name__}, and a "
                    "confidence is a whole number from 0 to 100"
                )
            # `type(...) is not str` and not `isinstance`, same reason as the
            # confidence check above and the one `wall.py` writes down: pyright
            # calls the isinstance form redundant against the annotation, and
            # the annotation is exactly what is not being trusted here.
            if type(word.text) is not str:
                return (
                    f"{MALFORMED_READING}: a {name} word carries text of type "
                    f"{type(word.text).__name__}"
                )
            if word.confidence == NO_SCORE_MARKER:
                continue
            if not 0 <= word.confidence <= 100:
                return (
                    f"{MALFORMED_READING}: a {name} word carries a confidence "
                    f"of {word.confidence}, which is on no scale we know. "
                    f"{NO_SCORE_MARKER} is the marker for a row with no score; "
                    "anything else outside 0 to 100 would have to be guessed at"
                )
            if not word.text.strip():
                return (
                    f"{MALFORMED_READING}: a {name} word has no text and yet "
                    f"carries a confidence of {word.confidence}. A confident "
                    "blank is a contradiction, not a reading"
                )
    return ""


def _scores(words: tuple[Word, ...]) -> tuple[int, ...] | None:
    """The word confidences, or None when these words carry no usable score.

    None in two cases that are one fact: there are no words, or one of them
    carries the marker. A marker is not dropped and the rest averaged, because
    dropping it would silently discard a word the engine actually reported and
    then report a confidence about the words that are left as though it were
    about the field.
    """
    if not words:
        return None
    if any(word.confidence == NO_SCORE_MARKER for word in words):
        return None
    return tuple(word.confidence for word in words)


def _joined(words: tuple[Word, ...]) -> str:
    """The field's text: the engine's words, in order, single-spaced.

    Joined rather than carried as a second string, so the text and the scores
    cannot come apart. A separate `text` field on `Reading` would be one more
    thing that can disagree with the words it is supposed to describe.
    """
    return " ".join(word.text for word in words)


def _money(words: tuple[Word, ...]) -> tuple[int | None, str]:
    """Whole paise, or the sentence saying why what was read is not that.

    `adapter._to_paise` and not a second conversion. Two places that turn
    rupees into paise is how one of them rounds: that module carries the
    measured case where `round(float(...) * 100)` put "10.005" into the books
    as ten rupees exactly. `confidence.looks_like_paise` is the wrong check
    here - it refuses a decimal point, and every printed bill has one, because
    what is printed is rupees.
    """
    text = _joined(words).strip()
    paise = _to_paise(text)
    if paise is None:
        return None, (
            f"{text!r} is not an amount that can be held exactly in whole paise"
        )
    if paise < 0:
        return None, (
            f"{text!r} is a negative amount. A minus sign on a bill is a misread "
            "character or a credit note, and this system does corrections by "
            "reversal and never by sign"
        )
    return paise, ""


def _read_date(words: tuple[Word, ...]) -> tuple[datetime.date | None, str]:
    """A real calendar date, or the sentence saying why this is not one.

    `confidence.looks_like_a_date` is the check, unchanged: the 34th of a month
    is a misread 04th, and an impossible date is one of the few forgery signals
    this product actually claims.
    """
    text = _joined(words).strip()
    if not looks_like_a_date(text):
        return None, (
            f"{text!r} is not a real date written the way this system reads "
            "dates, which is year-month-day"
        )
    return datetime.date.fromisoformat(text), ""


def _read_party(words: tuple[Word, ...]) -> tuple[str | None, str]:
    """A supplier name, or the sentence saying why there is not one.

    A blank becomes None and then an explicit not_found. A party field holding
    "   " is a silent blank, which is the single thing `ExtractedRecord` exists
    to make impossible.
    """
    text = _joined(words).strip()
    if not text:
        return None, "no name was read here"
    return text, ""


def _judge(
    words: tuple[Word, ...],
    *,
    read: bool,
    problem: str,
    agrees: bool,
    disagreement: str,
    backend: str,
) -> tuple[float, str]:
    """One field's confidence, and the sentence saying where it came from.

    `field_confidence` is called with the real booleans rather than with True.
    Passing `format_valid=True` because we already checked the format would
    make the multiplier decorative, and the next person to change the check
    would not find out that the score stopped depending on it.
    """
    scores = _scores(words)
    if scores is None:
        return 0.0, (
            f"{NOT_FOUND}: {backend} reported no word here that carries a "
            f"confidence. {NO_SCORE_MARKER} is its marker for a row with no "
            "score, and a marker is not a low score"
        )
    confidence = field_confidence(scores, format_valid=read, consistent=agrees)
    if confidence > 0.0:
        return confidence, backend
    if not read:
        return 0.0, f"{NOT_FOUND}: {problem}"
    if not agrees:
        return 0.0, f"{NOT_FOUND}: {disagreement}"
    return 0.0, (
        f"{NOT_FOUND}: {backend} read something here and then said it was not "
        "sure of a single character of it"
    )


@dataclass(frozen=True)
class _Answer:
    """The four named fields, their scores and their sources - or a refusal.

    One shape for both outcomes, so `extract` and `observe` cannot end up
    saying different things about the same document. `reason` being non-empty
    is the refusal, and in that case every value is None and every score is 0.0
    by construction rather than by the caller remembering.
    """

    date: datetime.date | None
    party: str | None
    total_paise: int | None
    tax_paise: int | None
    confidences: dict[str, float]
    sources: dict[str, str]
    reason: str

    @classmethod
    def refused(cls, reason: str) -> _Answer:
        return cls(
            date=None,
            party=None,
            total_paise=None,
            tax_paise=None,
            confidences=dict.fromkeys(ExtractedRecord.FIELDS, 0.0),
            sources=dict.fromkeys(ExtractedRecord.FIELDS, f"{NOT_FOUND}: {reason}"),
            reason=reason,
        )


def _scored(reading: Reading, backend: str) -> _Answer:
    """Every field judged, with the amounts checked against one another first."""
    total, total_problem = _money(reading.total)
    tax, tax_problem = _money(reading.tax)
    net, _ = _money(reading.net)

    # The law, not a comparison written out here again. A FAIL is a real
    # contradiction between three numbers on the page; an INDETERMINATE means
    # the bill did not print a net and so nothing has been contradicted. The
    # second is not a reason to zero a field - `conservation.py` says calling
    # it a failure "makes the product useless" - and blocking on it belongs to
    # the decision layer, which already treats it as blocking.
    law = net_plus_tax_equals_gross(net, tax, total)
    agrees = law.verdict is not Verdict.FAIL

    date_value, date_problem = _read_date(reading.date)
    party_value, party_problem = _read_party(reading.party)

    # Date and party are outside the arithmetic, so a contradiction between the
    # amounts leaves them alone. A smudged letterhead does not become more or
    # less legible because the tax line does not add up.
    date_score, date_source = _judge(
        reading.date,
        read=date_value is not None,
        problem=date_problem,
        agrees=True,
        disagreement="",
        backend=backend,
    )
    party_score, party_source = _judge(
        reading.party,
        read=party_value is not None,
        problem=party_problem,
        agrees=True,
        disagreement="",
        backend=backend,
    )
    total_score, total_source = _judge(
        reading.total,
        read=total is not None,
        problem=total_problem,
        agrees=agrees,
        disagreement=law.said,
        backend=backend,
    )
    tax_score, tax_source = _judge(
        reading.tax,
        read=tax is not None,
        problem=tax_problem,
        agrees=agrees,
        disagreement=law.said,
        backend=backend,
    )

    # ONE rule, applied here and nowhere else: a value survives only where a
    # confidence above zero was stated for it. It is what makes the record and
    # the observation agree by construction, and it is what stops a number this
    # system does not trust appearing on a screen next to an invisible 0.0 for
    # a person to read off and type into Tally by hand.
    return _Answer(
        date=date_value if date_score > 0.0 else None,
        party=party_value if party_score > 0.0 else None,
        total_paise=total if total_score > 0.0 else None,
        tax_paise=tax if tax_score > 0.0 else None,
        confidences={
            "date": date_score,
            "party": party_score,
            "total_paise": total_score,
            "tax_paise": tax_score,
        },
        sources={
            "date": date_source,
            "party": party_source,
            "total_paise": total_source,
            "tax_paise": tax_source,
        },
        reason="",
    )


class FreeReader:
    """The free engine's answer, turned into a record and an observation.

    `extract` satisfies the `Extractor` Protocol unchanged, so this plugs into
    the same seam every other backend uses and nothing outside the package
    changes. `observe` is the addition: it carries the per-field confidences,
    which `ExtractedRecord` has nowhere to put and the decision bands need.
    """

    name = "free_ocr"

    def __init__(self, read_page: PageReader, *, name: str = "free_ocr") -> None:
        self._read_page = read_page
        self.name = name

    def extract(self, data: bytes, mime: str) -> ExtractedRecord:
        answer = self._reading(data, mime)
        if answer.reason:
            return self.outage(answer.reason)
        return ExtractedRecord(
            date=answer.date,
            party=answer.party,
            total_paise=answer.total_paise,
            tax_paise=answer.tax_paise,
            # Empty, deliberately, and this is `placeholder.py`'s argument
            # rather than a new one: `pipeline.build_draft` copies `raw_text`
            # into `Voucher.narration`, which reaches the page, the durable
            # action log and - on a VALID entry - Tally itself. A backend that
            # carried what it read would put somebody's whole scanned bill in
            # all three.
            raw_text="",
            backend=self.name,
            per_field_source=dict(answer.sources),
        )

    def observe(self, data: bytes, mime: str) -> Observation:
        """The same reading, with the confidences the decision bands are about.

        No branch on failure. `_Answer.refused` already sets every value None
        and every score 0.0, which is exactly what `wall.Field` demands of a
        field with no value, so the refusal path and the read path build the
        same way.
        """
        answer = self._reading(data, mime)
        return Observation(
            date=Field(answer.date, answer.confidences["date"], answer.sources["date"]),
            party=Field(
                answer.party, answer.confidences["party"], answer.sources["party"]
            ),
            total_paise=Field(
                answer.total_paise,
                answer.confidences["total_paise"],
                answer.sources["total_paise"],
            ),
            tax_paise=Field(
                answer.tax_paise,
                answer.confidences["tax_paise"],
                answer.sources["tax_paise"],
            ),
        )

    def outage(self, reason: str) -> ExtractedRecord:
        """The all-not_found record, built by the one class that builds it.

        `UnavailableExtractor`, not a second shape that resembles it - the
        argument `registry.GuardedExtractor.outage` and
        `adapter.TypedTextExtractor._refuse` already make. Two places that build
        this record is how one of them ends up without a reason on it, which is
        a silent blank wearing a label.
        """
        return UnavailableExtractor(reason, name=self.name).extract(b"", "")

    def _reading(self, data: bytes, mime: str) -> _Answer:
        declared = _media_type(mime)
        if declared not in READABLE_MEDIA:
            return _Answer.refused(
                f"{UNREADABLE_MEDIA}: it says it is "
                f"{declared or 'nothing in particular'}, and we read "
                + ", ".join(READABLE_MEDIA)
            )
        try:
            # `declared` and not `mime`. It has just been matched against
            # `READABLE_MEDIA`, so what crosses this line is one of five
            # constants written above - never a string a caller chose. Whatever
            # the reader does with it, no input of theirs is in it.
            answer = self._read_page(data, declared)
        except Exception as exc:
            # `Exception` and not `BaseException`, for the reason
            # `service.ServiceExtractor` gives: a KeyboardInterrupt or a
            # SystemExit is somebody stopping the process, and answering that
            # with a tidy record would fight them.
            return _Answer.refused(refusal_for(exc))
        if not isinstance(answer, Reading):
            return _Answer.refused(
                f"{MALFORMED_READING}: it answered with a "
                f"{type(answer).__name__} instead of a reading"
            )
        complaint = _complaint(answer)
        if complaint:
            return _Answer.refused(complaint)
        return _scored(answer, self.name)
