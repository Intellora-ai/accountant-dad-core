"""Which words on a photographed page are the total, the tax, the date, the party.

WHY THIS FILE EXISTS
--------------------
`freeocr.py` has two seams and until 2026-08-13 only one of them was filled.
`read_words` is the engine and it is real: bytes in, every word on the page with
the engine's own confidence for each. `PageReader` is the other one, and its
whole job is the sentence in this module's title. Nothing in this repository
answered it, `registry._NEEDS_WIRING` said so by name, and so an uploaded
photograph met a refusal rather than a reading.

This is that function. It is not a second reader and it holds no vocabulary of
its own.

THE WHOLE DESIGN, IN ONE LINE
------------------------------
`textlayer.py` already turns labelled invoice text into fields and does it well
- MEASURED at 92/100 exact on the twenty corpus PDFs. The engine already turns a
picture into text. So the missing piece is not a parser. It is a JOIN:

    engine -> the words on each line -> the SAME field logic the PDF rung uses

The field logic moved to `labels.py` when this landed, so both callers run the
one copy. A second parser here would have meant two label vocabularies, and the
day one of them learns `AMOUNT PAYABLE` and the other does not is the day the
same bill reads differently depending on whether it arrived as a PDF or as a
photograph.

WHAT THE JOIN COSTS, AND IT IS PAID IN CONFIDENCE, NOT IN VALUES
------------------------------------------------------------------
A field read off a text layer is EXACT and `confidence.EXACT` is 1.0, because
the characters are in the file and the alternative readings of them number
zero. A field read off pixels is an ESTIMATE, and it must never inherit that
1.0. THE WHOLE CAGE KEYS OFF THAT NUMBER: `cage/decision.py` auto-posts at 0.95
and above, so a reading that claimed exactness would post a guess to somebody's
books with nothing on screen to notice.

Nothing here computes a confidence, and that is how the difference survives.
This function reports WORDS, each carrying the engine's own 0-100 score for it,
and `freeocr._judge` runs them through `confidence.field_confidence` - the
minimum word, times format validity, times the conservation law. A field is
exactly as trustworthy as its least legible character, and it says so.

WHAT IT DOES NOT DO, EACH FOR A STATED REASON
----------------------------------------------
    it does not touch the      no resizing, no thresholding, no cleaning. The
    picture                    bytes go to the engine as they arrived.
                               MEASURED, because it was tried: scaling the
                               corpus PNGs up reads GT-0041's supplier
                               perfectly at 2x, mangles it at 3x and mangles it
                               differently at 4x. A factor that has to be
                               picked by which one flatters the corpus is a
                               number fitted to the corpus, and interpolation
                               invents ink that was never on the page.

    it does not read a         `labels.py` locates the date; `freeocr._read_date`
    date, an amount or a       decides whether those characters are one. This
    name                       file never rewrites a word: normalising
                               `13-05-2026` to ISO so the engine's caller would
                               accept it would be putting characters into
                               evidence that the page does not carry.

    it does not say WHY a      `Reading` has no slot for a reason, so a field
    field is missing           this declines to point at comes back from
                               `freeocr` as "reported no word here that carries
                               a confidence". That sentence is right when the
                               page really had nothing and WRONG when the bill
                               printed its total twice with two different
                               figures - which is refused here, correctly, and
                               then explained badly. Named rather than hidden:
                               the fix is a reason on `Reading`, and it is a
                               change to that file's shape, not to this one's.

    it does not add up a       a bill printing CGST and SGST states its tax as
    split tax                  two figures in two places. There is no set of
                               words on that page that IS the tax, so this
                               points at none and the field is unread. The PDF
                               rung can add them because it answers with a
                               number; this one answers with words.

WHY LINES ARE REBUILT WITH ONE SPACE BETWEEN WORDS
----------------------------------------------------
Because that is all the engine reports here. `freeocr.Word` deliberately
carries no geometry, so a column gap and a word gap are the same thing by the
time they reach this file. `labels.py` treats a run of two or more spaces as a
column boundary, so on a rebuilt line a SECOND label sharing a line with the
first is not cut off, and its value bleeds into the first one's.

MEASURED on the twenty corpus PNGs: it never happened, because the corpus
prints one field per line. It is a real limitation on a bill that does not, and
the honest fix is geometry, which is a change to what a `Word` is.

WHAT THIS FILE DOES NOT PROVE
------------------------------
That anything it points at was read CORRECTLY. MEASURED on the twenty corpus
PNGs, which are rendered in a 5x7 bitmap font: five of them yield a supplier
name and two of those are exactly right. The other three are wrong - `IVER.
ELECTRICALS` for `IYER ELECTRICALS`. They are wrong at the confidence the
engine stated, which is the entire point of stating one.

That an amount it points at survives. Usually it does not, and that is the cage
working: on GT-0041 scaled up, the engine read a total of 1,626.70 against a
subtotal of 865.08 and a tax of 155.76, and `net_plus_tax_equals_gross` refused
both amounts outright. A misread digit that breaks arithmetic is caught by
arithmetic, not by confidence.

NO NETWORK, NO CLOCK, NO FILESYSTEM. Bytes in, words grouped by field out.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from accountant.extract.freeocr import PageReader, Reading, Word, read_lines
from accountant.extract.labels import (
    DATE_LABEL,
    NET_LABELS,
    PARTY_LABELS,
    TAX_WHOLE,
    TOTAL_LABELS,
    Amount,
    Found,
    amounts_for,
    the_one,
    values_for,
)

#: How long this application gives the engine to read ONE page.
#:
#: A BOUND, NOT A TARGET, and the difference is the whole of the number.
#: `read_words` and `read_lines` take it with no default on purpose - an
#: unbounded wait is a request that hangs - so something has to name one, and
#: the constant is here rather than in `registry.py` because this is the only
#: module in the repository that has a use for it and both callers already
#: import this file. Every call site passes it explicitly, so no reader ever
#: inherits it silently.
#:
#: 30 SECONDS, and it is not invented here: it is the number this repository
#: already uses everywhere it waits on something outside the process -
#: `ingest/fetch.TIMEOUT_SECONDS`, `tallyio.TallyConfig.timeout_seconds` and
#: `agent/connector`. MEASURED for scale rather than to fit: the slowest of the
#: twenty corpus PNGs reads in 0.151 seconds on the machine this was written on,
#: so the bound is roughly two hundred times a real page and will only ever be
#: met by an engine that has stopped making progress.
READING_DEADLINE_SECONDS: Final = 30.0

#: What a line's words are joined with when the line is rebuilt as a string.
#: ONE space, because the engine reports no gap widths - see the module
#: docstring for what that costs and why the alternative is geometry.
BETWEEN_WORDS: Final = " "


@dataclass(frozen=True)
class _Page:
    """One page as strings, with every string's words still attached to it.

    The two tuples are index-aligned with each other and `lines[i]` is exactly
    what `words[i]` joins to, which is what makes a character position on a line
    answerable in words. Built once per document by `_page_of` and never edited,
    for the reason every other reading in this package is frozen: a page that
    can be changed after the fact is not evidence about what was on it.
    """

    lines: tuple[str, ...]
    words: tuple[tuple[Word, ...], ...]
    spans: tuple[tuple[tuple[int, int], ...], ...]


def _line_of(
    words: tuple[Word, ...],
) -> tuple[str, tuple[Word, ...], tuple[tuple[int, int], ...]]:
    """One engine line as a string, and where each of its words sits in it.

    A ROW WITH NO CHARACTERS IS DROPPED, and this is the one place that decision
    is made. `read_lines` hands them over faithfully because they are what the
    engine said; a row carrying no characters contributes none to the line, so
    keeping it would either do nothing or - because it is joined with a space on
    each side - manufacture a two-space run, which `labels.py` reads as a column
    boundary. A gap invented by our own join is not evidence about the page.

    It is NOT a second handling of the marker. `freeocr` decides what a
    confidence of -1 means and still does: a word WITH characters is kept here
    whatever its confidence, and if it carries the marker then `freeocr._scores`
    refuses to score the field it lands in. What is dropped here is rows with no
    text, which is a different fact from a row with no score.
    """
    kept = tuple(word for word in words if word.text.strip())
    spans: list[tuple[int, int]] = []
    at = 0
    for word in kept:
        spans.append((at, at + len(word.text)))
        at += len(word.text) + len(BETWEEN_WORDS)
    return BETWEEN_WORDS.join(word.text for word in kept), kept, tuple(spans)


def _page_of(lines: tuple[tuple[Word, ...], ...]) -> _Page:
    """Every engine line, as a string and as the words that make it."""
    built = [_line_of(words) for words in lines]
    return _Page(
        lines=tuple(text for text, _, _ in built),
        words=tuple(words for _, words, _ in built),
        spans=tuple(spans for _, _, spans in built),
    )


def _words_at(page: _Page, line: int, start: int, end: int) -> tuple[Word, ...]:
    """The words of `line` that the characters `[start, end)` are part of.

    OVERLAP AND NOT CONTAINMENT. `labels.py` reports the range the VALUE
    occupied, and a value can begin part way through a word the engine reported
    - `TOTAL:1,020.70` comes back as one word when the printing is tight. A
    containment test would answer with nothing there and lose a field the page
    plainly states; an overlap test answers with the word that carries it, and
    `freeocr._money` then refuses the whole thing if the extra characters mean
    it is not an amount. Refusing a real amount is the failure to avoid here.
    """
    return tuple(
        word
        for word, (at, to) in zip(page.words[line], page.spans[line], strict=True)
        if at < end and start < to
    )


def _words_for(page: _Page, found: tuple[Found, ...]) -> tuple[Word, ...]:
    """The words behind the one value every printing of it agreed on.

    Nothing when the printings disagree, and nothing when the label is absent.
    Both are `the_one` refusing, and both mean the same thing here: there is no
    set of words on this page that this file is willing to call the field.
    """
    agreed, _ = the_one(tuple(one.printed for one in found), "")
    if agreed is None:
        return ()
    first = next(one for one in found if one.printed == agreed)
    return _words_at(page, first.line, first.start, first.end)


def _words_for_amount(page: _Page, found: tuple[Amount, ...]) -> tuple[Word, ...]:
    """The same, for a labelled amount. Same refusals, for the same reasons."""
    agreed, _ = the_one(tuple(one.paise for one in found), "")
    if agreed is None:
        return ()
    first = next(one for one in found if one.paise == agreed)
    return _words_at(page, first.line, first.start, first.end)


def read_page(lines: tuple[tuple[Word, ...], ...]) -> Reading:
    """The four fields and the net, as the words each is printed in.

    TAKES THE ENGINE'S ANSWER AND NOT AN IMAGE, so every claim about this join
    can be tested without a picture and without the engine installed: hand it
    the words and assert which of them came back as the total. The bytes are one
    layer out, in `page_reader` below, which is the only thing here that reads a
    file at all.

    `net` never reaches a record and is never shown. It is here so that
    `conservation.net_plus_tax_equals_gross` has three numbers to compare
    instead of two, which is the difference between a real check and one that
    passes by construction - and on a misread page it is the check that fires.
    """
    page = _page_of(lines)
    return Reading(
        date=_words_for(page, values_for(page.lines, (DATE_LABEL,))),
        party=_words_for(page, values_for(page.lines, PARTY_LABELS)),
        total=_words_for_amount(page, amounts_for(page.lines, TOTAL_LABELS)),
        tax=_words_for_amount(page, amounts_for(page.lines, TAX_WHOLE)),
        net=_words_for_amount(page, amounts_for(page.lines, NET_LABELS)),
    )


def page_reader(*, deadline_seconds: float) -> PageReader:
    """A `freeocr.PageReader` that runs the engine and then locates the fields.

    `deadline_seconds` HAS NO DEFAULT, for the reason `read_words` gives about
    the same argument: an unbounded wait is a request that hangs, the bound
    belongs to the deployment, and a module that picked one here would be
    inventing a production setting. `registry.READING_DEADLINE_SECONDS` is where
    this application's number is written down and argued for.

    The media type is not used and is not ignored either: `FreeReader._reading`
    has already matched it against `READABLE_MEDIA` and hands across one of five
    constants, and the engine identifies the format from the bytes themselves.
    Passing it on to the engine would be handing a parser a second opinion about
    what a file is, which is how a JPEG gets parsed as a PNG.
    """

    def read(data: bytes, _media: str) -> Reading:
        return read_page(read_lines(data, deadline_seconds=deadline_seconds))

    return read
