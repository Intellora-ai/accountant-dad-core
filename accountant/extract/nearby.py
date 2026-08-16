"""A figure that is NOT on the label's line, found by looking beside the label.

WHY THIS FILE EXISTS
--------------------
MEASURED 2026-08-15 across 60 real image documents, at the point in the pipeline
where the reading engine had already finished its own job:

    6,210 word rows returned by the engine, 4,962 of them carrying characters
    296 of 300 field slots died at "words present, NO LABEL MATCHED"

The engine works. Reading the ink is not the constraint. The obvious repair -
more spellings in `labels.py` - was measured, and it buys ZERO:

    18 lines on those 60 documents mention a total-ish word
     4 of those carried a number on the SAME line, and `labels.py` already
       matches all 4
     0 lines had a number on the line AND went unmatched

That last row is the whole argument. The "has a number beside a total word but
nothing read it" list came back EMPTY, so there is nothing left for a wider
vocabulary to win. The other 14 lines print the label with NO number on the line
at all:

    SUB TOTAL            GRAND TOTAL          Total Cost
    Total des            TOTAL QUANTITY:      AMOUNT IN WORDS: TWO THOUSAND NINE

The figure is on the next line, or in the box to the right, or below. Finding it
is the subject of this file, and this file is the ONLY place in this package
allowed to take a number that its label did not sit beside.

WHY THAT IS DANGEROUS, AND WHAT IS DONE ABOUT IT
-------------------------------------------------
`adapter.TYPED_TEXT_MIME` records what happens when a reader points at a number
because of where it sits: twenty invented totals. Distance is a weaker reason
than a label, and a weaker reason has to be BOUNDED and ARGUED WITH.

    BOUNDED   `Limits` caps the search in lines and in pixels. Nothing outside
              the bound is looked at. There is no whole-page fallback here and
              there is deliberately no parameter that asks for one.
    ARGUED    Eight detectors say what a nearby number looks like OTHER than
              money - a date, a percentage, a phone number, a postal code, a
              quantity, an HSN or SAC code, an invoice number, a malformed
              amount. Each is a separate public function with its own test.
    KEPT      A refused candidate is RETURNED with the reason written on it, not
              deleted. "998311 sat beside GRAND TOTAL and was refused because it
              is the shape of an HSN code" is a sentence a person can overrule.
              A deletion is a sentence nobody can see.
    NEVER ONE `candidates_for` returns every survivor and picks nothing. Where
              two survivors disagree and neither is clearly closer,
              `review_needed` says so in words. A caller holding a tuple cannot
              post silently, because there is no single value in its hand.

THE TRAP THIS MODULE EXISTS TO NOT FALL INTO
----------------------------------------------
`TOTAL QUANTITY:` is one of the 14 measured lines. A search that takes the
nearest number to a total label reads a case count of 12 as twelve rupees. It is
refused here as a quantity, by name, and `tests/test_nearby.py` pins it.

WHY THE GEOMETRY IS ABSTRACT AND NOT THE ENGINE'S OWN WORDS
-------------------------------------------------------------
`freeocr.Word` carries text and confidence and NO position, on purpose - that
module decides nothing from where a word sat, and carrying the geometry would be
the first line of something that did. This module's whole question is where a
word sat, so it declares the smallest geometry that answers it and lets the
integrator adapt the engine's rows onto it.

The gain is not tidiness. It is that every test below is a fixture of integers:
no tesseract binary, no image, no engine, no timeout, nothing that can be
missing on a machine. The rules can be argued with by reading them.

WHAT THIS COSTS, STATED RATHER THAN HIDDEN
--------------------------------------------
SIX BARE DIGITS ARE REFUSED. `123456` next to a total is refused as a postal
code. It is also the shape of a SAC code with no `SAC` printed beside it, and
the shape of a real ₹1,23,456 written with no comma and no paise - including a
real `1,23,456` whose commas the engine dropped. Nothing here can tell the three
apart. They are refused because they end differently: two of them posted are
money that never existed, and all three refused are one question to a person.

`AMOUNT IN WORDS` IS NOT UNDERSTOOD. `AMOUNT` is a total label, so that measured
line opens a search whose own line holds no number - and a number on the line
below will be offered as a candidate. It will be ranked and, against a second
candidate, flagged for review, but it is not recognised for what it is. Teaching
this file to read a sum spelled out in English words is a different module.

THE PIXEL LIMIT IS IN PIXELS, AND PIXELS ARE NOT A LENGTH. 400px is a third of
the width of an A4 page rendered at 150 DPI and a sixth of the same page at 300
DPI. The caller renders the image and is therefore the only party that knows
which of those it means; a caller that changes its rendering DPI and not this
number has silently changed the search radius. That is a CONTROL the integrator
owns, and it is named here so it cannot be discovered later.

NO NETWORK, NO CLOCK, NO FILESYSTEM, NO SUBPROCESS, NO THIRD-PARTY IMPORT.
Integers and strings in, candidates out. The money vocabulary - what a currency
token looks like, what parses as paise, what to do when two readings disagree -
is imported from `labels.py` and never restated. A second money vocabulary is a
defect this repository has already hit twice.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Final

from accountant.labels import CURRENCY, paise_or_none, the_one

# =============================================================================
# the geometry, which is the caller's to fill in
# =============================================================================


@dataclass(frozen=True)
class Box:
    """Where a word sat, in whatever pixel grid the caller rendered.

    Half-open is NOT assumed and does not matter: every comparison here is an
    inequality between two boxes from the SAME grid, so a consistent off-by-one
    convention cancels out. What is assumed is `left <= right`, `top <= bottom`
    and an origin at the TOP LEFT - which is what every image library this
    repository is allowed to import reports, `Pillow` included.

    Frozen, like `labels.Found`. A position that can be edited after the fact is
    not evidence about where anything was.
    """

    left: int
    top: int
    right: int
    bottom: int


@dataclass(frozen=True)
class PageWord:
    """One word, where it sat, and how sure the engine said it was.

    `line` is the caller's own line numbering and this module never checks it
    against the geometry. That is deliberate: the two disagree on real pages - a
    table column often gets its own line number - and the whole reason
    `right_of` and `below` exist is to reach a value the line numbering lost.

    `box` MAY BE None, and a caller with no geometry is a supported caller
    rather than a broken one. `pagereader.py` hands on `freeocr.Word` rows that
    carry no position at all today. With no box the line methods still work and
    the pixel limit cannot be applied - stated again on `Limits`.

    `confidence` IS CARRIED AND NEVER RANKED ON. The engine's score is a
    statement about CHARACTERS and this module's question is about POSITION;
    ranking on a mixture of the two would produce a number meaning neither. It
    rides along for the same reason `labels.Found` carries a character range: so
    the caller that ends up using these words can score them itself.
    """

    text: str
    line: int
    box: Box | None
    confidence: int


# =============================================================================
# how far the search may reach
# =============================================================================


@dataclass(frozen=True)
class Limits:
    """The edge of the search. Nothing outside it is ever taken.

    `max_line_distance = 1` follows from the 14 measured lines: each prints a
    label with the figure not on it, so the figure is the next thing printed.
    WHICH neighbouring line holds it was NOT measured - the corpus pass counted
    label lines, not the lines around them - so 1 is the smallest radius that
    can work rather than a fitted number. Raising it widens `next_line` and
    `previous_line` together, and is a decision that needs evidence attached.

    `max_gap = 400` IS NOT MEASURED AND THE UNIT IS THE PROBLEM. See the module
    docstring: 400 pixels is a third of an A4 width at 150 DPI and a sixth of it
    at 300 DPI, so the same number is two different search radii depending on
    how the caller rendered the page. The caller owns that control.

    IT IS APPLIED ONLY WHERE BOTH BOXES ARE KNOWN. A caller supplying no
    geometry gets the line limit and nothing else, which is weaker - and saying
    so here is better than a silent second behaviour discovered on a scan.
    """

    max_line_distance: int = 1
    max_gap: int = 400


#: How much closer the winner must be than the runner-up before this module
#: calls it clearly stronger at the same method and the same line distance.
#:
#: NOT MEASURED, AND IT ERRS TOWARDS ASKING. There is no corpus of
#: two-candidate pages to measure it on: the measured pass counted label lines,
#: not what sat around them. 40 pixels is roughly one line of 10pt text at 300
#: DPI, so two figures within 40px of each other are being called "the same
#: distance away", which is what a person looking at the page would say.
#:
#: WIDER MEANS MORE REVIEW, NOT MORE RISK. Every value this number moves is a
#: value that either goes to a person or gets picked by a margin nobody
#: measured. If it is ever tuned, it is tuned upward.
CLEARLY_CLOSER_GAP: Final = 40


# =============================================================================
# where the search looked
# =============================================================================

#: The label's own line, to the right of the label.
SAME_LINE: Final = "same_line"

#: A line printed after the label's, within `Limits.max_line_distance`.
NEXT_LINE: Final = "next_line"

#: A line printed before the label's, within `Limits.max_line_distance`.
PREVIOUS_LINE: Final = "previous_line"

#: A box to the right of the label sharing a horizontal band with it, on a line
#: the caller's numbering put somewhere else entirely. This is the method that
#: reaches a two-column table cell.
RIGHT_OF: Final = "right_of"

#: A box below the label sharing a vertical band with it, likewise on a line
#: number that does not neighbour the label's - AND vertically closer than that
#: line number implies, which is what stops this method being a way round
#: `Limits.max_line_distance`. See `_placed` for the arithmetic and for the
#: defect it was written against.
BELOW: Final = "below"

#: THE SEARCH ORDER, AND IT IS ALSO THE STRENGTH ORDER. Earlier is a better
#: reason to believe the figure belongs to the label, so a candidate found by an
#: earlier method outranks one found later regardless of pixels. A figure on the
#: label's own line was printed AGAINST it; a figure found by geometry alone was
#: printed NEAR it, which is a weaker claim about the same page.
#:
#: A span reachable by more than one method keeps the EARLIEST one. Without that
#: the same figure comes back twice under two names, and a caller counting
#: candidates sees an ambiguity that is not on the page.
SEARCH_ORDER: Final[tuple[str, ...]] = (
    SAME_LINE,
    NEXT_LINE,
    PREVIOUS_LINE,
    RIGHT_OF,
    BELOW,
)


# =============================================================================
# what a nearby number can be other than money
# =============================================================================

A_DATE: Final = "a date"
A_PERCENTAGE: Final = "a percentage or a tax rate"
A_QUANTITY: Final = "a quantity"
AN_HSN_OR_SAC_CODE: Final = "an HSN or SAC code"
A_PHONE_NUMBER: Final = "a phone number"
A_POSTAL_CODE: Final = "a postal code"
AN_INVOICE_NUMBER: Final = "an invoice number"
A_MALFORMED_AMOUNT: Final = "a malformed amount"

#: THE ORDER THE DETECTORS RUN IN, AND IT IS PART OF THE ANSWER. One figure can
#: satisfy two of them - `998311` is six bare digits AND, beside the word HSN, a
#: service code - and the reason a person reads should be the one that knows the
#: most. So the CONTEXT-LED detectors run before the SHAPE-ONLY ones: a detector
#: that has read the words around the figure is better informed than one looking
#: at the digits alone.
#:
#: `a date` runs first regardless, because a date's shape is unmistakable and no
#: context improves on it. `a malformed amount` runs last, because it is the
#: residue: it fires only when nothing recognised the figure AND it will not
#: parse as paise, which is the honest thing to say at that point.
REJECTION_ORDER: Final[tuple[str, ...]] = (
    A_DATE,
    A_PERCENTAGE,
    A_QUANTITY,
    AN_HSN_OR_SAC_CODE,
    A_PHONE_NUMBER,
    A_POSTAL_CODE,
    AN_INVOICE_NUMBER,
    A_MALFORMED_AMOUNT,
)


#: A day, a month and a year separated by the SAME mark twice. The repetition is
#: load-bearing: `1.234.56` is a continental thousands grouping, not a date, and
#: it fails here on its middle group being three digits wide. `32.838,00` fails
#: because its two separators differ. Both are real amounts printed on documents
#: in this repository's own corpus.
_DATE_WITH_MARKS: Final = re.compile(r"(\d{1,4})([/.-])(\d{1,2})\2(\d{1,4})")

#: `01-Aug-2026`, `1 Jan 26`, `15/March/2026`. Letters cannot reach here through
#: `candidates_for` - a value span is digits and decoration - so this arm is for
#: direct callers and for the day one widens what it hands in.
_DATE_WITH_A_MONTH_NAME: Final = re.compile(
    r"\d{1,2}[\s/.-]*[A-Za-z]{3,9}[\s/.-]*\d{2,4}"
)

#: A four-digit year a bill could plausibly carry. Wider than any bill needs, on
#: purpose: this is a shape test, and narrowing it to the current decade would
#: let a document's age decide whether its date is recognised as one.
_EARLIEST_YEAR: Final = 1900
_LATEST_YEAR: Final = 2199

_LAST_DAY_OF_ANY_MONTH: Final = 31
_LAST_MONTH: Final = 12
_A_TWO_DIGIT_YEAR: Final = 99


def looks_like_a_date(text: str) -> bool:
    """Is this printed figure a date rather than an amount?

    BOTH ORDERS ARE ACCEPTED because both are printed on real bills: `28/01/26`
    day-first, and `2018-09-21` year-first, the second lifted from this
    repository's own corpus. The parts are checked for plausibility - a month of
    1 to 12, a day of 1 to 31 - so an amount that happens to carry two identical
    separators is not called a date on the strength of its punctuation.

    IT DOES NOT PARSE THE DATE AND MUST NOT START TO. Whether `03/04/26` is
    March or April is a question `textlayer.py` already owns, and answering it
    in two places is how two readers end up disagreeing about one bill. This
    function answers one thing: is this figure money, or is it a date standing
    where money was expected.
    """
    stripped = text.strip()
    if _DATE_WITH_A_MONTH_NAME.fullmatch(stripped):
        return True
    marks = _DATE_WITH_MARKS.fullmatch(stripped)
    if marks is None:
        return False
    first, middle, last = int(marks.group(1)), int(marks.group(3)), int(marks.group(4))
    if not 1 <= middle <= _LAST_MONTH:
        return False
    day_first = 1 <= first <= _LAST_DAY_OF_ANY_MONTH and (
        last <= _A_TWO_DIGIT_YEAR or _EARLIEST_YEAR <= last <= _LATEST_YEAR
    )
    year_first = (
        _EARLIEST_YEAR <= first <= _LATEST_YEAR and 1 <= last <= _LAST_DAY_OF_ANY_MONTH
    )
    return day_first or year_first


#: A percentage that survived as one word, and the largest rate that is not
#: nonsense. 100 rather than 28 - the highest GST slab - because this is a shape
#: test, and a bill may print any percentage beside a total, a discount included.
_A_SMALL_NUMBER: Final = re.compile(r"(\d{1,3})(?:\.\d{1,2})?")
_LARGEST_SENSIBLE_RATE: Final = 100


def looks_like_a_percentage(text: str, context: str) -> bool:
    """Is this figure a rate rather than the money the rate applies to?

    TWO ARMS, AND THE SECOND IS THE ONE THAT EARNS ITS KEEP. A percent sign
    inside the figure is easy. The case that matters is the engine splitting
    `18 %` into two words, so the figure arrives as a bare `18` with the sign
    stranded beside it - which is the only reason the context is read at all.

    THE WORD `RATE` WAS CONSIDERED AND IS DELIBERATELY NOT HERE. Every line-item
    row of an ordinary Indian bill prints `RATE`, so accepting it as evidence
    would refuse any genuine total under ₹100 that shared a line with one. The
    percent sign is the only mark that means one thing.

    `labels._RATE` consumes `@ 18%` after a label and never reads it. The same
    decision on a different rung: a percentage is not an amount, and computing
    tax from a rate would be arithmetic on a figure the bill already states.
    """
    if "%" in text:
        return True
    if "%" not in context:
        return False
    small = _A_SMALL_NUMBER.fullmatch(text.strip())
    if small is None:
        return False
    return int(small.group(1)) <= _LARGEST_SENSIBLE_RATE


#: What a bill calls a count. Word-bounded, so `NOS` cannot fire inside an
#: ordinary word that happens to contain those three letters.
_COUNTED_IN: Final = re.compile(r"\b(?:QUANTITY|QTY|NOS|PCS|PIECES|UNITS)\b")

#: A count: plain digits, optionally a fractional part for goods sold by weight.
#: No comma grouping and no currency, because a figure wearing either of those
#: is being printed as money, and a count is not.
_A_COUNT: Final = re.compile(r"\d{1,6}(?:\.\d{1,3})?")


def looks_like_a_quantity(text: str, context: str) -> bool:
    """Is this figure a count of things rather than a sum of money?

    THIS IS THE DETECTOR THIS MODULE WAS BUILT AROUND. `TOTAL QUANTITY:` is one
    of the 14 measured lines that print a total-ish word with no number on it. A
    search that takes the nearest number to a total label reads the 12 cases on
    that bill as twelve rupees, and that posting looks entirely ordinary in a
    ledger afterwards. `tests/test_nearby.py` pins the refusal.

    CONTEXT ONLY, NEVER SHAPE ALONE. A bare `12` beside a total label is also
    exactly what a ₹12 bill looks like, and refusing every small integer would
    throw real money away to catch this. The word `QUANTITY` on the page is the
    evidence; the digits on their own are evidence of nothing.
    """
    if _COUNTED_IN.search(context.upper()) is None:
        return False
    return _A_COUNT.fullmatch(text.strip()) is not None


#: The tax classification codes on an Indian bill, by name.
_CLASSIFIED_AS: Final = re.compile(r"\b(?:HSN|SAC)\b")

#: HSN codes are 4, 6 or 8 digits; SAC codes are 6. Nothing between, nothing
#: longer.
_A_CLASSIFICATION_CODE: Final = re.compile(r"\d{4}|\d{6}|\d{8}")


def looks_like_an_hsn_or_sac_code(text: str, context: str) -> bool:
    """Is this figure a goods or services classification code?

    `HSN/SAC: 998311` is printed on the same line as the party on this
    repository's corpus - `labels._NEXT_LABEL` exists because of that exact
    string - so a six-digit code sitting a line from a total label is an
    ordinary page layout rather than a strange one.

    CONTEXT-LED, AND IT RUNS BEFORE THE POSTAL CODE for that reason. `998311`
    satisfies both shapes; only one of the two has read the word beside it.
    """
    if _CLASSIFIED_AS.search(context.upper()) is None:
        return False
    return _A_CLASSIFICATION_CODE.fullmatch(text.strip()) is not None


#: What a bill calls a number somebody rings.
_RUNG_ON: Final = re.compile(
    r"\b(?:PHONE|TEL|TELEPHONE|MOB|MOBILE|CONTACT|FAX|HELPLINE)\b"
)

#: An Indian mobile number is ten digits beginning 6, 7, 8 or 9. A landline
#: dialled nationally carries a leading 0 and eleven digits. With the country
#: code it is 91 and then the mobile.
_AN_INDIAN_MOBILE: Final = re.compile(r"[6-9]\d{9}")
_A_NATIONAL_LANDLINE: Final = re.compile(r"0\d{10}")
_WITH_THE_COUNTRY_CODE: Final = re.compile(r"91[6-9]\d{9}")

#: For the context arm only: digits and dialling punctuation, and nothing that
#: means money. A figure carrying a decimal point or a comma is being printed as
#: an amount, and the word `CONTACT` elsewhere on the line does not change that.
_DIALLING_CHARACTERS: Final = re.compile(r"[\d +\-()]+")
_ENOUGH_DIGITS_TO_DIAL: Final = 6


def looks_like_a_phone_number(text: str, context: str) -> bool:
    """Is this figure a number somebody rings rather than a sum of money?

    THE SHAPE ARM IS THE STRONG ONE. Ten digits starting 6-9 is an Indian mobile
    and is nothing else: as money it would be ₹98,76,54,321 beside a total, with
    no comma grouping and no paise, which no supplier prints.

    THE CONTEXT ARM IS NARROWED ON PURPOSE. It fires only on figures made of
    dialling characters - no decimal point, no comma - so a real `1,234.56`
    printed near the word `CONTACT` in a footer is not refused as a telephone.
    """
    dialled = re.sub(r"[^\d+]", "", text)
    if dialled.startswith("+"):
        return True
    if _AN_INDIAN_MOBILE.fullmatch(dialled):
        return True
    if _A_NATIONAL_LANDLINE.fullmatch(dialled):
        return True
    if _WITH_THE_COUNTRY_CODE.fullmatch(dialled):
        return True
    if _RUNG_ON.search(context.upper()) is None:
        return False
    if _DIALLING_CHARACTERS.fullmatch(text.strip()) is None:
        return False
    return len(dialled) >= _ENOUGH_DIGITS_TO_DIAL


#: An Indian PIN code: exactly six digits, no grouping, no decimal.
_SIX_BARE_DIGITS: Final = re.compile(r"\d{6}")


def looks_like_a_postal_code(text: str) -> bool:
    """Is this figure an address code rather than a sum of money?

    SHAPE ONLY, AND IT REFUSES MORE THAN PIN CODES. Six bare digits is also a
    SAC code with no `SAC` printed beside it, and it is also a genuine
    ₹1,23,456 written with no comma and no paise - including a genuine
    `1,23,456` whose commas the reading engine dropped.

    NOTHING HERE CAN TELL THOSE THREE APART AND IT DOES NOT PRETEND TO. All
    three are refused and the commonest is named, because being wrong about
    WHICH six-digit thing it was costs nothing: every one of the three ends the
    same way, which is a candidate carrying a reason and a person being asked.
    Only posting them differs, and two of the three posted are money that never
    existed.

    THAT IS A REAL COST AND IT IS NOT HIDDEN - the module docstring states it
    again under WHAT THIS COSTS. It is paid because a refusal is a question and
    a wrong post is a wrong ledger.
    """
    return _SIX_BARE_DIGITS.fullmatch(text.strip()) is not None


#: What a bill calls a reference somebody quotes back at it.
_REFERENCED_AS: Final = re.compile(r"\b(?:INVOICE|BILL|REF|ORDER|CHALLAN|GSTIN|PAN)\b")

#: A long unbroken digit run: too many digits to be a printed amount, which
#: would be carrying grouping or paise by the time it got this long.
_A_LONG_DIGIT_RUN: Final = re.compile(r"\d{7,}")

#: The currency decoration `labels.CURRENCY` already names, stripped from both
#: ends before letters are counted. Without this, every `Rs. 1,234.56` reads as
#: "letters and digits together" and is refused as a reference number.
_CURRENCY_AT_EITHER_END: Final = re.compile(rf"^{CURRENCY}|{CURRENCY}$", re.IGNORECASE)


def looks_like_an_invoice_number(text: str, context: str) -> bool:
    """Is this figure a reference rather than a sum of money?

    LETTERS MIXED WITH DIGITS IS THE SIGNAL - `INV-2026-0041`, `GST/26/119` -
    and the currency decoration has to come off first or every `Rs. 1,234.56`
    trips it. `labels.CURRENCY` is the list of what decoration is; a second list
    here would be a second answer to what a rupee looks like.

    A `#` ANYWHERE IS ENOUGH ON ITS OWN. No supplier prints money behind a hash.
    """
    if "#" in text:
        return True
    undecorated = _CURRENCY_AT_EITHER_END.sub("", text.strip()).strip()
    has_letters = re.search(r"[A-Za-z]", undecorated) is not None
    has_digits = re.search(r"\d", undecorated) is not None
    if has_letters and has_digits:
        return True
    if _REFERENCED_AS.search(context.upper()) is None:
        return False
    return _A_LONG_DIGIT_RUN.fullmatch(text.strip()) is not None


def looks_like_a_malformed_amount(text: str) -> bool:
    """Would this figure fail to parse as money at all?

    THE RESIDUE, AND IT RUNS LAST. It fires only where no detector recognised
    the figure as something else AND `labels.paise_or_none` refuses it - which
    covers sub-paise precision, two decimal points, a glyph the engine invented,
    and a run of digits with a space through the middle.

    IT IS `paise_or_none` ITSELF, ON THE EXACT CHARACTERS THE CALLER WILL USE,
    AND THAT IS THE POINT. It holds the one invariant every caller of this
    module depends on:

        rejected == ""  IMPLIES  paise_or_none(value_text) is not None

    The first draft of this function did NOT do that. It stripped a trailing
    `/-` before parsing, mirroring `labels._ONLY_AMOUNT`, and the probe caught
    what that costs: `45,61,546/-` came back ACCEPTED with an empty `rejected`
    while the caller's own `paise_or_none` on the same characters returned None.
    An accepted candidate nobody can convert is worse than a refused one,
    because a refusal carries a sentence and that carried nothing.

    That is the two-vocabularies defect in miniature, and the repair is not to
    copy the marker rule more carefully - it is to have ONE parser decide, on
    ONE string. So nothing is stripped here.

    THE COST IS NAMED AND IS PAID IN THE RIGHT PLACE. `₹45,61,546/-` is printed
    on `data/real_invoices_indian/gst-portal-and-govt-002.pdf` in this
    repository's own corpus, and `/-` means only "and no paise", which the
    digits already said. `labels._ONLY_AMOUNT` accepts and discards it; the
    value-level `labels.paise_or_none` does not know it. Until that asymmetry is
    closed inside `labels.py` - the exact diff is in this task's report - a `/-`
    sum reaching this module is refused as a malformed amount, which is a
    question to a person rather than a figure nothing can convert.
    """
    return paise_or_none(text) is None


def _why_refused(value_text: str, context: str) -> str:
    """The first thing this figure looks like other than money, or empty.

    The order is `REJECTION_ORDER` and a test asserts it, because a figure
    satisfying two detectors must always name the same one. A reason that
    changes under a refactor is a reason nobody can pin - the argument
    `labels.values_for` makes about the order in which it scans labels.
    """
    if looks_like_a_date(value_text):
        return A_DATE
    if looks_like_a_percentage(value_text, context):
        return A_PERCENTAGE
    if looks_like_a_quantity(value_text, context):
        return A_QUANTITY
    if looks_like_an_hsn_or_sac_code(value_text, context):
        return AN_HSN_OR_SAC_CODE
    if looks_like_a_phone_number(value_text, context):
        return A_PHONE_NUMBER
    if looks_like_a_postal_code(value_text):
        return A_POSTAL_CODE
    if looks_like_an_invoice_number(value_text, context):
        return AN_INVOICE_NUMBER
    if looks_like_a_malformed_amount(value_text):
        return A_MALFORMED_AMOUNT
    return ""


# =============================================================================
# one thing the search found
# =============================================================================


@dataclass(frozen=True)
class Candidate:
    """One figure found beside one label, and everything used to judge it.

    ACCEPTED AND REFUSED CANDIDATES ARE THE SAME TYPE and both are returned.
    `rejected` is empty on an accepted one and carries the plain reason on a
    refused one. A refusal deleted instead of returned is a decision nobody can
    see and nobody can overrule.

    NO PAISE FIELD, ON PURPOSE. `value_text` is the characters the bill printed,
    and the caller converts them with `labels.paise_or_none`, which is the money
    parser this repository already has. A number produced here as well would be
    a second answer to what `10.005` is worth.

    THE INVARIANT THAT MAKES THAT SAFE, AND IT IS TESTED ACROSS A BATTERY OF
    PRINTED FIGURES:

        rejected == ""  IMPLIES  paise_or_none(value_text) is not None

    An accepted candidate always converts. `looks_like_a_malformed_amount` holds
    that line by BEING `paise_or_none`, on the same characters, with nothing
    stripped in between - see its docstring for the draft that broke it.
    """

    #: What was being looked for - `total`, `tax`, `net`. Carried, never read by
    #: this module: the search is the same shape for every field, and the name
    #: is for the sentence a person eventually sees.
    field: str

    #: The label AS PRINTED, not the vocabulary entry that matched it. A person
    #: reading a review sentence should see `SUB TOTAL` if that is what the page
    #: says, even where the entry that matched was spelled `SUBTOTAL`.
    label_text: str

    #: The figure as printed, decoration and all. Not a number - see above.
    value_text: str

    #: Where the figure sat, or None where the caller supplied no geometry.
    box: Box | None

    #: How many lines from the label, always zero or positive. Direction lives
    #: in `method`, so a caller sorting on this cannot accidentally prefer up
    #: over down or the reverse.
    line_distance: int

    #: The gap between the two boxes in pixels, or None where either box is
    #: missing. MANHATTAN AND NOT EUCLIDEAN: horizontal gap plus vertical gap,
    #: each clamped at zero where the boxes overlap on that axis. It is an
    #: integer with no square root in it, which keeps this module's arithmetic
    #: whole - the same reason money here is paise and never a float.
    gap: int | None

    #: Which of `SEARCH_ORDER` found it.
    method: str

    #: Empty when accepted. Otherwise the plain reason, from `REJECTION_ORDER`.
    rejected: str

    #: 1-based position under the ranking rule stated on `candidates_for`.
    #: Accepted candidates always rank above refused ones.
    rank: int


# =============================================================================
# reading the words as labels and as values
# =============================================================================


@dataclass(frozen=True)
class _Site:
    """One place on the page where a label from the caller's family is printed."""

    line: int
    first: int
    last: int
    printed: str
    box: Box | None


@dataclass(frozen=True)
class _Span:
    """One run of words on one line that could be a printed figure."""

    line: int
    first: int
    last: int
    text: str
    box: Box | None


#: What a printed figure may be made of, once currency decoration is off. `#`
#: and `%` are in the set SO THAT THEIR DETECTORS CAN BE REACHED: leaving them
#: out would drop `#4417` and `18%` before anything looked at them, and a figure
#: silently never considered is exactly the outcome this module argues against.
#: They are considered, named and refused instead.
_VALUE_CHARACTERS: Final = frozenset("0123456789.,+-/#%")

#: A word that is nothing but a currency mark, sitting in front of its figure.
_JUST_A_CURRENCY_MARK: Final = re.compile(CURRENCY, re.IGNORECASE)

#: Punctuation at either end of a word, removed before it is compared with a
#: label. THE TOLERANCE IS THE SEPARATOR AND NEVER A LETTER, which is
#: `labels._SEPARATOR`'s rule and its measurement: the corpus engine reads
#: `SUPPLIER:` as `SUPPLIERS` eight times in twenty, and accepting a letter
#: there would read `SUPPLIERS OF FINE GOODS` as a supplier called `OF FINE
#: GOODS`. So `TOTAL:` and `(TOTAL)` match the label `TOTAL`, and `TOTALS`
#: matches nothing.
_PUNCTUATION_AT_EITHER_END: Final = re.compile(r"^[^0-9A-Za-z]+|[^0-9A-Za-z]+$")

#: A word which, printed immediately before a label, means the label is NOT that
#: label. Exactly one entry, and it is a MEASURED DEFECT rather than a guess:
#: `labels.amount_on` records `Sub  Total  278.61` on a bill whose real total is
#: 319.00. That posts ₹278.61 and loses exactly the ₹40.39 of input credit while
#: the bill still looks read. `SUB TOTAL` is one of the 14 measured lines here,
#: so this module walks into that same defect unless it is guarded.
#:
#: A SUBTOTAL IS STILL FOUND - as the NET field, where it belongs - because a
#: caller passing `labels.NET_LABELS` matches `SUBTOTAL` against the two printed
#: words joined tight. It is refused only as the amount payable.
MODIFIERS_BEFORE: Final[tuple[str, ...]] = ("SUB",)


def _bare(text: str) -> str:
    """A word with its end punctuation off and its case flattened."""
    return _PUNCTUATION_AT_EITHER_END.sub("", text).upper()


def _union(boxes: tuple[Box | None, ...]) -> Box | None:
    """The smallest box holding all of these, or None if any one is missing.

    ALL OR NOTHING. A union of the boxes that happen to exist would be a
    position for a phrase, part of which has no position - a measurement made of
    a measurement and a gap, reported as though it were only the first.
    """
    if not boxes or any(box is None for box in boxes):
        return None
    known = [box for box in boxes if box is not None]
    return Box(
        left=min(box.left for box in known),
        top=min(box.top for box in known),
        right=max(box.right for box in known),
        bottom=max(box.bottom for box in known),
    )


def _gap(one: Box, other: Box) -> int:
    """Manhattan gap between two boxes, zero on an axis where they overlap."""
    across = max(0, one.left - other.right, other.left - one.right)
    down = max(0, one.top - other.bottom, other.top - one.bottom)
    return across + down


def _shares_a_horizontal_band(one: Box, other: Box) -> bool:
    """Do these two boxes overlap vertically - are they on the same row?"""
    return min(one.bottom, other.bottom) > max(one.top, other.top)


def _shares_a_vertical_band(one: Box, other: Box) -> bool:
    """Do these two boxes overlap horizontally - are they in the same column?"""
    return min(one.right, other.right) > max(one.left, other.left)


def _vertical_gap(one: Box, other: Box) -> int:
    """Blank pixels between these two boxes down the page, zero if they overlap."""
    return max(0, one.top - other.bottom, other.top - one.bottom)


def _is_a_value_word(text: str) -> bool:
    """Could this single word be part of a printed figure?

    At least one digit is required, so a bare `-` or a stranded `%` is not a
    figure on its own. Currency decoration comes off first, so `₹1,234.56`
    arriving as ONE word is recognised - which is how a reading engine usually
    reports it.
    """
    undecorated = _CURRENCY_AT_EITHER_END.sub("", text.strip())
    if not undecorated:
        return False
    if not all(character in _VALUE_CHARACTERS for character in undecorated):
        return False
    return any(character.isdigit() for character in undecorated)


def _is_just_a_currency_mark(text: str) -> bool:
    """Is this whole word nothing but `Rs.`, `INR`, `₹` or the like?"""
    return _JUST_A_CURRENCY_MARK.fullmatch(text.strip()) is not None


def _spans_on(line_words: tuple[PageWord, ...], line: int) -> tuple[_Span, ...]:
    """Every run of words on this line that could be a printed figure.

    TWO SHAPES ONLY, AND THE SHORTNESS OF THAT LIST IS THE POINT. One word, or a
    currency mark followed by one word. A greedy run of adjacent numeric words
    would swallow `1,234.56    2,000.00` - two columns of one table row - into a
    single unparseable span and lose both figures, rather than offering both and
    letting the ranking and the review sentence do their job.
    """
    spans: list[_Span] = []
    index = 0
    while index < len(line_words):
        following = index + 1
        leads_a_figure = (
            _is_just_a_currency_mark(line_words[index].text)
            and following < len(line_words)
            and _is_a_value_word(line_words[following].text)
        )
        if leads_a_figure:
            spans.append(
                _Span(
                    line=line,
                    first=index,
                    last=following,
                    text=f"{line_words[index].text} {line_words[following].text}",
                    box=_union((line_words[index].box, line_words[following].box)),
                )
            )
            index += 2
            continue
        if _is_a_value_word(line_words[index].text):
            spans.append(
                _Span(
                    line=line,
                    first=index,
                    last=index,
                    text=line_words[index].text,
                    box=line_words[index].box,
                )
            )
        index += 1
    return tuple(spans)


def _sites_on(
    line_words: tuple[PageWord, ...], line: int, labels: tuple[str, ...]
) -> tuple[_Site, ...]:
    """Every place on this line where one of `labels` is printed.

    LONGEST RUN FIRST, then longest string, then alphabetically. That is
    `labels.amounts_for`'s `break` rule in a second dimension: `GRAND TOTAL` is
    reported once as itself and never again as `TOTAL` one word later, because
    the scan resumes past the whole run it matched. Without it, a caller
    counting candidates sees two labels where the page prints one.

    THE WORDS ARE ALSO JOINED TIGHT, and that is a measured need rather than a
    nicety: `SUB TOTAL` is one of the 14 measured lines, and `labels.NET_LABELS`
    spells that word `SUBTOTAL`. Without the tight join the measured line
    matches nothing at all.

    WORDS ARRIVE IN READING ORDER, WHICH IS THE CALLER'S CONTRACT. This function
    does not sort by box, because a caller with no geometry must behave the same
    as one with it, and every reading engine this package may use emits words
    left to right already.
    """
    ordered = sorted(
        labels, key=lambda label: (-len(label.split()), -len(label), label)
    )
    longest = max(len(label.split()) for label in ordered)
    sites: list[_Site] = []
    index = 0
    while index < len(line_words):
        hit: int | None = None
        for run in range(min(longest, len(line_words) - index), 0, -1):
            parts = [_bare(word.text) for word in line_words[index : index + run]]
            spaced = " ".join(parts)
            tight = "".join(parts)
            if any(one in (spaced, tight) for one in ordered):
                hit = index + run - 1
                break
        if hit is None:
            index += 1
            continue
        modified = index > 0 and _bare(line_words[index - 1].text) in MODIFIERS_BEFORE
        if not modified:
            matched = line_words[index : hit + 1]
            sites.append(
                _Site(
                    line=line,
                    first=index,
                    last=hit,
                    printed=" ".join(word.text for word in matched),
                    box=_union(tuple(word.box for word in matched)),
                )
            )
        index = hit + 1
    return tuple(sites)


def _placed(
    site: _Site, span: _Span, limits: Limits
) -> tuple[str, int, int | None] | None:
    """Which method reaches this span from this label, and how far away it is.

    None means the span is outside every bound, and outside is the ordinary
    answer - most of a page is.

    THE LINE METHODS ARE BOUNDED BY LINES. Nothing subtle: within
    `max_line_distance`, after the label on its own line or on a neighbouring
    one.

    THE GEOMETRIC METHODS RUN ONLY WHERE THE GEOMETRY CONTRADICTS THE LINE
    NUMBERING, and that condition is the whole reason they are safe. They exist
    for one case: a table cell the engine gave a line number nowhere near the
    label's while printing it right beside the label on the page. The
    contradiction is measurable rather than assumed - the numbering claims N
    lines lie between the two boxes, N lines of text stand at least N times the
    label's own height, so the boxes must be VERTICALLY CLOSER than that before
    the numbering is disbelieved.

    WITHOUT THAT TEST `below` IS A BACK DOOR AROUND `max_line_distance`, and it
    was one: caught by `tests/test_nearby.py` on the first run, where a figure
    two lines under a total - genuinely two lines under it, correctly numbered,
    already refused by the line limit - was taken anyway because it sat in the
    same column. A bound that a second code path walks round is not a bound.

    The pixel bound is then applied to EVERY method, geometric or not, wherever
    both boxes are known. A figure printed a page away that the caller happened
    to number as the next line is not next to anything.
    """
    lines_apart = abs(span.line - site.line)
    if span.line == site.line:
        if span.first <= site.last:
            return None
        method, distance = SAME_LINE, 0
    elif 0 < span.line - site.line <= limits.max_line_distance:
        method, distance = NEXT_LINE, lines_apart
    elif 0 < site.line - span.line <= limits.max_line_distance:
        method, distance = PREVIOUS_LINE, lines_apart
    elif site.box is None or span.box is None:
        return None
    else:
        implied = lines_apart * (site.box.bottom - site.box.top)
        if _vertical_gap(site.box, span.box) >= implied:
            return None
        if span.box.left >= site.box.right and _shares_a_horizontal_band(
            site.box, span.box
        ):
            method, distance = RIGHT_OF, lines_apart
        elif span.box.top >= site.box.bottom and _shares_a_vertical_band(
            site.box, span.box
        ):
            method, distance = BELOW, lines_apart
        else:
            return None
    if site.box is None or span.box is None:
        return method, distance, None
    gap = _gap(site.box, span.box)
    if gap > limits.max_gap:
        return None
    return method, distance, gap


def _order(candidate: Candidate) -> tuple[int, int, int, int, int, int, int, str]:
    """The total order candidates are ranked in. Every element is deterministic.

    In words, and this is the rule `candidates_for` promises:

        1. accepted before refused
        2. earlier method in `SEARCH_ORDER` first
        3. fewer lines away first
        4. a known gap before an unknown one, then the smaller gap
        5. further left, then further up
        6. the printed characters, so equal geometry still has one answer

    Steps 5 and 6 decide nothing about which figure is likelier and exist only
    so that two candidates can never swap places between runs. A ranking that is
    stable except under a tie is not a ranking any refusal can quote.
    """
    return (
        1 if candidate.rejected else 0,
        SEARCH_ORDER.index(candidate.method),
        candidate.line_distance,
        0 if candidate.gap is not None else 1,
        candidate.gap if candidate.gap is not None else 0,
        candidate.box.left if candidate.box is not None else 0,
        candidate.box.top if candidate.box is not None else 0,
        candidate.value_text,
    )


def _refuse_a_nested_label_family(labels: tuple[str, ...]) -> None:
    """The guard `labels.values_for` grew after a nested tuple cost 98 tests.

    Same mistake, same shape, same silence from the type checker: `labels` is
    annotated `tuple[str, ...]` and `(TOTAL_LABELS,)` is also a tuple, so the
    annotation is exactly what is wrong at the call site. Here the failure would
    be quieter still - a nested tuple never equals a joined word, so every
    document would simply find no label and read nothing, with no exception
    raised anywhere. A silent zero is worse than a loud one.

    The ignore is the evidence and not a workaround: pyright calls this check
    unnecessary BECAUSE it trusts the annotation.
    """
    for label in labels:
        if not isinstance(label, str):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise TypeError(
                f"candidates_for takes a family of labels, not one nested "
                f"inside another: got {label!r}. A caller spreading a label "
                f"constant that is already a tuple should pass it whole - "
                f"`candidates_for(words, labels=TOTAL_LABELS, ...)` - and "
                f"never `labels=(TOTAL_LABELS,)`."
            )


def candidates_for(
    words: tuple[PageWord, ...],
    *,
    field: str,
    labels: tuple[str, ...],
    limits: Limits,
) -> tuple[Candidate, ...]:
    """Every figure near a label from `labels`, ranked, with refusals named.

    IT PICKS NOTHING. It returns a tuple - possibly empty, possibly several -
    and a caller holding a tuple cannot post silently because it has no single
    value in its hand. Where two survivors disagree, `review_needed` says so in
    a sentence a person can read.

    RANKING RULE, in full, implemented by `_order`:

        1. accepted candidates before refused ones
        2. earlier method in `SEARCH_ORDER` - same line, next line, previous
           line, right of, below
        3. fewer lines from the label
        4. a known pixel gap before an unknown one, then the smaller gap
        5. further left, then further up the page
        6. the printed characters

    Steps 1 to 4 are the argument. Steps 5 and 6 exist so that two equally good
    candidates still come back in the same order every time - the determinism
    `tests/test_nearby.py` compares as objects AND as reprs.

    ONE SPAN IS ONE CANDIDATE. Where a page prints the same label twice and both
    printings reach the same figure, the better-placed reading is kept and the
    other dropped. A repeated label must not look like two competing answers -
    `labels.the_one` draws the same line between repetition and disagreement.

    `limits` HAS NO DEFAULT, for the reason `labels.Printing` has none: a bound
    that can be left unstated will be left unstated, and the search radius is
    the whole difference between reading a bill and reading its footer.

    NO PAGE-WIDE FALLBACK EXISTS AND NONE CAN BE ASKED FOR. There is no
    parameter that widens this to the whole page, because `adapter` already
    records what that costs - twenty invented totals - and a knob that can reach
    that state will eventually be turned.
    """
    _refuse_a_nested_label_family(labels)
    if not labels:
        raise ValueError(
            "candidates_for was given no labels to look for. An empty family "
            "would search a page for nothing and report nothing, which reads "
            "exactly like a document with no total printed on it."
        )
    by_line: dict[int, tuple[PageWord, ...]] = {}
    for word in words:
        by_line[word.line] = (*by_line.get(word.line, ()), word)
    text_of = {line: " ".join(w.text for w in ws) for line, ws in by_line.items()}

    best: dict[tuple[int, int, int], Candidate] = {}
    for label_line in sorted(by_line):
        for site in _sites_on(by_line[label_line], label_line, labels):
            for value_line in sorted(by_line):
                for span in _spans_on(by_line[value_line], value_line):
                    placed = _placed(site, span, limits)
                    if placed is None:
                        continue
                    method, line_distance, gap = placed
                    context = text_of[site.line]
                    if value_line != site.line:
                        context = f"{context} {text_of[value_line]}"
                    candidate = Candidate(
                        field=field,
                        label_text=site.printed,
                        value_text=span.text,
                        box=span.box,
                        line_distance=line_distance,
                        gap=gap,
                        method=method,
                        rejected=_why_refused(span.text, context),
                        rank=0,
                    )
                    key = (span.line, span.first, span.last)
                    standing = best.get(key)
                    if standing is None or _order(candidate) < _order(standing):
                        best[key] = candidate

    ranked = sorted(best.values(), key=_order)
    return tuple(replace(one, rank=place + 1) for place, one in enumerate(ranked))


def _clearly_stronger(winner: Candidate, runner_up: Candidate) -> bool:
    """Is the winner's position a better reason, or only a different one?

    Three ways, in the order the ranking already argues: a stronger method beats
    a weaker one, a nearer line beats a further one, and at the same method and
    the same line a gap must be smaller by at least `CLEARLY_CLOSER_GAP`.

    UNKNOWN GEOMETRY IS NEVER CLEARLY STRONGER. Two candidates the same number
    of lines away with no boxes are indistinguishable, and naming one of them
    the winner would be a coin toss that posts money - which is exactly what
    `labels.the_one` refuses to do when two readings disagree.
    """
    if SEARCH_ORDER.index(winner.method) < SEARCH_ORDER.index(runner_up.method):
        return True
    if winner.line_distance < runner_up.line_distance:
        return True
    if winner.gap is None or runner_up.gap is None:
        return False
    closer = runner_up.gap - winner.gap
    return closer >= CLEARLY_CLOSER_GAP


def review_needed(candidates: tuple[Candidate, ...]) -> str:
    """The sentence a person must read before this figure is used, or empty.

    WHY THIS IS A FUNCTION AND NOT A FLAG ON `Candidate`. Ambiguity is a
    property of the SET and not of any one member: the top candidate is no
    different in itself for something else on the page tying with it. A boolean
    on one object would be a claim that object cannot support, and the day a
    caller filtered the tuple the flag would start lying.

    THREE WAYS TO NEED NO REVIEW, and all three are real rather than convenient:

        fewer than two survivors   there is nothing to choose between
        the survivors AGREE        `labels.the_one`'s own rule - a continuation
                                   sheet repeats its header, so repetition is
                                   ordinary. The check IS that function, called,
                                   and not a second copy of its reasoning.
        one is clearly stronger    a better method, a nearer line, or a gap
                                   smaller by `CLEARLY_CLOSER_GAP`

    Anything else comes back as words. The caller is expected to show them, and
    a caller that ignores them is choosing between two figures on its own
    account - which is a decision, and this module has made sure it is a visible
    one.
    """
    accepted = sorted(
        (one for one in candidates if not one.rejected), key=lambda one: one.rank
    )
    if len(accepted) < 2:
        return ""
    field = accepted[0].field
    agreed, _refusal = the_one(tuple(one.value_text for one in accepted), field)
    if agreed is not None:
        return ""
    winner, runner_up = accepted[0], accepted[1]
    if _clearly_stronger(winner, runner_up):
        return ""
    return (
        f"two different figures sit equally close to '{winner.label_text}' and "
        f"nothing on this page says which one is the {field}: "
        f"'{winner.value_text}' and '{runner_up.value_text}'. "
        "Neither is used until a person says which."
    )
