"""What a bill printed under a label, and exactly where it printed it.

WHY THIS FILE EXISTS
--------------------
`textlayer.py` already turns labelled invoice text into fields and does it
well - MEASURED at 92/100 exact on the twenty corpus PDFs. On 2026-08-13 a
second caller appeared that needs the same answers from the same shape of text:
the free reading engine hands back the words on a photographed page, and
something has to say which of those words are the total, the tax, the date and
the supplier.

There were two ways to give it that. Write a second parser, or share the one
that already works. A second parser is the worse option and it is not close:
two label vocabularies drift, and the day one of them learns `AMOUNT PAYABLE`
and the other does not is the day the same bill reads differently depending on
whether it arrived as a PDF or as a photograph. So the vocabulary and the
matching move here, `textlayer.py` imports them back, and there is ONE answer to
"what is a total called".

WHAT THE SECOND CALLER NEEDED THAT THE FIRST DID NOT
-----------------------------------------------------
A position. `textlayer.py` only ever wanted the VALUE - the characters are the
answer and there is nothing further to look up. The reading engine's caller
wants to know WHICH WORDS produced that value, because each word carries the
engine's own confidence and the field's score is computed from them.

So every function here answers with the value AND the half-open character range
`[start, end)` it occupied on its line. That is the whole of the addition. The
matching itself is unchanged, which is the point: a range that came from a
different match than the value would be worse than no range at all.

AND THE SECOND THING, WHICH IS ONE CHARACTER WIDE
---------------------------------------------------
A text layer's bytes say `SUPPLIER:` exactly. A photograph's do not, and the
character an engine loses first is the separator. MEASURED with `tesseract` on
the twenty corpus PNGs, counting what it produced where the truth prints
`SUPPLIER:`

    `:` 5    `S` 8    `!` 2    `®` 2    `?` 1    `'` 1

so exact matching threw away three quarters of the suppliers the engine had
already located on the page. `Printing` is how a caller says which of those two
worlds it is in, and the tolerance it unlocks is the SEPARATOR ONLY - see that
class for what is deliberately not tolerated and what it costs.

WHY A RANGE AND NOT A LIST OF WORDS
------------------------------------
Because this module must not know what a word is. It is handed strings, it
reports where in those strings it found something, and the caller that built
the strings is the one that knows which of its own words sat at those offsets.
Teaching this file about words would tie the PDF rung to the reading engine's
data types for no gain to either.

NO NETWORK, NO CLOCK, NO FILESYSTEM, NO THIRD-PARTY IMPORT. Strings in,
positions out.

WHAT THIS FILE DOES NOT PROVE
------------------------------
That the vocabulary is complete. `TOTAL`, `GRAND TOTAL`, `AMOUNT PAYABLE` and
the rest are the names the corpus suppliers print. A bill using a name not on
the list has that field UNREAD and the person is asked. Widening the list is
safe; guessing at an unlabelled number is not, and that guess is exactly what
`adapter.TYPED_TEXT_MIME` records as having invented twenty totals.

That a value it locates is CORRECT. It reports what was printed under a label.
Whether those characters were read correctly off a page is the caller's problem
and, for the reading engine, the whole content of its confidence.

That a value it locates under a tolerated separator is even the RIGHT SHAPE.
`SUPPLIER? AQUANCED PROPULSION CENTRE UK LTO` comes back as `AQUANCED
PROPULSION CENTRE UK LTO`, which is a misread supplier, and it is handed over
misread. Mending it would be inventing data; refusing it hides a reading the
engine scored at 0.30 from the layer built to argue with exactly that.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Final

from accountant.extract.adapter import NOT_FOUND

# =============================================================================
# money
# =============================================================================

_HUNDRED: Final = Decimal(100)

#: Decoration a supplier prints around an amount. Removed before the number is
#: parsed; never used to decide what the number MEANS. This reader does no
#: currency conversion and would be wrong to - `£606.00` on an Indian purchase
#: bill is a fact about the bill, and converting it here would invent a rate.
#: The non-breaking space is written as an escape because a literal one is
#: invisible in a diff - `ingest/spend.py` says the same about the same
#: character, and it turns up inside published amounts often enough to matter.
_DECORATION: Final[tuple[str, ...]] = (
    "\u20b9",
    "\u00a3",
    "\u00a0",
    "RS.",
    "RS",
    "INR",
    "GBP",
    ",",
    " ",
)

#: What is left must be exactly a number. `Decimal` accepts `NaN`, `Infinity`
#: and `1e5`; the first two turn into an OverflowError inside the conversion
#: and the third into a number nobody printed. None of them is an amount a
#: supplier wrote, so the shape is checked before `Decimal` sees it.
_JUST_A_NUMBER: Final = re.compile(r"-?\d+(?:\.\d+)?")


def paise_or_none(text: str) -> int | None:
    """A printed rupee amount as integer paise, or None if it is not one.

    `"1,234.56"` is 123456. `"RS. 1,234.56"` and `"1,234.56 GBP"` are the same
    number wearing the decoration a supplier printed.

    REFUSES SUB-PAISE RATHER THAN ROUNDING. `"10.005"` is None, not 1000.
    `tallyio.paise_from_rupees` has always refused that string, and two money
    parsers in one repository disagreeing about it is how a reconciliation
    breaks three months later. This one returns None where that one raises,
    because an unreadable amount on a bill is a QUESTION for the person, not a
    500 in the web app.
    """
    cleaned = text.strip().upper()
    for junk in _DECORATION:
        cleaned = cleaned.replace(junk, "")
    if not _JUST_A_NUMBER.fullmatch(cleaned):
        return None
    try:
        rupees = Decimal(cleaned)
    except InvalidOperation:
        return None
    scaled = rupees * _HUNDRED
    paise = int(scaled)
    return paise if scaled == paise else None


# =============================================================================
# which of the two worlds these characters came out of
# =============================================================================


class Printing(StrEnum):
    """How the characters being matched came to exist.

    **There is no default and it is never inferred.** A caller must say, for
    the reason `cage/decision.Moment` gives about the same shape of argument: a
    tier that can be guessed at will be guessed at wrongly, and the wrong guess
    here is a PDF quietly reading like a photograph. The obvious alternative -
    work it out from the media type - puts the decision in whichever module
    happens to hold a MIME string, which is how two callers end up disagreeing
    about the same bill.

    THE TWO ARE NOT SYMMETRICAL, AND THE ASYMMETRY IS THE WHOLE POINT.
    `EXACT_CHARACTERS` has nothing to tolerate: the bytes say `SUPPLIER:` and a
    reader that accepted `SUPPLIER?` there would be reading something the
    document does not say. That tier measures 20/20 party, 20/20 total, 20/20
    tax and 14/20 date on the twenty corpus PDFs with ZERO wrong, and that is
    worth more than any number of photographs.
    """

    #: A text layer. The characters are IN THE FILE and the alternative
    #: readings of them number zero.
    EXACT_CHARACTERS = "exact_characters"

    #: A reading engine's guess at ink. Every character is an estimate,
    #: including the punctuation.
    READ_OFF_A_PHOTOGRAPH = "read_off_a_photograph"


#: What may sit between a label and its value, per printing.
#:
#: THE SEPARATOR IS TOLERATED AND THE LABEL WORD IS NOT, and that line is drawn
#: at "is this character a letter". MEASURED: the corpus engine reads
#: `SUPPLIER:` as `SUPPLIERS` eight times out of twenty - more often than any
#: mark - so accepting a letter would recover those eight AND read `SUPPLIERS OF
#: FINE GOODS` as a supplier called `OF FINE GOODS`. A plural and a mangled
#: colon are the same characters, nothing here can tell them apart, and the
#: eight readings are not worth a heading in somebody's ledger. Those eight
#: stay UNREAD, which is a question for a person rather than a wrong answer.
#:
#: A MARK RUN MUST BE FOLLOWED BY A SPACE. `SUPPLIER-MANAGED STOCK` and
#: `SUPPLIER/CUSTOMER DETAILS` are one word carrying a mark, not a label and a
#: value, and without this they read as a supplier called `MANAGED STOCK`. The
#: colon alternative keeps its own rule instead, so `SUPPLIER:MUMBAI` printed
#: tight still reads on both tiers.
#:
#: NOT APPLIED TO AMOUNTS, and that is a measurement rather than an oversight.
#: `_ONLY_AMOUNT` already accepts a bare space, which is what every amount label
#: on the corpus PNGs is followed by, so tolerance there buys nothing - while a
#: wider leading class would swallow the `-` of a credit note and post a refund
#: as a charge. An unread amount is a question; a sign flipped by a regex is
#: money moving the wrong way.
_SEPARATOR: Final[dict[Printing, str]] = {
    Printing.EXACT_CHARACTERS: r"\s*:\s*",
    Printing.READ_OFF_A_PHOTOGRAPH: r"\s*(?::|[^\w\s]+(?=\s))\s*",
}


# =============================================================================
# the vocabulary
# =============================================================================

#: A label counts when it starts the line or follows a run of spaces wide enough
#: to be a column gap. `SUPPLIER: CORNWALL COUNCIL        HSN/SAC: 998311` is one
#: line carrying two fields, and a reader anchored only to line starts reads the
#: second field into the first one's value.
_LABEL_AT: Final = r"(?:^|\s{2,})"

#: Where a value stops: the next column's label. Without this the party above
#: comes back as `CORNWALL COUNCIL        HSN/SAC: 998311`, which does not match
#: any vendor in history, so it is proposed as a NEW supplier. A wrong party is
#: worse than a missing one.
#:
#: A COLUMN GAP AND NOT A SINGLE SPACE, and that costs the reading engine's
#: caller something real: it reports no gaps, so the line it rebuilds is
#: single-spaced and a second label on the same line is not cut off. Narrowing
#: this to `\s+` would fix that and break the PDF rung, where a supplier
#: legitimately printed as `ACME LTD: MUMBAI` would lose everything after the
#: colon. The rung that has the evidence keeps the rule; the rung that does not
#: says so in its own docstring and carries the limitation.
_NEXT_LABEL: Final = re.compile(r"\s{2,}[A-Z][A-Z0-9 /]*:")

#: The symbols and codes a supplier prints beside an amount. PUBLIC, because
#: `textlayer.py` needs the same set to recognise a line item and a second copy
#: of it there is a second answer to what a currency looks like.
CURRENCY: Final = r"(?:RS\.?|INR|GBP|₹|£)"

#: `GST @ 18%` and `GST 18%` are the same label wearing its rate. The rate is
#: consumed, never read: a percentage is not the tax amount, and computing the
#: tax from it would be arithmetic on a number the bill already states.
_RATE: Final = r"(?:\s*@?\s*\d{1,2}(?:\.\d+)?\s*%)?"

#: After the label, the rest of the line must be the amount and nothing else.
#: This is what stops `TAX INVOICE 2026` being read as 2026 rupees of tax.
_ONLY_AMOUNT: Final = re.compile(
    rf"^[\s:|]*{CURRENCY}?\s*(-?[\d,]+(?:\.\d+)?)\s*{CURRENCY}?\s*\|?\s*$"
)

#: Names a supplier gives the amount payable. Longest first, so `TOTAL DUE` is
#: reported as itself rather than as `TOTAL`. `SUBTOTAL` is absent on purpose
#: and the match is anchored, so it can never be read as the total - a
#: substring match there leaves the bill short by the whole of its tax.
TOTAL_LABELS: Final[tuple[str, ...]] = (
    "GRAND TOTAL",
    "TOTAL DUE",
    "AMOUNT PAYABLE",
    "AMOUNT DUE",
    "TOTAL",
)

#: Tax printed as HALVES of one figure. An intra-state Indian bill prints CGST
#: and SGST, and reading one of them posts half the input credit - real money
#: lost, with a bill that still looks read.
TAX_PARTS: Final[tuple[str, ...]] = ("CGST", "SGST", "UTGST")

#: Tax printed as ONE figure. Kept apart from the halves because adding all of
#: them together double-counts, and a bill showing both is refused rather than
#: guessed at.
TAX_WHOLE: Final[tuple[str, ...]] = (
    "OUTPUT TAX",
    "INPUT TAX",
    "IGST",
    "GST",
    "TAX",
)

#: Labels the party is printed under. Vocabulary, not policy: an unmatched
#: label leaves the party unread and the person is asked who this bill is from.
PARTY_LABELS: Final[tuple[str, ...]] = (
    "SUPPLIER",
    "VENDOR",
    "BILLED BY",
    "SOLD BY",
)

#: The label a bill prints its date under.
DATE_LABEL: Final = "DATE"

#: What a bill calls the figure BEFORE tax. It is never the amount payable -
#: `TOTAL_LABELS` deliberately excludes it - and it is the third number the
#: conservation law `net_plus_tax_equals_gross` needs to be a real check rather
#: than one that passes by construction.
NET_LABELS: Final[tuple[str, ...]] = ("SUBTOTAL", "NET AMOUNT", "NET")


# =============================================================================
# what was printed, and where
# =============================================================================


@dataclass(frozen=True)
class Found:
    """One value printed under a label, and the characters it occupied.

    `line` indexes the tuple that was handed in, and `[start, end)` is a range
    within THAT line. Frozen, for the reason the nine types in `schema.py` are
    frozen: a located value that can be edited afterwards is not evidence about
    where anything was.
    """

    printed: str
    line: int
    start: int
    end: int


@dataclass(frozen=True)
class Amount:
    """One amount printed against a label, in whole paise, and where.

    A separate type from `Found` and not a `Found` with an optional number on
    it. `amounts_for` yields nothing for a label whose amount would not parse,
    so `paise` here is always a real figure - an optional field would make
    every caller re-check something this module already decided.
    """

    paise: int
    line: int
    start: int
    end: int


def _values_on(
    line: str, label: str, printing: Printing
) -> tuple[tuple[str, int, int], ...]:
    """Every value printed under `label` on this line, and where each sat.

    A blank value is dropped rather than returned. A field holding `"   "` is a
    silent blank, which is the single thing `ExtractedRecord` exists to make
    impossible.

    `printing` decides ONE thing: what may stand where the colon should be. The
    label word, the value and every rule about where a value stops are the same
    on both tiers, because a reader that answered differently about the same
    characters depending on how they arrived would be two readers again.
    """
    pattern = re.compile(rf"{_LABEL_AT}{re.escape(label)}{_SEPARATOR[printing]}(.*)$")
    located: list[tuple[str, int, int]] = []
    for match in pattern.finditer(line):
        cut = _NEXT_LABEL.split(match.group(1), maxsplit=1)[0]
        value = cut.strip()
        if not value:
            continue
        start = match.start(1) + (len(cut) - len(cut.lstrip()))
        located.append((value, start, start + len(value)))
    return tuple(located)


def values_for(
    lines: tuple[str, ...], labels: tuple[str, ...], *, printing: Printing
) -> tuple[Found, ...]:
    """Every value on the page printed under any of `labels`.

    `printing` IS KEYWORD-ONLY AND HAS NO DEFAULT. A positional third argument
    would be one `True` away from turning a PDF into a photograph at a call
    site nobody re-reads, and a default would mean a caller who never thought
    about the question still got an answer to it.

    LABEL BY LABEL, then line by line, which is the order the whole-document
    form this replaced produced: it ran one `re.MULTILINE` scan per label and
    the caller concatenated the results. Page order would read better and would
    also change which value `the_one` names first in a disagreement, and a
    refusal sentence that changes wording under a refactor is a refusal nobody
    can pin.

    A label is looked for once per LINE for the same reason: that pattern used
    `$` and a `.` that does not cross a newline, so a match never spanned two.
    """
    found: list[Found] = []
    for label in labels:
        for index, line in enumerate(lines):
            for printed, start, end in _values_on(line, label, printing):
                found.append(Found(printed=printed, line=index, start=start, end=end))
    return tuple(found)


def amount_on(line: str, label: str) -> tuple[int, int, int] | None:
    """The amount printed against `label` on this line, and where it sat.

    None covers three different situations on purpose - the label is not here,
    the rest of the line is not an amount, or the amount will not parse - and
    all three mean the same thing to the caller: this line states no figure for
    that label.
    """
    head = re.match(rf"{re.escape(label)}\b{_RATE}", line)
    if head is None:
        return None
    tail = _ONLY_AMOUNT.match(line[head.end() :])
    if tail is None:
        return None
    paise = paise_or_none(tail.group(1))
    if paise is None:
        return None
    return paise, head.end() + tail.start(1), head.end() + tail.end(1)


def amounts_for(lines: tuple[str, ...], labels: tuple[str, ...]) -> tuple[Amount, ...]:
    """Every amount on the page printed against any of `labels`.

    The `break` is load-bearing and `TOTAL_LABELS` is why: `GRAND TOTAL` also
    starts with no label on the list, but a line reading `TOTAL DUE 500.00`
    must be counted once, under the longest label that matched, and not again
    under `TOTAL`.
    """
    found: list[Amount] = []
    for index, line in enumerate(lines):
        for label in labels:
            located = amount_on(line, label)
            if located is not None:
                paise, start, end = located
                found.append(Amount(paise=paise, line=index, start=start, end=end))
                break
    return tuple(found)


def the_one[T](values: tuple[T, ...], what: str) -> tuple[T | None, str]:
    """One value that every printing of it agreed on, or None and the reason.

    A continuation sheet repeats the header, so REPETITION is ordinary and is
    not refused. DISAGREEMENT is refused: one of the two is wrong, nothing here
    can say which, and picking the first is a coin toss that posts money.
    """
    if not values:
        return None, f"{NOT_FOUND}: nothing on this document is labelled as {what}"
    distinct = list(dict.fromkeys(values))
    if len(distinct) > 1:
        return None, (
            f"{NOT_FOUND}: this document states {what} more than once and the "
            f"statements disagree ({', '.join(str(v) for v in distinct)}), so "
            "nothing is read from it"
        )
    return distinct[0], ""
