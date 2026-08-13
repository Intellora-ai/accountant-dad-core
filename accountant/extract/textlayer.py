"""The text-layer reader. The rung of the ladder that needs no pixels.

WHY THIS FILE EXISTS, AND WHY IT IS FIRST
------------------------------------------
A GST e-invoice, an emailed bill and a supplier's PDF all carry a TEXT LAYER -
the characters the producing program wrote, still in the file. For those
documents OCR is the wrong tool. Text extraction is EXACT, costs about ten
milliseconds, needs no model, no network and no training data, and returns the
same answer on every machine that has ever run it.

OCR is for photographs and scans. They are a DIFFERENT RUNG, not a competitor,
and noticing that these two are a ladder rather than a choice is the whole
saving: the cheap deterministic reader handles the documents that are already
machine-readable, and the expensive uncertain one is only reached by the
documents that actually need it.

WHY A FIELD FROM HERE CARRIES CONFIDENCE EXACTLY 1.0
-----------------------------------------------------
`confidence.EXACT` is 1.0, and this module is the only reader entitled to it.
The reason is not that this reader is good. It is that there is nothing here to
be unsure ABOUT: the file says `584.10` in characters, and the alternative
readings of those characters number zero. A score of 0.99 would be a claim
about an uncertainty that does not exist, and 1.0 on a guess would be a lie.

So the rule this module holds itself to is the strict form of that sentence:

    a value it returns was READ. Anything that required a guess is not
    returned at all.

That is why a `06/10/2026` comes back UNREAD. Read day-first it is the 6th of
October; read month-first it is the 10th of June; both are somebody's national
convention and neither is in the document. A guessed date at confidence 1.0
files a GST return in the wrong month with nothing on screen to notice. Six of
the twenty corpus PDFs are refused for exactly this and that is the correct
answer, not a gap.

DECIDED 2026-08-13: a locale is never assumed, and the refusal must be a
sentence somebody can act on. It names BOTH readings in months a person
recognises and asks for `YYYY-MM-DD`, which is the one written form of a date
that cannot be read two ways. `_ambiguous` is the whole of it.

WHEN THE FILE HAD TO BE REPAIRED TO READ IT
---------------------------------------------
Corrupt nothing in a corpus PDF but the number after its `startxref` and
`pypdf` rebuilds the object table by scanning for `N 0 obj`, then reads the
bill perfectly - same total, `Outcome.READ`, confidence 1.0. Measured. The
reconstruction leaves no mark, and a rebuild keeps the LAST definition of an
object it finds while the cross-reference table names the CURRENT one, so
"append a new total, break the xref" reads back the appended figure.

So `xref_was_rebuilt` asks the bytes whether the document's own pointer leads
where it says, and `TextLayerReading.pdf_repaired` carries the answer with the
reading. What is DONE about it - never auto-posting one of these - is
`cage/decision.py`'s half and deliberately not here: this file measures, the
cage decides.

WHAT IT DOES WHEN THERE IS NO TEXT LAYER
------------------------------------------
It says so, and stops. `Outcome.NO_TEXT_LAYER` is a scan or a photograph, and
it is neither an error nor an empty bill. It does not fall through to OCR -
this module contains no OCR and must not acquire any, because a ladder whose
top rung silently does the bottom rung's work is one rung with two names.

FAILS CLOSED, AND WHY THAT MATTERS MORE HERE THAN ANYWHERE
------------------------------------------------------------
`pypdf` parses UNTRUSTED bytes in this process. Somebody emails a bill; the
bytes arrive from outside and are handed straight to a parser. So:

    a parse failure is a REFUSAL, never a traceback. `pipeline.build_draft`
    calls its extractor with nothing around it and `web/app.py` turns an
    escaping exception into "Something in Accountant Dad broke" - which is
    what a person sees today when their upload was merely truncated.

    nothing is unzipped, executed, decrypted or followed. This module reads
    `PdfReader(...).pages[n].extract_text()` and nothing else. It never touches
    attachments, `/EmbeddedFiles`, `/OpenAction`, `/Launch`, `/URI` or
    `decrypt()`. An encrypted file is refused, not opened - we hold no password
    and guessing one is not reading.

    every unread field says WHY, in the sentence a person reads.

`BaseException` is deliberately not caught, for the reason
`registry.GuardedExtractor` gives: a KeyboardInterrupt is somebody stopping the
process and answering it with a tidy record would fight them.

WHAT THIS FILE DOES NOT PROVE
------------------------------
That a field it read is CORRECT. Exactness is about the READING, not about the
document. A supplier who prints the wrong total has it read perfectly. The
conservation laws run afterwards and are what catch that.

That it reads every PDF. It refuses anything not beginning `%PDF-`, which
rejects the small number of real files carrying junk before the header. That is
a refusal, and refusing is the safe direction.

That the label vocabulary below is complete. `TOTAL`, `GRAND TOTAL`, `AMOUNT
PAYABLE` and the rest are the names suppliers in the corpus print. A bill using
a name not on the list has its total UNREAD and the person is asked. Widening
the list is safe; guessing at an unlabelled number is not.

NO NETWORK, NO CLOCK, NO FILESYSTEM. Bytes in, a reading out.
"""

from __future__ import annotations

import datetime
import io
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from pypdf import PdfReader

from accountant.cage.confidence import EXACT
from accountant.cage.wall import Field, Observation
from accountant.extract.adapter import (
    NOT_FOUND,
    ExtractedRecord,
    LineItem,
    UnavailableExtractor,
)

# THE LABEL VOCABULARY AND THE MATCHING LIVE IN `labels.py` AS OF 2026-08-13,
# and this module reads them back from there rather than holding its own copy.
# The reading engine's page reader needs the same answers off the same shape of
# text, and two vocabularies is how one of them learns `AMOUNT PAYABLE` and the
# other does not - the same bill then reads differently depending on whether it
# arrived as a PDF or as a photograph. `labels.py` imports nothing outside the
# standard library, so nothing about this module's dependencies changed.
#
# `paise_or_none` is re-exported rather than re-implemented: it was already the
# ONE rule about sub-paise on this side of the system and callers name it here.
from accountant.extract.labels import (
    CURRENCY,
    DATE_LABEL,
    PARTY_LABELS,
    TAX_PARTS,
    TAX_WHOLE,
    TOTAL_LABELS,
    Amount,
    Found,
    Printing,
    amount_on,
    amounts_for,
    paise_or_none,
    the_one,
    values_for,
)

#: THIS TIER READS BYTES, NEVER PIXELS, AND SAYS SO AT EVERY CALL SITE.
#: `labels.Printing` has no default for the reason `cage/decision.Moment` has
#: none: a tolerance that could be inherited silently would be, and the field
#: this module is proudest of - 20/20 party, 20/20 total, 20/20 tax, ZERO wrong
#: on the twenty corpus PDFs - is exactly the one a tolerance would cost.
_PRINTING: Final = Printing.EXACT_CHARACTERS

#: What every field this module reads says about where it came from.
SOURCE: Final = "pdf_text_layer"

#: The media type this backend reads. Anything else is refused rather than
#: sniffed: `TypedTextExtractor` spent months returning invented totals because
#: it ran a money regex over whatever container it was handed.
PDF_MIME: Final = "application/pdf"

#: The first five bytes of every PDF. Checked before `pypdf` is reached so the
#: commonest mistake - a JPEG or a text file sent as a PDF - produces a sentence
#: naming the real problem instead of a parser's internal error.
PDF_MAGIC: Final = b"%PDF-"

#: The five fields this tier answers, in the order everything here reports them.
FIELDS: Final[tuple[str, ...]] = (
    "date",
    "party",
    "total_paise",
    "tax_paise",
    "line_paise",
)


class Outcome(StrEnum):
    """What happened to the document, which is not the same as what was read.

    Three, and not two, for the same reason `conservation.Verdict` has three.
    `NO_TEXT_LAYER` and `UNREADABLE` both return nothing, and collapsing them
    sends somebody hunting a corrupt file when what they have is a scan - or
    worse, sends a genuinely broken file to an OCR tier that will read noise
    out of it.
    """

    READ = "read"
    NO_TEXT_LAYER = "no_text_layer"
    UNREADABLE = "unreadable"


@dataclass(frozen=True)
class LineRead:
    """One line item, as printed. Integer paise, and its own description."""

    description: str
    amount_paise: int


@dataclass(frozen=True)
class TextLayerReading:
    """What the text layer said, and what it did not say.

    Frozen. A reading that can be edited after the fact is not evidence, which
    is the same rule the nine types in `schema.py` are built on.

    `observation` is the cage's own type, so this plugs into the decision layer
    without a translation step. `fields` exposes all FIVE as `Field` objects -
    `Observation.line_paise` is a bare tuple with no confidence slot, and a
    reader whose line items carried no stated confidence would be making the
    exact silent-blank claim this repository keeps finding.
    """

    outcome: Outcome
    observation: Observation
    fields: dict[str, Field]
    said: str
    lines: tuple[LineRead, ...] = ()
    text: str = ""
    pages: int = 0
    pdf_repaired: bool = False


# =============================================================================
# was this reading reconstructed?
# =============================================================================

#: What a person is told about a document whose object table was rebuilt.
#: OWNER DECISION 2026-08-13, word for word. The decision-layer half - never
#: auto-post on one of these - belongs to `cage/decision.py` and is not here.
REPAIRED_WARNING: Final = (
    "This PDF was damaged and had to be repaired to read it. The extracted "
    "data may be unreliable. Please verify carefully."
)

_STARTXREF: Final = b"startxref"
_DIGITS: Final = b"0123456789"

#: The two things a `startxref` offset may legally point at: the classic
#: `xref` keyword, or the object header of a cross-reference STREAM. Enough
#: bytes for `NNNNNNNNNN GGGGG obj` and no more.
_XREF_KEYWORD: Final = b"xref"
_OBJECT_HEADER: Final = re.compile(rb"\d+\s+\d+\s+obj\b")
_ENOUGH: Final = 32

#: `0 6` - the first object number this run of entries covers, and how many.
_SUBSECTION: Final = re.compile(rb"\s*(\d+)\s+(\d+)\s*?[\r\n]+")

#: `0000000245 00000 n `. Twenty bytes by the specification, and written as a
#: pattern rather than a slice because writers disagree about the last two.
_XREF_ENTRY: Final = re.compile(rb"(\d{10}) (\d{5}) ([nf])[ \r\n]{1,2}")

#: How many digits a number in this structure may have before it is stating
#: something no file can be. 2**63 has 19 digits, so 19 covers every offset,
#: object number and entry count any real PDF can carry - and CPython refuses
#: to convert a literal past 4300 digits at all, which is what turned an
#: unguarded `int()` here into a crash rather than a large number.
_MOST_DIGITS: Final = 19


def xref_was_rebuilt(data: bytes) -> bool:
    """Did `pypdf` have to RECONSTRUCT this file's object table to read it?

    WHY THIS IS ASKED AT ALL. Corrupt nothing in a corpus PDF but the number
    after its `startxref` and `pypdf` rebuilds the object table by scanning the
    whole file for `N 0 obj`, then reads the bill perfectly - the same total,
    `Outcome.READ`, confidence 1.0. Measured, not assumed. The recovery is
    silent, and this rung's entire claim is that nothing it returns was guessed.

    IT IS NOT COSMETIC. A rebuild keeps the LAST definition of an object it
    finds, while the cross-reference table names the CURRENT one. So append a
    second copy of the content stream carrying a different total, break the
    pointer, and the reader answers the appended figure at confidence 1.0 -
    which is what a tampered invoice looks like, and `test_textlayer.py` runs
    exactly that.

    HOW IT IS TOLD, on the bytes and nothing else. The document states where
    its cross-reference structure lives; either that statement is true or the
    reader that followed it did not follow it. So: find the last `startxref`,
    read the offset, and look at what is actually there. No `pypdf` internals
    are touched, so a version upgrade cannot silently change the answer, and no
    logging or warning capture is involved, so two PDFs read at the same moment
    in two threads cannot swap answers.

    THE SAME QUESTION IS ASKED OF EVERY ENTRY, 2026-08-13. Asking it only of
    `startxref` left a second door, and adversarial verification walked through
    it: `pypdf` has a recovery path that never calls `_rebuild_xref_table`.
    Break the table's ENTRY for object 4, leave `startxref` correct, and it
    logs `Object ID 4,0 ref repaired`, rescans, and answers from what it finds.
    Shift every offset by seven bytes and it recovers all five objects the same
    way. Both read at confidence 1.0 and both were FALSE here. A rebuild keeps
    the LAST definition it finds, so both are the appended-total tamper above
    with the pointer left honest and the table broken instead.

    A table entry is the same kind of statement as `startxref` - object N
    starts at offset X - so it gets the same treatment: the bytes at X either
    begin `N G obj` or the reader did not go where the document sent it.

    WHAT IT DOES NOT CATCH, stated rather than left to be discovered. A pointer
    aimed at some OTHER object is accepted here, because a cross-reference
    stream is an ordinary object and refusing those would fire on a large share
    of the PDFs a modern word processor writes. Measured: `pypdf` refuses those
    files outright rather than rebuilding, so no reading comes out of that hole.
    The entries INSIDE such a stream are not walked either - they are
    compressed, and decompressing them here would be writing a second PDF
    parser to check the first one.

    True for bytes that state no pointer at all. `read` never asks about those
    - they do not parse - but the sentence this function answers is about the
    bytes, and bytes with no pointer state nothing for a reader to have read.
    """
    at = data.rfind(_STARTXREF)
    if at < 0:
        return True
    tail = data[at + len(_STARTXREF) :].lstrip()
    digits = tail[: len(tail) - len(tail.lstrip(_DIGITS))]
    if not digits:
        return True
    offset = _stated_number(digits)
    if offset is None or not 0 < offset < len(data):
        return True
    here = data[offset : offset + _ENOUGH]
    if here.startswith(_XREF_KEYWORD):
        return _table_lies(data, offset + len(_XREF_KEYWORD))
    return _OBJECT_HEADER.match(here) is None


def _table_lies(data: bytes, at: int) -> bool:
    """Does any entry of the classic table at `at` point somewhere it should not?

    Subsection by subsection, entry by entry, and each entry is checked against
    ITS OWN object number: `0000000245 00000 n` in the slot for object 4 means
    the bytes at 245 begin `4 0 obj`, and anything else means the reader that
    followed that entry landed somewhere the document did not send it.

    A table this cannot parse is NOT called a lie. `pypdf` would refuse such a
    file rather than read it, so answering True would put a warning on a
    document nobody read anything off - and the twenty corpus bills are the
    control that this stayed a detector rather than becoming a rubber stamp.
    """
    entries = _table_entries(data, at)
    if entries is None:
        return True
    for number, offset in entries:
        here = data[offset : offset + _ENOUGH]
        if not re.match(rb"%d\s+\d+\s+obj\b" % number, here):
            return True
    return False


def _table_entries(data: bytes, at: int) -> list[tuple[int, int]] | None:
    """`(object number, offset)` for every in-use entry of a classic table.

    Empty when the bytes are not a table this reads, which is the same answer
    as a table with nothing wrong in it. That is deliberate: this function
    reports what it could check, and `_table_lies` is what decides.

    `None` is the THIRD answer and it is not the same as empty: a subsection
    header stating an object number or a count of more digits than any file can
    hold is a table this could not finish reading, and `_table_lies` calls that
    a repair. Answering empty there would report "nothing wrong" about bytes
    that were never checked.

    `int(entry[1])` below needs no such guard - `_XREF_ENTRY` matches exactly
    ten digits, so the pattern is already the bound.
    """
    entries: list[tuple[int, int]] = []
    while (head := _SUBSECTION.match(data, at)) is not None:
        first, count = _stated_number(head[1]), _stated_number(head[2])
        if first is None or count is None:
            return None
        at = head.end()
        for index in range(count):
            entry = _XREF_ENTRY.match(data, at)
            if entry is None:
                return entries
            at = entry.end()
            if entry[3] == b"n":
                entries.append((first + index, int(entry[1])))
    return entries


def _stated_number(digits: bytes) -> int | None:
    """The number these ASCII digits state, or `None` when no file is that big.

    THE LEADING ZEROS ARE STRIPPED BEFORE THE LENGTH IS JUDGED, and that is the
    whole of the care in this function. `0000000245` is 245, and a `startxref`
    padded out to four thousand columns still names the offset it names - so
    the caller asking `0 < offset < len(data)` gets the answer it always got,
    and an honest document is never called repaired for its formatting.

    What is refused is a number too long to be an offset into anything: 2**63
    has 19 digits and no upload is 10**19 bytes. The caller answers True for
    that, which is the same answer `0 < offset < len(data)` would have reached
    on its own - CPython simply will not convert a literal past 4300 digits to
    let it, and the unguarded `int()` that used to be here raised `ValueError`
    straight out of a `read` whose docstring promises it never raises.

    MEASURED 2026-08-13: a 588-byte PDF that reads correctly, with `startxref`
    and 4301 zeros appended AFTER its `%%EOF`. `pypdf` stops at the last end
    marker so it never saw those bytes; this guard does `rfind` over the whole
    buffer, so they were the only pointer it read. The offset's value was zero.
    Only its written form was long.
    """
    lean = digits.lstrip(b"0")
    if not lean:
        return 0
    return int(lean) if len(lean) <= _MOST_DIGITS else None


# =============================================================================
# reading one labelled thing off a page
# =============================================================================
#
# The label vocabulary, `paise_or_none` and the two matchers now live in
# `labels.py` and are imported at the top of this file. What is left here is
# what only a PDF's text layer has: an ITEMISED BLOCK with a column header, a
# printed rule closing it, and dates written four different ways.

#: A line item: a description, then an amount with a decimal point in it.
#: Requiring the decimals is what keeps `PAGE 1 OF 2` and `GT/0021` out of the
#: line items - and it is how a bill actually prints money.
_ITEM: Final = re.compile(
    rf"^\|?\s*(?P<desc>\S.*?)\s*\|?\s*{CURRENCY}?\s*"
    rf"(?P<amount>-?[\d,]+\.\d{{2,}})\s*{CURRENCY}?\s*\|?\s*$"
)

_ITEM_HEADER: Final = re.compile(r"^DESCRIPTION\b.*\bAMOUNT\b")

#: A printed rule closes the itemised block. It must carry at least one drawn
#: character: a BLANK line is not a rule, and treating it as one meant that a
#: page break falling between the column header and the first row closed the
#: block before a single item was read. `_text_of` joins pages with a newline,
#: so that blank line is an artefact of OUR join, not something the bill prints.
_RULE: Final = re.compile(r"^[\s|]*[-=_][-=_\s|]*$")
_BLANK: Final = re.compile(r"^\s*$")

#: Written down rather than taken from `%B`, which follows the machine's
#: locale. A bill that parses on one machine and not another is not a reading,
#: and a refusal that names a month in the server's language is not a sentence
#: the person holding the bill can act on. `ingest/spend.py` carries the same
#: twelve for the same reason; importing them would make document extraction
#: depend on the UK-spend CSV loader, and that coupling is worse than three
#: lines of month names.
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

#: What a supplier prints. DERIVED, not written twice: two lists that must
#: agree are one list plus a bug waiting for somebody to edit one of them.
_MONTHS: Final[tuple[str, ...]] = tuple(name[:3].upper() for name in _MONTH_NAMES)

_ISO_DATE: Final = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$")
_NUMERIC_DATE: Final = re.compile(r"^(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})$")
_LONG_DATE: Final = re.compile(r"^(\d{1,2})[ -]([A-Z]{3})[A-Z]*[ -](\d{4})$")


# =============================================================================
# the five fields
# =============================================================================


def _date_from(printed: str) -> tuple[datetime.date | None, str]:
    """The date this string states, or None and the reason it states none.

    `2026-09-21` and `28 APR 2026` name their parts. `26/02/2026` does not, and
    is read only when ARITHMETIC settles the order: 26 is not a month, so the
    day is first and no convention was assumed. When both numbers could be
    either, this refuses. See the module docstring - a locale guess wearing
    confidence 1.0 is how a return gets filed in the wrong month.
    """
    text = printed.strip().upper()

    iso = _ISO_DATE.match(text)
    if iso is not None:
        return _real_date(int(iso[1]), int(iso[2]), int(iso[3]))

    spelled = _LONG_DATE.match(text)
    if spelled is not None and spelled[2] in _MONTHS:
        month = _MONTHS.index(spelled[2]) + 1
        return _real_date(int(spelled[3]), month, int(spelled[1]))

    numeric = _NUMERIC_DATE.match(text)
    if numeric is None:
        return None, (
            f"{NOT_FOUND}: {printed.strip()!r} is not a date this reader reads"
        )
    return _ordered_date(
        int(numeric[1]), int(numeric[2]), int(numeric[3]), printed.strip()
    )


def _ordered_date(
    first: int, second: int, year: int, printed: str
) -> tuple[datetime.date | None, str]:
    """`26/02/2026` when only one reading of it is a real date.

    Equal numbers are determined too: `05/05/2026` is the 5th of May either
    way round, so there is nothing to guess.

    NEITHER NUMBER A MONTH IS NOT AMBIGUITY, and until 2026-08-13 this fell
    through to the ambiguity sentence and CRASHED there - `85-13-2026` used 85
    as an index into the twelve month names, IndexError, escaping `read` into
    an HTTP 503 that told a person the application was broken. Ambiguity means
    two readings and no way to choose; this is zero readings, which is the
    impossible-date refusal and always was.

    A ZERO IS THE SAME CASE AT THE OTHER END, and it did not crash, which is
    why it survived the fix above. `_MONTH_NAMES[0 - 1]` is `_MONTH_NAMES[-1]`,
    so `10/00/2026` was answered "could be 10 December or 0 October" - a month
    nothing on the document mentions, INVENTED, inside the one sentence whose
    decided purpose is to carry both true readings. Zero is not a month and not
    a day, so neither reading exists and `_real_date` says exactly that.
    """
    if first == 0 or second == 0:
        return _real_date(year, second, first)
    if first == second or (first > 12 >= second):
        return _real_date(year, second, first)
    if second > 12 >= first:
        return _real_date(year, first, second)
    if first > 12 and second > 12:
        return None, (
            f"{NOT_FOUND}: {printed!r} is not a date - neither {first} nor "
            f"{second} can be a month, so it was misread or misprinted"
        )
    return None, f"{NOT_FOUND}: {_ambiguous(first, second, printed)}"


def _ambiguous(first: int, second: int, printed: str) -> str:
    """The sentence for a date whose order the document does not state.

    OWNER DECISION 2026-08-13, word for word, and the words are the point. The
    old sentence said the date was "either the 6 of month 10 or the 10 of month
    6" - true, and useless to somebody holding the bill, because it neither
    names the months nor says what to send back. This one does both:

        NAMES BOTH READINGS, in months a person recognises. A sentence carrying
        only one of them is a guess with a citation, which is worse than a
        guess, and it is the mutant this wording is tested against.

        ASKS FOR ISO. `YYYY-MM-DD` is the one written form of a date that
        cannot be read two ways, so the answer that comes back cannot reopen
        the question the refusal was raised about.

    QUOTES WHAT WAS PRINTED, not a normalised form. FOUR of the twenty corpus
    bills write their dates with hyphens - GT-0027, GT-0031, GT-0035, GT-0038 -
    and asking somebody to confirm `07/11/2026` when their document says
    `07-11-2026` is asking them to find a string that is not there.

    That count said TWO until 2026-08-13, when somebody counted. The decision
    it justifies was right and is unchanged; the number cited for it was not
    measured, and a number nobody measured is an opinion with a digit in it.
    `scripts/run_ground_truth.py` regenerates the corpus, so the count is
    checked by reading the twenty documents and never by remembering it.

    The first reading is day-first, the second month-first, which is the order
    the decision states them in - and neither is preferred by anything here.
    """
    return (
        f"The date '{printed}' is ambiguous (could be "
        f"{first} {_MONTH_NAMES[second - 1]} or {second} {_MONTH_NAMES[first - 1]}). "
        "Please confirm the date in YYYY-MM-DD format."
    )


def _real_date(year: int, month: int, day: int) -> tuple[datetime.date | None, str]:
    """A date that exists. The 34th of a month is a misread 04th, not a date."""
    try:
        return datetime.date(year, month, day), ""
    except ValueError:
        return None, (
            f"{NOT_FOUND}: {year}-{month:02d}-{day:02d} is not a day that "
            "exists, so it was misread or misprinted"
        )


def _printed(found: tuple[Found, ...]) -> tuple[str, ...]:
    """Just the characters, for the readers here that want nothing else.

    `labels.values_for` also reports WHERE each value sat, because the reading
    engine's page reader has to map a value back to the words that produced it.
    This rung has no words, so it drops the positions here rather than
    threading them through four functions that would never look at them.
    """
    return tuple(one.printed for one in found)


def _paise(found: tuple[Amount, ...]) -> tuple[int, ...]:
    """Just the figures. Same reason as `_printed` above."""
    return tuple(one.paise for one in found)


def _read_date(lines: tuple[str, ...]) -> tuple[datetime.date | None, str]:
    printed, why = the_one(
        _printed(values_for(lines, (DATE_LABEL,), printing=_PRINTING)), "its date"
    )
    return (None, why) if printed is None else _date_from(printed)


def _read_party(lines: tuple[str, ...]) -> tuple[str | None, str]:
    return the_one(
        _printed(values_for(lines, PARTY_LABELS, printing=_PRINTING)), "its supplier"
    )


def _read_total(lines: tuple[str, ...]) -> tuple[int | None, str]:
    return the_one(_paise(amounts_for(lines, TOTAL_LABELS)), "its total")


def _split_tax(lines: tuple[str, ...]) -> tuple[int | None, str, bool]:
    """CGST + SGST + UTGST, each agreed with its OWN repeats before adding.

    Agreed first, and that is not a detail. Summing every printing of a split
    tax made a two-page bill whose footer repeats read 376.64 where the
    document says 188.32 twice - double the tax, at confidence 1.0, from a
    document that never contradicted itself. Repetition is what a continuation
    sheet does; only DISAGREEMENT is refused, exactly as it is for the total.

    The third value is "were any split labels printed at all", which the caller
    needs and cannot get from the sum: a bill with no CGST line and a bill
    whose CGST is zero are different facts.
    """
    added = 0
    printed_any = False
    for label in TAX_PARTS:
        printed = _paise(amounts_for(lines, (label,)))
        if not printed:
            continue
        printed_any = True
        stated, why = the_one(printed, f"its {label}")
        if stated is None:
            return None, why, True
        added += stated
    return (added if printed_any else None), "", printed_any


def _read_tax(lines: tuple[str, ...]) -> tuple[int | None, str]:
    """The tax on this bill, whether printed whole or printed in halves.

    Adding CGST to SGST is arithmetic on two figures that were both READ
    exactly, so the answer is still read rather than estimated. A bill printing
    BOTH a combined figure and its halves is refused: adding them double-counts
    and taking one assumes the other is a breakdown of it, and neither of those
    is something the document said.
    """
    split, why_split, has_split = _split_tax(lines)
    whole = _paise(amounts_for(lines, TAX_WHOLE))
    if has_split and whole:
        return None, (
            f"{NOT_FOUND}: this bill prints its tax both as one figure and as "
            "separate parts, and adding them would count the same tax twice"
        )
    if has_split:
        return split, why_split
    return the_one(whole, "its tax")


#: Labels that belong to a bill's FOOTER rather than to its items. `SUBTOTAL`
#: is here and deliberately absent from `labels.TOTAL_LABELS`: it closes the itemised
#: block without ever being readable as the amount payable.
_FOOTER_LABELS: Final[tuple[str, ...]] = (
    "SUBTOTAL",
    *TOTAL_LABELS,
    *TAX_WHOLE,
    *TAX_PARTS,
)


def _is_footer_row(line: str) -> bool:
    """Is this the footer starting, rather than another line item?

    Tested as "a footer label and then NOTHING BUT an amount", never as a
    prefix. An item called `TAX CONSULTANCY   500.00` begins with a tax label
    and is not a footer; closing the block on it would drop every line after it,
    the lines would stop adding up to the total, and `lines_sum_to_total` would
    blame the bill for the reader.

    This is what lets a blank line be skipped safely. Without it, a table whose
    closing rule is missing would run on and read `SUBTOTAL` as an item.
    """
    return any(amount_on(line, label) is not None for label in _FOOTER_LABELS)


def _read_lines(lines: tuple[str, ...]) -> tuple[tuple[LineRead, ...] | None, str]:
    """The itemised block, or None when the bill did not itemise.

    NONE, NOT AN EMPTY TUPLE. `conservation.lines_sum_to_total` reads `()` as
    "we read the lines and there were none", which contradicts any non-zero
    total and comes back FAIL. A bill saying "supply as per agreement" is not a
    bill whose arithmetic is wrong - it is one whose arithmetic we could not
    check, which is INDETERMINATE, and the difference is the difference between
    accusing a supplier and asking a question.

    A SECOND HEADER IS REFUSED, NOT APPENDED. A continuation sheet that
    reprints the table used to come back as the same items twice - a two-line
    bill answering `(100000, 4624, 100000, 4624)`, which does not add up to its
    own total, so `lines_sum_to_total` would report the BILL as wrong when the
    READER is what failed. Nothing in the bytes says whether a second table is
    the first one reprinted or genuinely more items, so it is asked.
    """
    found: list[LineRead] = []
    inside = False
    headed = False
    for line in lines:
        if _ITEM_HEADER.match(line):
            if headed:
                return None, (
                    f"{NOT_FOUND}: this document prints its itemised table more "
                    "than once, and nothing in it says whether the second is the "
                    "first reprinted on a continuation sheet or a further set of "
                    "items. Adding them would invent line items"
                )
            headed = True
            inside = True
            continue
        if not inside:
            continue
        if _BLANK.match(line):
            # A page join, not the end of the table. `_text_of` puts this
            # newline here; the bill never printed it. Closing the block on it
            # meant a page break falling between the column header and the
            # first row left the whole table unread.
            continue
        if _RULE.match(line) or _is_footer_row(line):
            inside = False
            continue
        item = _ITEM.match(line)
        amount = None if item is None else paise_or_none(item.group("amount"))
        if item is None or amount is None:
            return None, (
                f"{NOT_FOUND}: {line.strip()!r} sits in this bill's itemised "
                "block and no amount could be read from it. Skipping it would "
                "produce lines that do not add up to the total, and the bill "
                "would be reported as wrong when the reading is what failed"
            )
        found.append(
            LineRead(description=item.group("desc").strip(" |"), amount_paise=amount)
        )
    if not found:
        return None, (
            f"{NOT_FOUND}: this bill has no itemised block, so its line items "
            "were not read. That is not the same as a bill with no items"
        )
    return tuple(found), ""


# =============================================================================
# the reading
# =============================================================================


#: One field's value and, when there is none, the sentence saying why not.
type Answered[T] = tuple[T | None, str]

#: The answer for a field nothing was read for. The reason is filled in from
#: the reading's own sentence, so a refusal cannot produce a blank with no why.
_NOTHING: Final[Answered[object]] = (None, "")


def _field(value: object, why: str, fallback: str) -> Field:
    """One field, at `EXACT` when it was read and at 0.0 when it was not.

    There is no middle value on this rung and there must never be one. A number
    between the two would mean somebody estimated, and estimating is the job of
    the tier that looks at pixels.

    `fallback` is the whole reading's sentence, used when a field has no reason
    of its own - which is every field of a document that could not be opened.
    `Field` refuses an empty source, so this is what stops a refusal producing
    four blanks that cannot say what happened.
    """
    if value is None:
        return Field(value=None, confidence=0.0, source=why or fallback)
    return Field(value=value, confidence=EXACT, source=SOURCE)


def _reading(
    outcome: Outcome,
    said: str,
    *,
    date: Answered[datetime.date] = _NOTHING,
    party: Answered[str] = _NOTHING,
    total: Answered[int] = _NOTHING,
    tax: Answered[int] = _NOTHING,
    items: Answered[tuple[LineRead, ...]] = _NOTHING,
    text: str = "",
    pages: int = 0,
    repaired: bool = False,
) -> TextLayerReading:
    """Assemble one reading. The ONE place a `TextLayerReading` is built.

    One place, for the reason `registry.GuardedExtractor.outage` gives about
    outage records: two constructors is how one of them ends up without a
    reason on a blank field, which is a silent blank wearing a label.

    The repair warning is added BEFORE the fields are built, because `said` is
    also the fallback reason on a field that has none of its own. A warning
    appended afterwards would be missing from exactly the fields that carry no
    other explanation.
    """
    if repaired:
        said = f"{said} {REPAIRED_WARNING}"
    found = items[0]
    line_paise = None if found is None else tuple(i.amount_paise for i in found)
    fields = {
        "date": _field(date[0], date[1], said),
        "party": _field(party[0], party[1], said),
        "total_paise": _field(total[0], total[1], said),
        "tax_paise": _field(tax[0], tax[1], said),
        "line_paise": _field(line_paise, items[1], said),
    }
    return TextLayerReading(
        outcome=outcome,
        observation=Observation(
            date=fields["date"],
            party=fields["party"],
            total_paise=fields["total_paise"],
            tax_paise=fields["tax_paise"],
            line_paise=line_paise,
        ),
        fields=fields,
        said=said,
        lines=found or (),
        text=text,
        pages=pages,
        pdf_repaired=repaired,
    )


def _text_of(data: bytes) -> tuple[str, int]:
    """The whole document's text layer, and its page count.

    `extract_text` and nothing else. No attachment is listed, no embedded file
    is opened, no action is followed and nothing is decrypted - see the module
    docstring for why that list is written down rather than assumed.
    """
    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted:
        raise _Refused(
            "this PDF is encrypted and we hold no password for it. Guessing "
            "one is not reading, so nothing was taken from it"
        )
    pages = len(reader.pages)
    if pages == 0:
        raise _Refused(
            "this PDF declares no pages, so there is no document in it to read"
        )
    return "\n".join(page.extract_text() for page in reader.pages), pages


class _Refused(Exception):
    """A refusal this module states in its own words rather than a parser's."""


def read(data: bytes) -> TextLayerReading:
    """Read the text layer of `data`, or say why nothing was read.

    NEVER RAISES. Every failure inside `pypdf` - a truncated upload, a cyclic
    page tree, a stream that ends early - comes back as `Outcome.UNREADABLE`
    with a sentence, because the alternative is an HTTP 503 telling a person
    the application is broken when their upload merely got cut off.
    """
    if not data.startswith(PDF_MAGIC):
        return _reading(
            Outcome.UNREADABLE,
            f"{NOT_FOUND}: these bytes are not a PDF - they do not begin with "
            f"{PDF_MAGIC.decode('ascii')}, so nothing was parsed from them",
        )
    try:
        text, pages = _text_of(data)
    except _Refused as refusal:
        return _reading(Outcome.UNREADABLE, f"{NOT_FOUND}: {refusal}")
    except Exception as exc:
        return _reading(
            Outcome.UNREADABLE,
            f"{NOT_FOUND}: this PDF could not be parsed "
            f"({type(exc).__name__}), so nothing was read from it",
        )

    # Asked only once the bytes have parsed. `pdf_repaired` is a statement
    # about data that came back, and a refusal brings none: telling somebody to
    # verify a reading carefully when there is no reading is noise on the one
    # warning that has to be read.
    #
    # INSIDE A TRY AS OF 2026-08-13, and it was not before. This call sat
    # outside the one above and broke the NEVER RAISES promise in the docstring
    # over it. The seam: `pypdf` stops at the last `%%EOF` while the guard does
    # `rfind` over the whole buffer, so bytes appended after the end marker are
    # invisible to the parser and authoritative for the guard - and an
    # unguarded `int()` behind them raised `ValueError` out of `extract`.
    #
    # The digit lengths are bounded now, so this should not fire. It is here
    # because the promise was resting on every line of that guard being right
    # and one of them was not, and the next such line should cost a warning
    # rather than a traceback. True is the answer on failure: "verify this
    # carefully" is the conservative thing to say about bytes we could not
    # finish checking, and False would tell the decision layer the reading was
    # trustworthy on the strength of a check that crashed.
    try:
        repaired = xref_was_rebuilt(data)
    except Exception:
        repaired = True

    if not text.strip():
        return _reading(
            Outcome.NO_TEXT_LAYER,
            f"{NOT_FOUND}: this PDF carries no text layer. It is a scan or a "
            "photograph, and reading pixels is the other tier's job - nothing "
            "here guesses at one",
            text=text,
            pages=pages,
            repaired=repaired,
        )
    return _parse(text, pages, repaired)


def _parse(text: str, pages: int, repaired: bool) -> TextLayerReading:
    lines = tuple(line.rstrip() for line in text.splitlines())
    answers: dict[str, Answered[object]] = {}
    date = answers["date"] = _read_date(lines)
    party = answers["party"] = _read_party(lines)
    total = answers["total_paise"] = _read_total(lines)
    tax = answers["tax_paise"] = _read_tax(lines)
    items = answers["line_paise"] = _read_lines(lines)
    return _reading(
        Outcome.READ,
        _said_about(pages, answers),
        date=date,
        party=party,
        total=total,
        tax=tax,
        items=items,
        text=text,
        pages=pages,
        repaired=repaired,
    )


def _said_about(pages: int, answers: dict[str, Answered[object]]) -> str:
    """The one sentence for the whole reading, naming every field it missed.

    A reading that said only "read" would put the person in front of a form
    with four blanks and no explanation of any of them.
    """
    missed = [name for name in FIELDS if answers[name][0] is None]
    said = f"read the text layer of this {pages}-page PDF"
    if not missed:
        return said + "."
    why = "; ".join(f"{name} - {answers[name][1]}" for name in missed)
    return f"{said}. Not read: {why}."


# =============================================================================
# the seam
# =============================================================================


class TextLayerReader:
    """The text-layer tier, as an `adapter.Extractor`.

    The seam already exists and is not redesigned here: `pipeline.build_draft`
    takes an `Extractor` and never names a class, so this rung joins the same
    Protocol the other backends satisfy. Registering it is one line in
    `registry._READY` and that file chooses backends; this one is a backend.

    THE RECORD IS NOT THE READING. `ExtractedRecord` has no confidence on it,
    which is the whole reason `accountant/cage/wall.py` exists. Callers inside
    the cage want `read()` and its `Observation`; this class is for the
    existing pipeline, and both come from the same parse.
    """

    name = SOURCE

    def _refuse(self, reason: str) -> ExtractedRecord:
        """Every field `not_found`, with this reason on each.

        `UnavailableExtractor`, which is the one class in this package that
        builds an outage record. A second shape that resembles it is how one of
        them ends up with no reason on it.
        """
        return UnavailableExtractor(reason, name=self.name).extract(b"", "")

    def extract(self, data: bytes, mime: str) -> ExtractedRecord:
        declared = _declared_media_type(mime)
        if declared != PDF_MIME:
            return self._refuse(
                f"{self.name} reads the text layer of a PDF and was handed "
                f"{declared or 'no media type'}; reading that is another "
                "tier's job and nothing here guesses at its contents"
            )
        reading = read(data)
        return ExtractedRecord(
            date=_as_date(reading.fields["date"].value),
            party=_as_str(reading.fields["party"].value),
            total_paise=_as_int(reading.fields["total_paise"].value),
            tax_paise=_as_int(reading.fields["tax_paise"].value),
            line_items=tuple(
                LineItem(description=item.description, amount_paise=item.amount_paise)
                for item in reading.lines
            ),
            raw_text=reading.text,
            backend=self.name,
            per_field_source={
                name: reading.fields[name].source
                for name in ("date", "party", "total_paise", "tax_paise")
            },
            # The same `EXACT` this reading already states on every field it
            # read, carried rather than recomputed. `ExtractedRecord` had no
            # column for it until 2026-08-13, so the tier that is entitled to
            # 1.0 and the tier that guesses at pixels arrived downstream
            # indistinguishable - and the consumer that could not tell them
            # apart was the one turning a party name into a vendor identity.
            per_field_confidence={
                name: reading.fields[name].confidence
                for name in ("date", "party", "total_paise", "tax_paise")
            },
            # The fact travels with the evidence, 2026-08-13. This line was
            # missing and `ExtractedRecord` had nowhere to put it, so a
            # repaired PDF became an ordinary record here and the decision
            # layer's ceiling was unreachable from any shipped path. It is not
            # a per-field source because it is not something read off the
            # document - it is what we had to do to the bytes to read anything.
            pdf_repaired=reading.pdf_repaired,
        )


def _declared_media_type(mime: str) -> str:
    """`application/pdf; qs=0.001` -> `application/pdf`.

    The same one line as `adapter._media_type`, and written again rather than
    imported because that one is private and pyright's strict mode is right to
    refuse a private name crossing a module boundary. It reads the CALLER'S own
    declaration and never the bytes; refusing a real form's charset parameter
    would be a refusal nobody could act on.
    """
    return mime.split(";", 1)[0].strip().lower()


def _as_date(value: object) -> datetime.date | None:
    return value if isinstance(value, datetime.date) else None


def _as_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _as_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None
