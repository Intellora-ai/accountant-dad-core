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
That anything it points at was read CORRECTLY. It mostly is not. MEASURED on
the twenty corpus PNGs, which are rendered in a 5x7 bitmap font, through the
wired product path, AFTER the separator tolerance landed on 2026-08-13:

    8 of 80 fields come back with a value, and all eight are the supplier
    3 of those 8 are exactly right, at 0.48, 0.61 and 0.74
    5 are wrong, at 0.48, 0.30, 0.16, 0.10 and 0.08
    72 are refused

It was 4 with a value - 2 right, 2 wrong - before the tolerance. THE WRONG
COUNT WENT UP AND THAT IS THE POINT: `AQUANCED PROPULSION CENTRE UK LTO` at
0.30 is a misreading the cage can block or ask about, and the same misreading
unread is a page the engine had already half-read and nobody could see. Nothing
here reads a letter better than it did.

A ninth page matches the `SUPPLIER` label and still comes back with nothing: on
GT-0051 the engine reported `COMMISSION` at confidence 0, and a field is carried
only where a confidence above zero can be stated for it. That is `freeocr`'s
rule working, not a reading being lost.

GT-0058 LOST a wrong answer to the tolerance, which is the safe direction and
worth writing down. It prints its supplier twice; the engine read the two
printings as `IVER. ELECTRICALS` and `IVER ELECTRICALS`. Exact matching saw
only the one with a surviving colon and answered it. Both are visible now, they
DISAGREE, and `the_one` refuses - so a value that used to reach a record at
0.37 is a question instead. It is also the case this file's "it does not say
WHY a field is missing" limitation names: the sentence a person gets says the
engine scored no word here, and the truth is that the page said two things.

That an amount it points at survives. Usually it does not, and that is the cage
working: on GT-0041 scaled up, the engine read a total of 1,626.70 against a
subtotal of 865.08 and a tax of 155.76, and `net_plus_tax_equals_gross` refused
both amounts outright. A misread digit that breaks arithmetic is caught by
arithmetic, not by confidence.

THE POSITIONAL FALLBACK, MEASURED THE DAY IT LANDED
----------------------------------------------------
OWNER DECISION 2026-08-15: where no label matched, guess from WHERE A THING SITS
ON THE PAGE. Three fallbacks were asked for. ONE SHIPPED.

MEASURED over 422 real documents in `data/real_invoices` and
`data/real_invoices_indian`, through `registry.default_extractor()` - the call
`accountant/web/app.py` makes - with `invoicelike.looks_like_a_bill` deciding
which of them is a bill:

    blank and looks like a bill    74      the documents this change is for
    read at least one field, of                 those 74     0  ->  31

31 of 74, against an expectation of 50 to 60. Every one of the 31 is the PARTY.
Exactly one document also gained a date, and no document gained an amount.

THE POSITIONAL TOTAL WAS BUILT, MEASURED AND REVERTED. `run_ground_truth.py`,
twenty corpus PNGs: `total_paise` went from 0 wrong to 15 WRONG, with 0 right.
GT-0046 answered ₹19,15,081 on a three-figure bill. The rule - largest amount in
the last ten lines - finds the running balance, the account number or a misread
column, because those are what sit at the foot of a real page. `read_page`
carries the numbers at the line where it used to be called.

THE DATE FALLBACK SHIPPED AND IS VERY NEARLY INERT, and that is honest rather
than disappointing. `freeocr._read_date` accepts ISO and nothing else, so of the
three shapes only the first can become a value; the other two are found, refused,
and produce a sentence naming the characters instead of a silence. One document
of 74 gained a date.

WHAT THE PARTY GUESS ACTUALLY PICKS, AND IT IS MOSTLY NOT THE SUPPLIER. Twenty
of the 31, verbatim, with the confidence each reached end to end:

    plausibly the supplier - 6 of 20
        0.50 'JNO. M. GRAHAM.'          0.21 'HOTEL ¥VISHWANAND'
        0.50 'Gobierno Auténomo'        0.03 '"NORTH BENGAL STATE TRANSPORT
        0.01 'PAR*GsiEMINS DE FRR.'          CORPORATION -'
        0.50 'af Alliance frangaise de Pondichéry INVOICE'

    a heading the length rule missed - 2
        0.50 'PERFORMA INVOICE'         0.50 'PASSENGER TICKET'

    a street address - 2
        0.50 '1935 E Katella Ave'       0.11 'i294 University', led by a stray
                                             quote mark the engine invented

    a line item or a label - 3
        0.50 'Additional usage charges' 0.16 'Total: Rs'
        0.50 'Arem Arem'

    engine noise - 7
        0.50 'Qnme'   0.39 'x.'   0.50 'Ny.'   0.11 'a ne'
        0.20 'ag ans' 0.50 'ad'

SO THE HONEST SUMMARY IS: this turns 31 silences into 31 questions, and about
six of those questions have the right name in them. It is reported and not
argued up. What makes it safe to ship anyway is that not one of the 31 can reach
anybody's books: 0.5 is below `ASK_FLOOR`, and `free_ocr` is in neither
`adapter.ENTITLED_TO_EXACT` nor `decision.AUTO_POST_ALLOWED_TIERS`, so the owner
rule "party unknown -> ALWAYS ASK, never auto-create a ledger" holds by two
independent walls rather than by this file guessing well.

THE CEILING IS A CEILING AND THE MEASUREMENT PROVES IT. Only 15 of the 31 land
at 0.5. The other 16 land LOWER - down to 0.01 - because `freeocr._judge` takes
the minimum of this file's ceiling and the engine's own worst word, and on a
smudged letterhead the engine is the more pessimistic of the two.

NO NETWORK, NO CLOCK, NO FILESYSTEM. Bytes in, words grouped by field out.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

from accountant.extract import artifacts, nearby
from accountant.extract.freeocr import PageReader, Reading, Word, read_lines
from accountant.extract.labels import (
    DATE_LABEL,
    NET_LABELS,
    PARTY_LABELS,
    TAX_PARTS,
    TAX_WHOLE,
    TOTAL_LABELS,
    Amount,
    Found,
    Printing,
    amounts_for,
    the_one,
    values_for,
)

#: THIS TIER READS PIXELS, AND EVERY CHARACTER ON IT IS AN ESTIMATE - INCLUDING
#: THE PUNCTUATION. `labels.Printing` unlocks exactly one tolerance for saying
#: so: a mark standing where the colon should be. It does NOT loosen the label
#: word, and it does not loosen the value. MEASURED on the twenty corpus PNGs,
#: where the truth prints `SUPPLIER:` the engine produced `:` 5 times, `S` 8,
#: `!` 2, `®` 2, `?` 1 and `'` 1; this recovers the six that are marks and
#: leaves the eight `S` unread, because a plural and a mangled colon are the
#: same character and guessing between them reads a heading as a supplier.
_PRINTING: Final = Printing.READ_OFF_A_PHOTOGRAPH

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

# =============================================================================
# THE POSITIONAL FALLBACK, AND WHY IT IS THE MOST DANGEROUS CODE IN THIS FILE
# =============================================================================
# Everything above this line finds a field because a LABEL pointed at it.
# `labels.py`'s own docstring draws the line these four functions cross:
# "widening the list is safe; guessing at an unlabelled number is not, and that
# guess is exactly what `adapter.TYPED_TEXT_MIME` records as having invented
# twenty totals."
#
# OWNER DECISION 2026-08-15. 74 of 422 real documents look like bills by
# `invoicelike.looks_like_a_bill` and read no field at all, because they print
# their values with no label in front of them. The owner's instruction is to
# guess from POSITION when and only when no label matched.
#
# THE GUESS IS MARKED, AND THAT IS THE WHOLE OF WHAT MAKES IT SAFE. A field
# found here comes back with a ceiling of `BY_POSITION` on `Reading.at_most`,
# and `freeocr._judge` takes the minimum of that and the engine's own score.
# Without the ceiling a guessed total would arrive carrying 0.80 to 0.96 - the
# engine's confidence that those digits are those digits, which is a statement
# about the CHARACTERS and says nothing whatever about whether that number is
# the total. That is failure mode F-02 wearing a high score.


#: THE CEILING ON ANY FIELD FOUND BY POSITION. The owner's number, 2026-08-15.
#:
#: Read against the two bands in `cage/decision.py` rather than chosen to look
#: cautious: `AUTO_POST_FLOOR` is 0.95 and `ASK_FLOOR` is 0.70, so 0.5 is below
#: BOTH. A positional find therefore cannot post, and cannot even spend one of
#: the five questions - it BLOCKS, and a person is told the reader had nothing
#: it was willing to stand behind. That is the correct direction for a guess:
#: the failure it prevents is F-03, one vendor's balance wrong for ever, and the
#: failure it causes is a document a person has to open.
#:
#: NOT MEASURED AND NOT PRESENTED AS IF IT WERE. Nothing here has measured how
#: often a positional find is right, because that needs labels this corpus does
#: not have. What IS measured is the direction: at 0.5 nothing it produces can
#: reach anybody's books unattended, whatever it picked.
BY_POSITION: Final = 0.5

#: How far into the page a date may be guessed at. The owner's number.
#: A bill prints its date in the head matter; the tenth line is already well
#: into the item table on the corpus documents, where a number in a date-shaped
#: column is a delivery date or a due date and not the bill's date.
FIRST_LINES_FOR_A_DATE: Final = 10

#: How far into the page a supplier name may be guessed at. The owner's number,
#: and TIGHTER than the date's for a reason: the letterhead is the first thing
#: printed, and every line past it is more likely to be the CUSTOMER's name and
#: address than the supplier's. On a purchase bill that mistake posts a vendor
#: ledger under somebody else.
FIRST_LINES_FOR_A_PARTY: Final = 5

#: THREE SHAPES A DATE IS PRINTED IN, and no fourth. Each is anchored on word
#: boundaries so a run of digits inside an invoice number is not a date.
#:
#: ONLY THE FIRST OF THESE CAN EVER PRODUCE A VALUE, and saying so here is
#: cheaper than letting somebody discover it. `freeocr._read_date` requires
#: `confidence.looks_like_a_date`, which is `date.fromisoformat` - ISO and
#: nothing else. So `13/05/2026` and `15 Aug 2026` are FOUND by this file,
#: judged by that one, refused, and the field comes back unread with a sentence
#: naming what it saw. They are kept because a refusal that names the characters
#: it refused is worth more than a silence, and because normalising them here
#: would be this file writing characters into evidence that the page does not
#: carry - which its own docstring forbids.
DATE_SHAPES: Final = (
    re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),
    re.compile(r"\b\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}\b"),
    re.compile(
        r"\b\d{1,2}[\s.-]*"
        r"(?:JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)[A-Z]*"
        r"[\s.,-]*\d{2,4}\b",
        re.IGNORECASE,
    ),
)

#: A line more than this much digits, by character, is not a supplier's name.
#: Half, because a name with a house number in it - `12 NEHRU ROAD` - is still a
#: name, and a line that is mostly figures is a table row, a phone number or a
#: GSTIN.
MOSTLY_DIGITS: Final = 0.5

#: A line no longer than this, printed with no lower-case letter in it, is read
#: as a HEADING and skipped.
#:
#: THE NUMBER IS THE WEAKEST THING ON THIS PAGE AND NO NUMBER WOULD BE STRONG.
#: Indian suppliers print their own names in capitals too, so length is the only
#: thing separating a heading from a name and the two overlap. 12 is chosen
#: against both lists written out, not picked round:
#:
#:     skipped at 12      INVOICE 7, RECEIPT 7, ORIGINAL 8, CASH MEMO 9,
#:                        DEBIT NOTE 10, TAX INVOICE 11, CREDIT NOTE 11,
#:                        GST INVOICE 11
#:     NOT skipped at 12  BILL OF SUPPLY 14, ORIGINAL FOR RECIPIENT 22
#:     kept at 12         SUNIL AGENCIES 14, and every supplier name longer
#:                        than it. AB TRADERS is 10 and IS LOST, and a
#:                        two-word name that short is a real thing.
#:
#: MEASURED at 15 first, which was the obvious guess and was worse in the way
#: that matters: `SUNIL AGENCIES` was skipped as a heading and the item line
#: `Cement bags 1,200.00` was answered as the supplier instead. Skipping a real
#: name to answer with an item description is the wrong direction, so the number
#: came down until the name survived.
#:
#: Both mistakes are held at `BY_POSITION` either way, which is the only reason
#: a number this soft is allowed to decide anything at all.
A_SHORT_HEADING: Final = 12


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
    labelled_date = values_for(page.lines, DATE_LABEL, printing=_PRINTING)
    labelled_party = values_for(page.lines, PARTY_LABELS, printing=_PRINTING)
    date = _words_for(page, labelled_date)
    party = _words_for(page, labelled_party)

    # FALLBACK, NEVER AN OVERRIDE. Each of the three runs only where the label
    # search came back with nothing, so no labelled read can be replaced by a
    # guess - which is the one way this change could make a document read WORSE
    # than it did before it landed.
    #
    # THE CONDITION IS "NO LABEL MATCHED", NOT "NO WORDS CAME BACK", AND THE
    # DIFFERENCE IS A MEASURED DEFECT. Written the obvious way - `if not total`
    # - this fired on a page printing `TOTAL 1,020.70` and `TOTAL 1,626.70`,
    # where `the_one` had REFUSED on purpose because the two printings of the
    # labelled total disagree. The fallback then picked the larger of them, which
    # is exactly the coin toss that
    # `test_two_totals_that_disagree_are_refused_rather_than_picked_between`
    # exists to forbid, and it posts money. An empty answer from `_words_for`
    # covers two different facts - the page said nothing, and the page said two
    # things - and only the first of them is an absence a guess may fill.
    #
    # THE TAX AND THE NET HAVE NO FALLBACK, deliberately. A bill states its tax
    # as CGST plus SGST in two places, so there is no single unlabelled figure
    # that IS the tax, and picking one would put half the tax in the record as
    # though it were all of it. The net has the same shape of problem and, worse,
    # feeds `conservation.net_plus_tax_equals_gross` - a guessed net would turn
    # the arithmetic check into a comparison between a read number and an
    # invented one, which is how a law starts passing by construction.
    ceilings: dict[str, float] = {}
    if not labelled_date:
        date = _extract_date_by_position(page)
        if date:
            ceilings["date"] = BY_POSITION
    # THE POSITIONAL PARTY WENT THE SAME WAY AS THE POSITIONAL TOTAL, ON THE
    # SAME EVIDENCE, ON 2026-08-15. It is left here disabled rather than deleted
    # because the measurement is the valuable part and a deleted function takes
    # its measurement with it.
    #
    # MEASURED through `scripts/run_ground_truth.py`, the same harness that
    # killed the positional total:
    #
    #     party WRONG rather than unread     5  ->  8
    #     party EXACT                        unchanged
    #
    # Three answers added, three of them wrong, and no correct read gained
    # anywhere to set against them. The three, verbatim off the page:
    #
    #     'TNoIte Noe eTvan42'
    #     'TNoIte Noe eTvonas'
    #     'Nolte Noe eTan6o'
    #
    # That is the corpus 5x7 bitmap font coming apart, and the fallback took it
    # for a supplier because of WHERE IT SAT. Over the wider 413-document run
    # the same mechanism produced 20 guesses of which about 6 contained a real
    # name - the rest were a street address, two headings, a line item, and
    # seven pieces of engine noise.
    #
    # WHY THE CEILING WAS NOT ENOUGH, WHICH IS THE PART WORTH KEEPING. The guess
    # is held at `BY_POSITION` = 0.5, below `ASK_FLOOR`, so none of it could ever
    # post and the money was never at risk. The cost is different and it is real:
    # a party is an IDENTITY, and 31 documents that used to say "I read nothing"
    # now say "I think this is your supplier" while holding up OCR noise. A
    # person answering five questions a day spends them on `Qnme` and `ag ans`.
    # The owner's exchange rate is about SILENT WRONG POSTS, and this was never
    # one - so the ceiling did its job and the feature still fails on its own
    # terms.
    #
    # `_extract_party_by_position` and its tests stay. Re-enabling is this one
    # line, and `tests/test_positional_party.py` is what says what comes back
    # with it.
    _ = _extract_party_by_position  # kept reachable for its tests; not consulted

    # THE POSITIONAL TOTAL WAS WRITTEN, MEASURED AND REVERTED THE SAME HOUR.
    #
    # MEASURED through `scripts/run_ground_truth.py`, twenty corpus PNGs,
    # `_extract_total_by_position` picking the largest amount in the last ten
    # lines with `adapter._not_an_amount` filtering it:
    #
    #     before   total_paise   0 exact, 0 WRONG, 20 refused
    #     after    total_paise   0 exact, 15 WRONG, 5 refused
    #
    # Not one of the fifteen was right. GT-0046 answered 191508100 paise -
    # ₹19,15,081 on a bill whose total is three figures. The rule finds the
    # largest number near the foot of the page and on a real bill that is a
    # running balance, an account number or a misread column, which is what the
    # brief for this change predicted and what the corpus then proved.
    #
    # THE ZERO-WRONG INVARIANT IS WHY IT IS GONE AND NOT TUNED. A money field
    # went from 0 wrong to 15 wrong. `ARCHITECTURE.md:671` forbids moving a
    # threshold to make a measurement pass, and there is no threshold here to
    # move anyway: the answers were not marginal, they were unrelated to the
    # totals. The ceiling of 0.5 would have blocked all fifteen, and "the cage
    # caught it" is exactly the reasoning that is not allowed to justify
    # producing them.
    #
    # `_extract_total_by_position` and `_spans_of_numbers` went with it, rather
    # than staying behind a dead `if`. A function nothing calls is a library,
    # and a library that invents totals is worse than one nobody uses.

    # THE ENGINE IS SURE ABOUT CHARACTERS THAT ARE NOT A NAME. Added 2026-08-15.
    #
    # MEASURED on the real corpus, engine confidence beside the text: the party
    # `|Certificati` came back at 88 - a table rule glued to a word. `format_valid`
    # is True because ANY string is a syntactically valid party, and `consistent`
    # is True because there is nothing to disagree with, so `field_confidence`
    # returns about 0.88 and that clears `ASK_FLOOR` of 0.70. A person is then
    # asked whether `|Certificati` is their supplier, and one of their five daily
    # questions is gone.
    #
    # IT IS A CEILING, WHICH IS WHY IT IS SAFE TO ADD HERE. It goes through the
    # same `at_most` channel as the positional guess and `_judge` applies it with
    # `min`, so it can refuse a field and can never rescue one. Nothing about
    # what may POST changes; what changes is what is worth asking about.
    #
    # PARTY ONLY. The amounts already have a stronger guard - `paise_or_none`
    # refuses anything that is not a number - and a date has `looks_like_a_date`.
    # A name is the one field where any characters at all are syntactically
    # acceptable, so it is the one that needs this.
    #
    # MEASURED over the fixture sets in `tests/test_artifacts.py`: 8 of 12
    # measured artifacts refused, 0 of 10 real supplier names lost. The four it
    # cannot catch are listed there with reasons and asserted as misses.
    if party and (ceiling := artifacts.ceiling_for(_text_of(party))) is not None:
        ceilings["party"] = min(ceilings.get("party", 1.0), ceiling)

    total = _words_for_amount(page, amounts_for(page.lines, TOTAL_LABELS))
    tax = _words_for_amount(page, amounts_for(page.lines, TAX_WHOLE))
    net = _words_for_amount(page, amounts_for(page.lines, NET_LABELS))

    # THE LABEL AND ITS FIGURE ARE OFTEN ON DIFFERENT LINES. Owner decision
    # 2026-08-15, recorded in `project.state.md`.
    #
    # MEASURED over 60 real documents: 287 of 300 field slots died at "words
    # present, no label matched", and of the 18 lines that mention a total-ish
    # word at ALL, only 4 carried a figure on that same line - and all 4 already
    # matched. The list of "has a figure and is unmatched" came back EMPTY. So
    # more label spellings buy nothing; the other 14 print the label with the
    # figure somewhere else:
    #
    #     'SUB TOTAL'   'GRAND TOTAL'   'Total Cost'   'Total des'
    #
    # THE FAMILIES STAY APART. `TOTAL_LABELS` and `NET_LABELS` are searched
    # separately and their answers never merge - `SUB TOTAL` feeds the NET.
    # Merging them reports a correct GST bill short by exactly its tax, which is
    # the defect `cage/gate._lines_add_up_to` was written against.
    #
    # NEXT LINE ONLY, AND NO GEOMETRY. `Limits.max_line_distance` is 1 and no
    # `Box` is ever built, because `freeocr.Word` carries no position and the
    # owner ruled it stays that way. `nearby`'s `RIGHT_OF` and `BELOW` methods
    # need boxes, so they are unreachable from here by construction rather than
    # by a flag somebody can flip.
    for name, found, family in (
        ("total", total, TOTAL_LABELS),
        ("tax", tax, TAX_WHOLE),
        ("net", net, NET_LABELS),
    ):
        if found:
            continue
        taken, ceiling = _from_a_neighbouring_line(page, name, family)
        if taken:
            if name == "total":
                total = taken
            elif name == "tax":
                tax = taken
            else:
                net = taken
            ceilings[name] = min(ceilings.get(name, 1.0), ceiling)

    return Reading(
        date=date,
        party=party,
        total=total,
        tax=tax,
        net=net,
        at_most=MappingProxyType(ceilings),
    )


def _page_words(page: _Page) -> tuple[nearby.PageWord, ...]:
    """The page as `nearby` wants it: words, line numbers, and NO geometry.

    `box=None` on every word, and that is the owner's ruling rather than an
    oversight. `freeocr.Word` is text and confidence; the position Tesseract
    reported is discarded one layer out and stays discarded. `nearby` already
    degrades correctly - its pixel limits apply only where both boxes are known -
    so this hands it the weaker evidence and lets it say so.
    """
    return tuple(
        nearby.PageWord(
            text=word.text, line=number, box=None, confidence=word.confidence
        )
        for number, words in enumerate(page.words)
        for word in words
    )


def _from_a_neighbouring_line(
    page: _Page, field: str, family: tuple[str, ...]
) -> tuple[tuple[Word, ...], float]:
    """The figure printed under a label rather than beside it, or nothing.

    RETURNS NOTHING RATHER THAN GUESSING, in three separate situations, and the
    third is the one that matters:

        no candidate survived        the label is not on this page, or every
                                     figure near it was refused as a date, a
                                     quantity, an HSN code or a phone number
        the next line is a LABEL     a line that is itself a label is not a
                                     value. Without this, `SUB TOTAL` followed
                                     by `GRAND TOTAL` reads the words of the
                                     second label as the first one's figure
        two or more survived         owner decision: preserve the ambiguity, do
                                     not pick. Not the first, not the largest,
                                     not the closest

    THE CEILING IS `BY_POSITION`, 0.5, the same one every other find that leaned
    on position carries. A next-line figure rests on a LINE RELATIONSHIP rather
    than on a label and a figure printed together, and 0.5 is below `ASK_FLOOR`
    (0.70) and far below `AUTO_POST_FLOOR` (0.95). So nothing found here can
    post, and nothing found here can even spend one of the five daily questions.
    It becomes visible for review without the reader claiming certainty it has
    not got.
    """
    candidates = nearby.candidates_for(
        _page_words(page),
        field=field,
        labels=family,
        limits=nearby.Limits(max_line_distance=1),
    )
    # EVERY FILTER RUNS BEFORE THE COUNT, and the order is the whole of the bug
    # this shape fixes. Counting first and filtering after made a candidate this
    # pass does not even allow - a `previous_line` figure - inflate the survivor
    # count to two, and two survivors is an ambiguity. So a bill printing
    # `GRAND TOTAL` on its own line came back with NO total, refused for
    # disagreeing with a reading that was never eligible.
    standing = tuple(
        one
        for one in candidates
        if not one.rejected
        and one.method in _NEIGHBOURING
        and not _belongs_to_another_family(field, one.label_text)
        # A line that is itself a label is not a value: `SUB TOTAL` followed by
        # `GRAND TOTAL` must not read the second label's words as the first
        # one's figure.
        and not _is_a_label(one.value_text)
    )
    # OWNER DECISION: two survivors is an ambiguity to preserve, not a tie to
    # break. Not the first, not the largest, not the closest.
    if len(standing) != 1:
        return (), BY_POSITION
    only = standing[0]
    line = _line_printing(page, only.value_text)
    if line is None:
        return (), BY_POSITION
    return _words_at(page, line, 0, len(page.lines[line])), BY_POSITION


def _belongs_to_another_family(field: str, label_text: str) -> bool:
    """Is this label really a DIFFERENT field's label?

    THE COLLISION IS REAL AND IT IS EXACTLY THE SUBTOTAL DEFECT. `labels._LABEL_AT`
    anchors a label at the start of a line OR after whitespace, so the entry
    `TOTAL` matches inside `SUB TOTAL`. Searching the TOTAL family on a bill that
    prints both therefore finds TWO labels - the real `GRAND TOTAL` and the
    `TOTAL` buried in `SUB TOTAL` - and two candidates is an ambiguity, so the
    total came back UNREAD on exactly the bills that state it most clearly.

    MEASURED before this guard, on a four-line page printing both:

        SUB TOTAL / 1,046.24 / GRAND TOTAL / 1,234.56
            net    104624     correct
            total  None       WRONG - the bill says 1,234.56 on its own line

    The fix is to ask which family the label PRINTED ON THE PAGE belongs to,
    rather than which family we happened to be searching. `SUB TOTAL` is a net
    label, so it is not a total no matter which search found it.

    ONE DIRECTION ONLY, deliberately. A net search is not filtered against the
    total family, because `TOTAL` is a substring of `SUB TOTAL` and not the other
    way round - `GRAND TOTAL` contains no net label. Filtering both ways would be
    symmetry for its own sake and would refuse a real net.
    """
    if field != "total":
        return False
    printed = label_text.strip().upper()
    return any(printed.startswith(label) for label in NET_LABELS)


def _line_printing(page: _Page, printed: str) -> int | None:
    """Which line carries exactly this text, when exactly one does.

    `nearby.Candidate` reports the characters it took and the METHOD it took
    them by, and deliberately not a line number - it works on an abstract
    geometry so it can be tested without an engine. So the line is found here,
    by looking for it.

    `None` WHEN TWO LINES MATCH, which is the case worth naming: a bill that
    prints the same figure twice gives no way to say which printing this
    candidate came from, and picking one would be a guess wearing a line number.
    """
    matches = [
        number
        for number, line in enumerate(page.lines)
        if line.strip() == printed.strip()
    ]
    return matches[0] if len(matches) == 1 else None


#: THE ONE METHOD THIS PASS ALLOWS, and `nearby.PREVIOUS_LINE` is deliberately
#: not in it.
#:
#: A VALUE IS PRINTED AFTER ITS LABEL, NOT BEFORE IT. MEASURED on a four-line
#: page printing both families:
#:
#:     SUB TOTAL          <- line 0
#:     1,046.24           <- line 1
#:     GRAND TOTAL        <- line 2
#:     1,234.56           <- line 3
#:
#: searching the TOTAL family found `GRAND TOTAL` and then TWO survivors -
#: `1,234.56` by next_line and `1,046.24` by previous_line. Two survivors is an
#: ambiguity, so the total came back UNREAD on a bill that states it on its own
#: line. The figure above a label is the PREVIOUS field's value, and reading it
#: as this one's is how a subtotal becomes a total.
#:
#: `nearby.RIGHT_OF` and `nearby.BELOW` are absent for a different reason: they
#: need bounding boxes, which `freeocr.Word` does not carry and which the owner
#: ruled it will not. They are unreachable as well as unlisted, so a future
#: geometry change has to switch them on deliberately rather than find them
#: already on.
_NEIGHBOURING: Final = (nearby.NEXT_LINE,)


#: Every label family this reader knows, for the next-line guard below.
_EVERY_FAMILY: Final[tuple[tuple[str, ...], ...]] = (
    TOTAL_LABELS,
    NET_LABELS,
    TAX_WHOLE,
    TAX_PARTS,
    PARTY_LABELS,
    DATE_LABEL,
)


def _is_a_label(line: str) -> bool:
    """Is this whole line one of the labels this reader knows?

    THE GUARD THAT STOPS A LABEL BEING READ AS A VALUE. A bill printing

        SUB TOTAL
        GRAND TOTAL
        1,234.56

    has a next line under `SUB TOTAL` that is not a figure at all. Without this,
    the words `GRAND TOTAL` come back as the subtotal's value.
    """
    printed = line.strip().upper()
    if not printed:
        return False
    return any(
        printed.startswith(label) for family in _EVERY_FAMILY for label in family
    )


def _text_of(words: tuple[Word, ...]) -> str:
    """The words joined back into the line the page printed.

    `artifacts` judges a VALUE, and a value is the words together: `|Certificati`
    is one word, but `TNoIte Noe eTvan42` is three and the case flipping only
    shows up across the whole of it.
    """
    return " ".join(word.text for word in words)


def _extract_date_by_position(page: _Page) -> tuple[Word, ...]:
    """The first date-shaped run of characters in the head of the page.

    EVERY WORD THE MATCH TOUCHES, through `_words_at`, and that is a fix to the
    obvious version rather than a flourish. `15 Aug 2026` is THREE words to the
    engine, so returning only the word that contains the match start hands
    `freeocr._joined` the single word `15` and the field becomes a fragment
    wearing the confidence of one legible character. `_words_at` already answers
    "which words do these character positions belong to" by OVERLAP, which is
    exactly the question, and it is the same call the labelled path makes.

    FIRST MATCH AND NOT BEST MATCH. There is no way to rank two dates on a page
    without knowing which is the bill's, and inventing a preference would be a
    second opinion nothing has measured. The shapes are tried in order per line,
    so the ISO form - the only one that can survive `freeocr._read_date` - wins
    a line it shares with another shape.
    """
    for index, line in enumerate(page.lines[:FIRST_LINES_FOR_A_DATE]):
        for shape in DATE_SHAPES:
            found = shape.search(line)
            if found:
                return _words_at(page, index, found.start(), found.end())
    return ()


def _mostly_digits(line: str) -> bool:
    """Is this line more figures than letters? Then it is not somebody's name."""
    solid = [character for character in line if not character.isspace()]
    if not solid:
        return True
    return sum(c.isdigit() for c in solid) / len(solid) > MOSTLY_DIGITS


def _a_short_heading(line: str) -> bool:
    """Is this a printed heading - `TAX INVOICE` - rather than a name?

    Two conditions and both are required. No lower-case letter, because a
    heading is set in capitals; and short, because a supplier's name in capitals
    is usually longer than the words a bill uses to announce itself.
    """
    return not any(c.islower() for c in line) and len(line.strip()) <= A_SHORT_HEADING


def _extract_party_by_position(page: _Page) -> tuple[Word, ...]:
    """The first line of the letterhead that could be somebody's name.

    THE MOST DANGEROUS OF THE THREE, and the report on this change says so with
    the strings it actually picked. The rule the owner set - the first line that
    is not mostly digits and is not a short all-caps heading - cannot tell a
    supplier from a customer, from a street, or from `Original for Recipient`,
    because none of those is mostly digits and none is short. What it CAN do is
    skip the two commonest wrong answers, `TAX INVOICE` and a row of figures.
    The rest is carried by the ceiling: at `BY_POSITION` the closed owner rule
    "party unknown -> ALWAYS ASK, never auto-create a ledger" is kept by
    arithmetic, because 0.5 is below `ASK_FLOOR` and nothing at 0.5 becomes a
    vendor identity.
    """
    for index, line in enumerate(page.lines[:FIRST_LINES_FOR_A_PARTY]):
        if not line.strip() or _mostly_digits(line) or _a_short_heading(line):
            continue
        return page.words[index]
    return ()


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
