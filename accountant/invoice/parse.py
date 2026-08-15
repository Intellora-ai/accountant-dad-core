"""Characters in, fields out. No clock, no network, no filesystem, no engine.

WHAT THIS FILE IS
-----------------
The layer measured to be missing. Reading works - 82 of 106 JPGs in `data/`
return text, median 107.5 characters, tesseract 5.5.3 - and then nothing turned
those characters into a supplier, an invoice number, a tax split or a line.

Everything here is a named pattern or a piece of arithmetic. There is no model,
no learned weight and no guess. Two runs over the same string produce the same
fields, in the same order, on a machine that has never seen an invoice, and
`tests/test_invoice_parse.py` asserts exactly that by running it twice.

WHAT IT REUSES RATHER THAN REBUILDS, AND WHY EACH ONE
------------------------------------------------------
    labels.paise_or_none   the ONE printed-rupees-to-paise parser. It already
                           knows about `1,23,456.00`, `Rs.`, `₹` and the Indian
                           `/-` suffix, and it refuses sub-paise rather than
                           rounding. A second one here would disagree with it
                           about `10.005` and a reconciliation would break
                           three months later.
    labels.values_for      the ONE label matcher, including the separator
                           tolerance a photograph needs. New VOCABULARY is
                           added here; no new MATCHING is.
    labels.amounts_for     the same, for a figure printed against a label,
                           with the line-start anchor that stops
                           `Sub  Total  278.61` being read as the total.
    place_of_supply        the ONE GSTIN shape. `gstin_state_code` returns None
                           for anything that is not one, so asking it is the
                           same question as asking whether the shape holds -
                           and there is no second regex to drift.
    hsn_sac.normalise      the ONE tariff-code reader. It never guesses a
                           nearby code, which is owner decision Q2 = C.
    cage.confidence        the ONE field score, `min(word)/100` with format and
                           consistency as hard multipliers.

WHAT IT DOES NOT DO
--------------------
IT NEVER REPAIRS A VALUE. A figure that will not parse is reported unread with
the characters that failed attached. Mending it would be inventing data, which
is the failure this whole repository is built against.

IT NEVER GUESSES WHICH PARTY A GSTIN BELONGS TO. Two GSTINs on a page with
nothing saying which is the supplier's is a coin toss that decides whose input
credit this is, so both stay unassigned and a person is asked. See
`gstins_on`.

IT NEVER CONVERTS AN AMOUNT IN WORDS INTO A FIGURE. "Rupees One Lakh Twenty
Three Thousand Only" is recorded verbatim as evidence for a person. Converting
it would be a second money parser built on a word table nobody has verified.

IT NEVER READS A DAY/MONTH ORDER THE DOCUMENT DID NOT STATE. See `_date_from`.

WHAT IT CANNOT DO, SAID SO NOBODY RELIES ON IT
-----------------------------------------------
NO ACCURACY IS CLAIMED FOR ANY OF IT. There is one GSTIN in the whole of
`data/` and it sits on a tribunal order, so GST-specific extraction cannot be
accuracy-validated in this repository at all. The fixtures prove PARSER
BEHAVIOUR. `docs/INVOICE_EXTRACTION_FRAMEWORK.md` carries the full statement
and the fixture contract that would change it.
"""

from __future__ import annotations

import datetime
import re
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Final

from accountant.cage.confidence import field_confidence
from accountant.extract.labels import (
    Amount,
    Found,
    Printing,
    amounts_for,
    paise_or_none,
    the_one,
    values_for,
)
from accountant.invoice.fields import Method, ReadField, Where, read_as, unread
from accountant.rules.hsn_sac import Code, normalise
from accountant.rules.place_of_supply import gstin_state_code

# =============================================================================
# what a reader handed us
# =============================================================================

#: Tesseract's reporting range, taken from `cage/confidence.py` rather than
#: written again. `-1` is its "no text here" marker and is refused below.
_WORST_WORD: Final = 0
_BEST_WORD: Final = 100

#: What sits between two words when a line is rebuilt from a word list. One
#: space, matching `extract/pagereader.py::BETWEEN_WORDS`, because a caller
#: holding that module's output must get the same offsets from this one.
BETWEEN_WORDS: Final = " "


@dataclass(frozen=True)
class Word:
    """One word a reader returned, and how sure it said it was.

    Two fields and no geometry. Nothing here decides anything from where a word
    was on the PAGE - only from where its characters sit on its LINE - and
    carrying the pixel coordinates would be the first line of something that
    did, which is reading and belongs in `accountant/extract/`.
    """

    text: str
    confidence: int

    def __post_init__(self) -> None:
        if type(self.confidence) is not int:
            raise TypeError(
                f"a word confidence is a whole number from {_WORST_WORD} to "
                f"{_BEST_WORD}, not {type(self.confidence).__name__}."
            )
        if not _WORST_WORD <= self.confidence <= _BEST_WORD:
            raise ValueError(
                f"a word confidence of {self.confidence} is outside "
                f"{_WORST_WORD}-{_BEST_WORD}. Tesseract reports -1 for 'no text "
                "here', which is a marker and not a score; a caller holding one "
                "must drop that row rather than hand it on as a number."
            )


@dataclass(frozen=True)
class Reading:
    """The characters one reader returned about one document, line by line.

    TWO WAYS IN, AND THEY ARE NOT SYMMETRICAL.

    `from_text` is for a tier that has characters and no per-word score - a PDF
    text layer, or a person's typed line. The caller STATES the confidence,
    because that tier's certainty is a fact about the tier and not about any
    word. The line is kept verbatim, column gaps and all, and those gaps are
    load-bearing: `labels.py` uses a run of two or more spaces to know where one
    field's value stops and the next column's label starts.

    `from_words` is for a tier that reports a score per word. The line is
    REBUILT by joining with a single space, which is what the engine's own
    caller does, and the cost is stated rather than hidden: a rebuilt line has
    no column gaps, so a second field printed on the same line is not cut off
    and may be read into the first one's value.
    """

    lines: tuple[str, ...]
    words: tuple[tuple[Word, ...], ...]
    source: str
    stated_confidence: float | None

    def __post_init__(self) -> None:
        if not self.source.strip():
            raise ValueError(
                "a reading must say which reader produced it. A value with no "
                "provenance cannot be explained to the person it affects."
            )
        if self.words and len(self.words) != len(self.lines):
            raise ValueError(
                f"this reading has {len(self.lines)} lines and "
                f"{len(self.words)} lines of words. They index each other, so "
                "a mismatch means a field would be scored from another line."
            )
        if (self.stated_confidence is None) == (not self.words):
            raise ValueError(
                "a reading carries either per-word scores or one stated "
                "confidence, and exactly one of the two. Both is two answers to "
                "how sure we are; neither is no answer at all."
            )
        if self.stated_confidence is not None and not (
            0.0 <= self.stated_confidence <= 1.0
        ):
            raise ValueError(
                f"a stated confidence of {self.stated_confidence} is outside "
                "0.0-1.0. A score above 1.0 would clear every band by accident."
            )

    @classmethod
    def from_text(cls, text: str, *, source: str, confidence: float) -> Reading:
        """A reading with no per-word scores. The caller states the certainty."""
        return cls(
            lines=tuple(text.splitlines()),
            words=(),
            source=source,
            stated_confidence=confidence,
        )

    @classmethod
    def from_words(cls, lines: Sequence[Sequence[Word]], *, source: str) -> Reading:
        """A reading rebuilt from a word list. One space between words."""
        rows = tuple(tuple(line) for line in lines)
        return cls(
            lines=tuple(BETWEEN_WORDS.join(w.text for w in row) for row in rows),
            words=rows,
            source=source,
            stated_confidence=None,
        )

    @property
    def text(self) -> str:
        """The whole document as one string, for anything that wants it whole."""
        return "\n".join(self.lines)

    def words_under(self, where: Where) -> tuple[Word, ...]:
        """The words whose characters overlap `[start, end)` on that line.

        A half-open overlap: a word ending exactly where the range starts is
        not under it. Without that, `TOTAL 500.00` scores the total using the
        confidence of the word `TOTAL`, which is not what was read.

        Returns `()` when this reading has no per-word scores, which the caller
        reads as "ask the stated confidence instead" and never as "no words
        were legible" - those are different facts and `score_of` keeps them apart.
        """
        if not self.words:
            return ()
        found: list[Word] = []
        at = 0
        for word in self.words[where.line]:
            end = at + len(word.text)
            if at < where.end and end > where.start:
                found.append(word)
            at = end + len(BETWEEN_WORDS)
        return tuple(found)


# =============================================================================
# scoring one field
# =============================================================================


def score_of(reading: Reading, where: Where | None, *, format_valid: bool) -> float:
    """How sure we are about the value at `where`, `0.0` to `1.0`.

    Three cases, and they are three because collapsing any two loses a fact:

        the tier states one score      that score, times the format multiplier
        the tier scores words          `min(word)/100`, times the same
        a value with no location       the tier's own answer, unweakened

    The third is for a value worked out from other fields rather than found on
    the page - there is nothing on the page to score, and inventing a penalty
    for that would be a weight nobody measured.

    `format_valid` is a HARD MULTIPLIER and not a penalty, exactly as
    `cage/confidence.py` argues: a date that will not parse is not a
    low-confidence date, it is not a date. A reader can be entirely certain it
    read `12/34/5678`, and that certainty is about ink.
    """
    if not format_valid:
        return 0.0
    if reading.stated_confidence is not None:
        return reading.stated_confidence
    if where is None:
        return 0.0
    scores = tuple(word.confidence for word in reading.words_under(where))
    return field_confidence(scores or None, format_valid=True, consistent=True)


def _where(located: Found | Amount) -> Where:
    """The half-open range a located value occupied, as this package holds it."""
    return Where(line=located.line, start=located.start, end=located.end)


# =============================================================================
# the vocabulary this package adds
# =============================================================================
#
# NEW WORDS, NOT A NEW MATCHER. `labels.py` owns how a label is recognised and
# what may stand where the colon should be. These are the names an Indian GST
# invoice prints that the four fields `labels.py` was written for did not need.
#
# LONGEST FIRST inside each tuple, for the reason `labels.TOTAL_LABELS` is
# ordered that way: `amounts_for` breaks on the first label that matches a line,
# so `TOTAL DUE` has to be tried before `TOTAL` or the longer name is never
# reported as itself.

#: `BILL NO` AND `BILL NUMBER` ARE DELIBERATELY ABSENT, and this is measured
#: rather than argued. `values_for` anchors a label to the start of a line OR to
#: a run of spaces, so on a line reading
#:
#:     E-Way Bill No: 481920375566
#:
#: the characters `Bill No` follow a space and match. With `BILL NO` on this
#: list, `labels.the_one` then sees an invoice number of `STM-4471` and one of
#: `481920375566`, refuses the disagreement, and the bill's number reads as
#: NOTHING - on every e-invoice, which is every invoice above the turnover
#: threshold. That was caught by `tests/invoice_documents.py::WITH_DISCOUNT`
#: the first time this file was run.
#:
#: The refusal was the CORRECT behaviour of the matcher and the wrong
#: vocabulary here, so the vocabulary is what changed. A bill that prints only
#: `Bill No:` has its number UNREAD and a person is asked, which is the price
#: and it is the cheaper one.
INVOICE_NUMBER_LABELS: Final[tuple[str, ...]] = (
    "TAX INVOICE NO",
    "INVOICE NUMBER",
    "INVOICE NO",
    "DOCUMENT NO",
    "INVOICE #",
    "INV NO",
)

PO_NUMBER_LABELS: Final[tuple[str, ...]] = (
    "PURCHASE ORDER NO",
    "PURCHASE ORDER",
    "ORDER NUMBER",
    "PO NUMBER",
    "ORDER NO",
    "PO NO",
)

IRN_LABELS: Final[tuple[str, ...]] = ("INVOICE REFERENCE NUMBER", "IRN")

EWAY_BILL_LABELS: Final[tuple[str, ...]] = (
    "E-WAY BILL NUMBER",
    "EWAY BILL NUMBER",
    "E-WAY BILL NO",
    "EWAY BILL NO",
    "E-WAY BILL",
    "EWB NO",
)

PLACE_OF_SUPPLY_LABELS: Final[tuple[str, ...]] = ("PLACE OF SUPPLY",)

GSTIN_LABELS: Final[tuple[str, ...]] = (
    "GST REGISTRATION NO",
    "GSTIN/UIN",
    "GST NUMBER",
    "GST NO",
    "GSTIN",
)

AMOUNT_IN_WORDS_LABELS: Final[tuple[str, ...]] = (
    "AMOUNT CHARGEABLE IN WORDS",
    "TOTAL IN WORDS",
    "AMOUNT IN WORDS",
    "RUPEES IN WORDS",
    "IN WORDS",
)

ROUND_OFF_LABELS: Final[tuple[str, ...]] = (
    "ROUNDING OFF",
    "ROUND OFF",
    "ROUND-OFF",
    "ROUNDED OFF",
    "ROUNDING",
    "R/OFF",
)

CGST_LABELS: Final[tuple[str, ...]] = ("CGST",)
SGST_LABELS: Final[tuple[str, ...]] = ("SGST", "UTGST")
IGST_LABELS: Final[tuple[str, ...]] = ("IGST",)
CESS_LABELS: Final[tuple[str, ...]] = ("COMPENSATION CESS", "CESS")
TOTAL_TAX_LABELS: Final[tuple[str, ...]] = ("TOTAL TAX", "TAX AMOUNT", "TOTAL GST")

#: Headings that say the lines under them are about the person SELLING.
SUPPLIER_SECTION: Final[tuple[str, ...]] = (
    "SUPPLIER",
    "BILLED BY",
    "SOLD BY",
    "SELLER",
    "VENDOR",
    "FROM",
)

#: Headings that say the lines under them are about the person BUYING.
#: `SHIP TO` is here rather than in a third section on purpose: a bill-to and a
#: ship-to can differ, and nothing in this package is entitled to decide which
#: of them owns a registration number. Both mean "not the supplier", which is
#: the only distinction this file makes.
BUYER_SECTION: Final[tuple[str, ...]] = (
    "BILLED TO",
    "CONSIGNEE",
    "CUSTOMER",
    "BILL TO",
    "SHIP TO",
    "SOLD TO",
    "BUYER",
)


# =============================================================================
# shapes
# =============================================================================

#: Fifteen characters with no separators. The candidate finder ONLY - whether a
#: candidate IS a GSTIN is decided by `place_of_supply.gstin_state_code`, which
#: is the one shape check in this repository. Deliberately looser than that
#: check, because a finder that is stricter than the validator can never be
#: caught being wrong: it would simply never offer the string that fails.
_GSTIN_CANDIDATE: Final = re.compile(r"\b[0-9A-Z]{15}\b")

#: An IRN is 64 hexadecimal characters - a SHA-256 digest of the invoice, which
#: the invoice registration portal returns. SHAPE ONLY. Nothing here can verify
#: that the digest is of this document, because that needs the portal's own
#: canonical form of the invoice, which this repository does not have.
IRN_SHAPE: Final = re.compile(r"^[0-9a-fA-F]{64}$")

#: An e-way bill number is twelve digits. SHAPE ONLY, and there is no check
#: digit claimed - see the GSTIN note in
#: `docs/INVOICE_EXTRACTION_FRAMEWORK.md` for why an unverified checksum is
#: worse than none.
EWAY_BILL_SHAPE: Final = re.compile(r"^\d{12}$")

#: `18%`, `18 %`, `18.00%`, `9`. Two decimal places at most, because a rate is
#: held in basis points and a third would not be representable.
_RATE_SHAPE: Final = re.compile(r"^(\d{1,3}(?:\.\d{1,2})?)\s*%?$")

#: A quantity, held to three decimal places. `2`, `2.5`, `0.125`.
_QUANTITY_SHAPE: Final = re.compile(r"^-?\d+(?:\.\d{1,3})?$")

#: What a bill writes a date as. Taken apart rather than handed to a parser, so
#: the day/month question below is answered by arithmetic and never by a locale.
_ISO_DATE: Final = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$")
_NUMERIC_DATE: Final = re.compile(r"^(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})$")
_LONG_DATE: Final = re.compile(r"^(\d{1,2})[ -]([A-Z]{3})[A-Z]*[ -](\d{4})$")

_MONTH_NAMES: Final[tuple[str, ...]] = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)

#: Derived, never written twice: two lists that must agree are one list plus a
#: bug waiting for somebody to edit one of them.
_MONTHS: Final[tuple[str, ...]] = tuple(name[:3].upper() for name in _MONTH_NAMES)

#: A hundred basis points to the per cent, and a thousand milli-units to the
#: unit. Named rather than written as bare literals so a reader of the
#: arithmetic below can see which scale each figure is on.
_BASIS_POINTS_PER_PERCENT: Final = Decimal(100)
_MILLI_PER_UNIT: Final = Decimal(1000)


def _decimal(text: str) -> Decimal | None:
    """A `Decimal`, or None because those characters are not a plain number.

    `Decimal` accepts `NaN`, `Infinity` and `1e5`. None of them is anything a
    supplier printed, so the shape is checked before `Decimal` sees the string -
    the same order `labels.paise_or_none` uses and for the same reason.
    """
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def gst_rate_basis_points(printed: str) -> int | None:
    """`"18%"` -> `1800`. None when those characters are not a rate.

    BASIS POINTS AND NOT A FLOAT. A GST rate of 2.5 per cent is real - it is
    half of the 5 per cent slab - and `0.025` as a float multiplied by an amount
    is how a paisa goes missing. `gst_rates.RateLookup` holds rates in basis
    points for exactly this reason; this matches it rather than inventing a
    second scale.
    """
    match = _RATE_SHAPE.match(printed.strip())
    if match is None:
        return None
    number = _decimal(match.group(1))
    if number is None:
        return None
    scaled = number * _BASIS_POINTS_PER_PERCENT
    points = int(scaled)
    return points if scaled == points else None


def quantity_milli(printed: str) -> int | None:
    """`"2.5"` -> `2500`, in thousandths. None when it is not a quantity.

    WHOLE THOUSANDTHS OR NOTHING, never a rounded one. A quantity with four
    decimal places is refused rather than trimmed, because the trim would show
    up later as a line that does not multiply out and nobody would know why.
    """
    text = printed.strip()
    if not _QUANTITY_SHAPE.match(text):
        return None
    number = _decimal(text)
    if number is None:
        return None
    scaled = number * _MILLI_PER_UNIT
    milli = int(scaled)
    return milli if scaled == milli else None


def valid_gstin(printed: str) -> str | None:
    """The GSTIN these characters are, or None. SHAPE ONLY - NO CHECKSUM.

    THE CHECKSUM IS NOT IMPLEMENTED and that is a decision, not an omission.
    `accountant/rules/place_of_supply.py` already records why: the algorithm is
    not in any document this repository retrieved, and an unverified
    implementation would reject real registrations on arithmetic nobody checked.
    A shape check can only ever REJECT, so being conservative costs nothing.

    Asks `gstin_state_code` rather than matching a pattern here. That function
    returns None for anything that is not shaped like a GSTIN, so asking it is
    the same question - and there is then one shape in this repository instead
    of two that agree today.
    """
    candidate = printed.strip().upper()
    return candidate if gstin_state_code(candidate) is not None else None


# =============================================================================
# dates
# =============================================================================


def _real_date(year: int, month: int, day: int) -> datetime.date | None:
    """A date that exists. The 34th of a month is a misread 04th, not a date."""
    try:
        return datetime.date(year, month, day)
    except ValueError:
        return None


def date_from(printed: str) -> datetime.date | None:
    """The date these characters state, or None because they state none.

    `2026-09-21` and `28 APR 2026` NAME their parts. `26/02/2026` does not, and
    is read only when ARITHMETIC settles the order: 26 cannot be a month, so
    the day is first and no convention was assumed. `04/05/2026` could be the
    4th of May or the 5th of April, nothing on the page says which, and picking
    one is how a return gets filed in the wrong month - so it is refused.

    THIS IS THE SECOND IMPLEMENTATION OF THAT RULE IN THIS REPOSITORY, and that
    is worth saying out loud rather than leaving for somebody to discover.
    `extract/textlayer.py::_ordered_date` holds the first. It is private, and
    its module is a live reader this package is not permitted to edit, so the
    rule is restated here and pinned by a table of exact cases in
    `tests/test_invoice_parse.py`. The right fix is to move that function into a
    module both can import; until somebody does, the table is what catches drift.
    """
    text = printed.strip().upper()

    iso = _ISO_DATE.match(text)
    if iso is not None:
        return _real_date(int(iso[1]), int(iso[2]), int(iso[3]))

    spelled = _LONG_DATE.match(text)
    if spelled is not None and spelled[2] in _MONTHS:
        return _real_date(
            int(spelled[3]), _MONTHS.index(spelled[2]) + 1, int(spelled[1])
        )

    numeric = _NUMERIC_DATE.match(text)
    if numeric is None:
        return None
    first, second, year = int(numeric[1]), int(numeric[2]), int(numeric[3])
    if first == 0 or second == 0:
        # Zero is neither a month nor a day, so neither reading exists. This is
        # the impossible-date refusal and not the ambiguity one.
        return _real_date(year, second, first)
    if first == second or (first > 12 >= second):
        return _real_date(year, second, first)
    if second > 12 >= first:
        return _real_date(year, first, second)
    # Either both are above twelve - no reading exists - or both are at or
    # below it, which is two readings and no way to choose. Both refuse.
    return None


# =============================================================================
# reading one labelled value
# =============================================================================


def value_under(
    reading: Reading,
    labels: tuple[str, ...],
    what: str,
    *,
    printing: Printing,
) -> ReadField:
    """The single value printed under any of `labels`, or an unread field.

    Uses `labels.the_one`, so a header repeated on a continuation sheet is
    ordinary and two DIFFERENT values under the same label is a refusal. One of
    the two is wrong, nothing here can say which, and picking the first is a
    coin toss that ends up in somebody's books.
    """
    found = values_for(reading.lines, labels, printing=printing)
    chosen, _why = the_one(tuple(one.printed for one in found), what)
    if chosen is None:
        return unread(reading.source)
    located = next(one for one in found if one.printed == chosen)
    where = _where(located)
    return read_as(
        chosen,
        confidence=score_of(reading, where, format_valid=True),
        source=reading.source,
        method=Method.UNDER_A_LABEL,
        printed=chosen,
        where=where,
    )


def amount_under(reading: Reading, labels: tuple[str, ...], what: str) -> ReadField:
    """The single figure printed against any of `labels`, in whole paise."""
    found = amounts_for(reading.lines, labels)
    chosen, _why = the_one(tuple(one.paise for one in found), what)
    if chosen is None:
        return unread(reading.source)
    located = next(one for one in found if one.paise == chosen)
    where = _where(located)
    return read_as(
        chosen,
        confidence=score_of(reading, where, format_valid=True),
        source=reading.source,
        method=Method.UNDER_A_LABEL,
        printed=reading.lines[located.line][located.start : located.end],
        where=where,
    )


def converted(base: ReadField, value: object, reading: Reading) -> ReadField:
    """`base`'s characters, converted - or unread with the conversion's verdict.

    The conversion result is a HARD MULTIPLIER on the score, never a penalty:
    a supplier number that will not parse as a date is not a low-confidence
    date. `cage/confidence.py` makes the same argument about the same shape.
    """
    if not base.read:
        return base
    if value is None:
        return unread(reading.source)
    return read_as(
        value,
        confidence=score_of(reading, base.where, format_valid=True),
        source=reading.source,
        method=base.method,
        printed=base.printed,
        where=base.where,
    )


# =============================================================================
# the two parties, and which registration number is whose
# =============================================================================


class Side(StrEnum):
    """Which party a line of the document is about."""

    SUPPLIER = "supplier"
    BUYER = "buyer"
    UNSAID = "unsaid"


@dataclass(frozen=True)
class FoundGstin:
    """One registration number on the page, and whose section it sat in."""

    gstin: str
    side: Side
    where: Where


def _section_of(lines: tuple[str, ...]) -> tuple[Side, ...]:
    """Which party each line is about, carried down from the last heading.

    A heading applies to its own line and every line after it until the next
    heading. `Side.UNSAID` covers everything above the first heading, which on
    a real invoice is the letterhead - and a letterhead IS the supplier, on
    almost every bill ever printed. This does not say so, because "almost every"
    is the shape of assumption that puts a buyer's credit on a supplier's
    ledger, and the cost of not saying it is one question on a screen.
    """
    sides: list[Side] = []
    current = Side.UNSAID
    for line in lines:
        upper = line.upper()
        if any(name in upper for name in SUPPLIER_SECTION):
            current = Side.SUPPLIER
        elif any(name in upper for name in BUYER_SECTION):
            current = Side.BUYER
        sides.append(current)
    return tuple(sides)


def gstins_on(reading: Reading) -> tuple[FoundGstin, ...]:
    """Every registration number on the page, in reading order, with its side.

    Found by SHAPE across the whole page rather than only under the label,
    because the commonest printing of a supplier's GSTIN on an Indian invoice
    is inside the letterhead block with no label at all. `Method.BY_SHAPE`
    records that weakness on the field rather than hiding it.
    """
    sides = _section_of(reading.lines)
    found: list[FoundGstin] = []
    seen: set[str] = set()
    for index, line in enumerate(reading.lines):
        for match in _GSTIN_CANDIDATE.finditer(line.upper()):
            gstin = valid_gstin(match.group(0))
            if gstin is None or gstin in seen:
                continue
            seen.add(gstin)
            found.append(
                FoundGstin(
                    gstin=gstin,
                    side=sides[index],
                    where=Where(line=index, start=match.start(), end=match.end()),
                )
            )
    return tuple(found)


def gstin_for(reading: Reading, found: tuple[FoundGstin, ...], side: Side) -> ReadField:
    """The one registration number in that party's section, or unread.

    UNREAD RATHER THAN A GUESS when nothing on the page assigns it. A page with
    one GSTIN and no `Bill To` heading is the common shape and the tempting one:
    the number is almost always the supplier's. Almost always is not a fact
    about THIS bill, and the wrong answer puts somebody else's input credit on a
    supplier ledger where nothing downstream would ever question it.

    Two in the same section is also unread. One of them is wrong and nothing
    here can say which - the same rule `labels.the_one` applies everywhere else.
    """
    mine = [one for one in found if one.side is side]
    if len(mine) != 1:
        return unread(reading.source)
    only = mine[0]
    return read_as(
        only.gstin,
        confidence=score_of(reading, only.where, format_valid=True),
        source=reading.source,
        method=Method.BY_SHAPE,
        printed=only.gstin,
        where=only.where,
    )


# =============================================================================
# line items
# =============================================================================


class Column(StrEnum):
    """A column an invoice table can carry, named for what it holds."""

    DESCRIPTION = "description"
    HSN_SAC = "hsn_sac"
    QUANTITY = "quantity"
    UNIT = "unit"
    RATE = "rate"
    DISCOUNT = "discount"
    TAXABLE = "taxable"
    GST_RATE = "gst_rate"
    CGST = "cgst"
    SGST = "sgst"
    IGST = "igst"
    CESS = "cess"
    LINE_TOTAL = "line_total"


#: What a supplier calls each column. LONGEST FIRST within the tuple that is
#: scanned, for the same reason the amount labels are: `TAXABLE VALUE` has to be
#: tried before `TAXABLE`, or the longer heading is never reported as itself.
COLUMN_NAMES: Final[tuple[tuple[str, Column], ...]] = (
    ("DESCRIPTION OF GOODS", Column.DESCRIPTION),
    ("PARTICULARS", Column.DESCRIPTION),
    ("DESCRIPTION", Column.DESCRIPTION),
    ("ITEM", Column.DESCRIPTION),
    ("HSN/SAC", Column.HSN_SAC),
    ("HSN CODE", Column.HSN_SAC),
    ("SAC", Column.HSN_SAC),
    ("HSN", Column.HSN_SAC),
    ("QUANTITY", Column.QUANTITY),
    ("QTY", Column.QUANTITY),
    ("UOM", Column.UNIT),
    ("UNIT", Column.UNIT),
    ("UNIT PRICE", Column.RATE),
    ("RATE", Column.RATE),
    ("DISCOUNT", Column.DISCOUNT),
    ("DISC", Column.DISCOUNT),
    ("TAXABLE VALUE", Column.TAXABLE),
    ("TAXABLE", Column.TAXABLE),
    ("GST RATE", Column.GST_RATE),
    ("TAX RATE", Column.GST_RATE),
    ("CGST", Column.CGST),
    ("SGST", Column.SGST),
    ("UTGST", Column.SGST),
    ("IGST", Column.IGST),
    ("CESS", Column.CESS),
    ("LINE TOTAL", Column.LINE_TOTAL),
    ("AMOUNT", Column.LINE_TOTAL),
    ("TOTAL", Column.LINE_TOTAL),
)

#: How many recognised headings a line needs before it is treated as the table
#: header. Two would match `TOTAL` and `AMOUNT` on a summary line at the foot of
#: the bill and read every line below it as an item.
#:
#: MEASURED ON NOTHING. This is a shape argument, not a corpus number, and it is
#: written here so nobody quotes it as one. `docs/INVOICE_EXTRACTION_FRAMEWORK.md`
#: says what it would take to measure it.
ENOUGH_HEADINGS: Final = 3


@dataclass(frozen=True)
class Header:
    """The table header this document printed, and the order of its columns."""

    line: int
    columns: tuple[Column, ...]


def find_header(lines: tuple[str, ...]) -> Header | None:
    """The line that names the table's columns, or None because none does.

    NONE MEANS NO LINE ITEMS ARE READ, and that is deliberate. Without a header
    the only way to know that the third number on a row is the rate is to
    assume a column order, and an assumed order silently swaps a rate with a
    quantity on the one bill that prints them the other way round.
    `labels.py`'s own docstring makes the same argument: guessing at an
    unlabelled number is not safe.

    The FIRST qualifying line wins. A bill with two tables has its second one
    read as rows of the first, which is a limitation and is recorded as one -
    but reading neither would be worse, and reading the last one would make the
    answer depend on how far down the page somebody scrolled.
    """
    for index, line in enumerate(lines):
        upper = line.upper()
        seen: list[tuple[int, Column]] = []
        taken: list[tuple[int, int]] = []
        for name, column in COLUMN_NAMES:
            at = upper.find(name)
            if at < 0:
                continue
            if any(start < at + len(name) and at < end for start, end in taken):
                continue
            taken.append((at, at + len(name)))
            seen.append((at, column))
        if len(seen) >= ENOUGH_HEADINGS:
            return Header(line=index, columns=tuple(col for _at, col in sorted(seen)))
    return None


@dataclass(frozen=True)
class Cell:
    """One column's characters on one row, before anything converted them."""

    column: Column
    printed: str
    where: Where


def _row_cells(line: str, index: int, header: Header) -> tuple[Cell, ...] | None:
    """A row split into its columns, or None because it is not a row.

    SPLIT FROM THE RIGHT, and that is the whole design. A description is the
    only column that can hold spaces, so the last `n - 1` whitespace-separated
    tokens are the other columns and everything before them is the description.

    WHY NOT COLUMN POSITIONS. Slicing by the header's character offsets is the
    obvious alternative and it works beautifully on a PDF text layer, where the
    column gaps survive. It fails completely on a reading rebuilt from a word
    list, because that line is single-spaced and every column sits at the wrong
    offset. The right-anchored split works on both, which is worth more than
    working perfectly on one.

    WHAT IT COSTS, STATED RATHER THAN DISCOVERED LATER: a row with a BLANK
    column has too few tokens, and is REFUSED rather than shifted. A shifted row
    puts the rate in the quantity column and still multiplies out to something,
    which is the worst possible failure - a wrong answer that passes its own
    arithmetic. `validate.py` could not catch it and neither could a person.
    """
    if Column.DESCRIPTION not in header.columns:
        return None
    tokens = line.split()
    trailing = len(header.columns) - 1
    if len(tokens) <= trailing:
        return None
    description = BETWEEN_WORDS.join(tokens[: len(tokens) - trailing])
    values = [description, *tokens[len(tokens) - trailing :]]

    cells: list[Cell] = []
    at = 0
    for column, printed in zip(header.columns, values, strict=True):
        start = line.find(printed, at)
        if start < 0:  # pragma: no cover - the tokens came out of this line
            return None
        at = start + len(printed)
        cells.append(
            Cell(
                column=column,
                printed=printed,
                where=Where(line=index, start=start, end=at),
            )
        )
    return tuple(cells)


#: How each column's characters become a value. `None` from any of these means
#: the characters are not that kind of thing, and the cell is reported unread
#: with the characters attached rather than repaired.
_CONVERT: Final[dict[Column, object]] = {}


def _cell_value(column: Column, printed: str) -> object:
    """One cell's characters as the value that column holds, or None.

    A plain `if` ladder and not a table of functions, because pyright can see
    the return type of each branch and cannot see through a dict of callables
    returning `object` - and a type checker that cannot see this is a type
    checker that cannot catch a quantity assigned to a rate.
    """
    text = printed.strip()
    if column is Column.DESCRIPTION or column is Column.UNIT:
        return text or None
    if column is Column.HSN_SAC:
        code: Code | None = normalise(text)
        return None if code is None else str(code)
    if column is Column.QUANTITY:
        return quantity_milli(text)
    if column is Column.GST_RATE:
        return gst_rate_basis_points(text)
    return paise_or_none(text)


def _cell_field(reading: Reading, cell: Cell) -> ReadField:
    value = _cell_value(cell.column, cell.printed)
    if value is None:
        return unread(reading.source)
    return read_as(
        value,
        confidence=score_of(reading, cell.where, format_valid=True),
        source=reading.source,
        method=Method.IN_A_COLUMN,
        printed=cell.printed,
        where=cell.where,
    )


def read_rows(reading: Reading) -> tuple[dict[Column, ReadField], ...]:
    """Every table row under the header, as column -> field.

    Stops at the first line that is not a row. A blank line, a `Total` summary
    or a terms-and-conditions paragraph all end the table, and continuing past
    one of them would read the footer as goods.
    """
    header = find_header(reading.lines)
    if header is None:
        return ()
    rows: list[dict[Column, ReadField]] = []
    for index in range(header.line + 1, len(reading.lines)):
        cells = _row_cells(reading.lines[index], index, header)
        if cells is None:
            break
        row = {cell.column: _cell_field(reading, cell) for cell in cells}
        if not row[Column.DESCRIPTION].read:
            break
        rows.append(row)
    return tuple(rows)
