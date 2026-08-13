"""Child 15 — extraction adapter.

WE WRITE AN ADAPTER, NEVER A READER. Not one line of OCR, layout analysis or
field detection lives in this package. If a task here starts to look like reading
a document, it is the wrong task.

Two backends today:
  TypedTextExtractor  parses a line a person typed. Not OCR.
  StubExtractor       fixed answers, for testing everything downstream.

A real third-party reader plugs in behind the same Protocol later, and nothing
outside this package changes.
"""

from __future__ import annotations

import datetime
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Protocol, runtime_checkable

NOT_FOUND = "not_found"

#: The one media type a sentence a person typed arrives as.
#:
#: PHASE 8 PR-1. `TypedTextExtractor.extract` took `_mime` and threw it away,
#: and the cost of throwing it away was measured on the five input types before
#: this constant existed:
#:
#:     %PDF-1.7 ...          total_paise = 170,     source "typed_text"
#:     PNG with a tEXt chunk total_paise = 420000,  source "typed_text"
#:     JPEG with a COM       total_paise = 3133700, source "typed_text"
#:
#: Those are not blanks and they are not `not_found`. They are invented numbers
#: wearing a real backend's name, which is worse than either, because a blank
#: asks the person a question and an invented total does not.
TYPED_TEXT_MIME = "text/plain"


@dataclass(frozen=True)
class LineItem:
    description: str
    amount_paise: int


@dataclass(frozen=True)
class ExtractedRecord:
    """Every named field is present, always. A field is either a value or an
    explicit not_found. A silently blank field is a bug (S3)."""

    date: datetime.date | None
    party: str | None
    total_paise: int | None
    tax_paise: int | None
    line_items: tuple[LineItem, ...] = ()
    raw_text: str = ""
    backend: str = "unknown"
    per_field_source: dict[str, str] = field(default_factory=dict[str, str])

    #: Did the reader have to MEND the bytes before it could read them?
    #:
    #: `None` means there was nothing to mend - not a PDF, or a backend that
    #: cannot tell. `False` is a reader that looked and found the file whole.
    #: It is not one of `FIELDS` because it is not something read OFF the
    #: document; it is a fact about our own processing of it, and it has no
    #: source line for the same reason.
    #:
    #: ADDED 2026-08-13. `textlayer.TextLayerReading` had carried this since the
    #: rung was written and `TextLayerReader.extract` DROPPED IT while building
    #: this record, so a repaired PDF reached the cage indistinguishable from an
    #: honest one - measured: `"repair" in repr(record).lower()` was False, and
    #: every per-field source said `pdf_text_layer`. The ceiling that refuses to
    #: auto-post one of these existed and nothing could reach it.
    pdf_repaired: bool | None = None

    FIELDS = ("date", "party", "total_paise", "tax_paise")

    def __post_init__(self) -> None:
        missing = [f for f in self.FIELDS if f not in self.per_field_source]
        if missing:
            raise ValueError(
                f"incomplete record: no source stated for {', '.join(missing)}"
            )

    @property
    def complete(self) -> bool:
        return all(f in self.per_field_source for f in self.FIELDS)


@runtime_checkable
class Extractor(Protocol):
    def extract(self, data: bytes, mime: str, /) -> ExtractedRecord: ...


# ---- backends ---------------------------------------------------------------

# `\.\d+`, not `\.\d{1,2}`. Capturing at most two decimals meant "10.005"
# matched as "10.00" and the half-paise was gone before any conversion could
# object to it. The truncation was in the pattern, not in the arithmetic.
#
# `\d[\d,]*`, not `[\d,]+`. The old class matched a BARE COMMA - "paid for the
# lot, TOTAL 4200" found "," first, `_to_paise` could make nothing of it, and
# the sentence read as having no amount at all. A number starts with a digit.
_AMOUNT = re.compile(r"(?:rs\.?|₹)?\s*(\d[\d,]*(?:\.\d+)?)", re.I)
_GST_PCT = re.compile(
    r"(\d{1,2}(?:\.\d+)?)\s*%\s*gst|gst\s*@?\s*(\d{1,2}(?:\.\d+)?)\s*%", re.I
)
# Party names keep "/" so the Indian "M/s Sharma Traders" form survives, and
# stop at a lowercase word so "paid Sharma Traders 4200 for cement" does not
# swallow "for cement".
_PARTY = re.compile(
    r"(?:from|to|paid|for)\s+((?:M/s\s+)?[A-Z][\w&./\-]*(?:\s+[A-Z][\w&./\-]*)*)", re.U
)


_HUNDRED = Decimal(100)


# =============================================================================
# THE FIRST NUMBER IS NOT THE AMOUNT
# =============================================================================
#
# `_AMOUNT.findall(text)[0]` took the FIRST number in the text. On a sentence a
# person typed that is the amount. On an invoice layout it is the invoice
# number, and the backend could not tell the two apart because it checked the
# MEDIA TYPE and never the SHAPE - `text/plain` is `text/plain` whether one
# line was typed or a whole bill was pasted, and that conflation IS the defect.
#
# Measured on the committed pack before any of this existed
# (`scripts/run_ground_truth.py`): 20 of 20 `text/plain` cases returned a WRONG
# `total_paise` sourced `typed_text`. GT-0001 states TOTAL 147.50 and this
# backend answered 100 paise, read off `GT/0001`.
#
# Everything below decides from the shape of the text. Nothing here opens a
# container or interprets a byte: it is still string parsing, and reading a
# document is still the third-party backend's job.

#: What the person reads when the text is laid out like a bill. Owner wording,
#: PHASE 8 DECISION 1, verbatim - it names the two things they can actually do.
INVOICE_SHAPED = (
    "This document looks like an invoice, but the amount could not be reliably "
    "read. Please upload a clearer image or a proper PDF."
)

#: What the person reads when the text is NOT invoice-shaped but carries more
#: than one number. Owner wording, verbatim.
AMBIGUOUS_NUMBERS = (
    "Multiple numbers were found and the amount could not be determined. Please "
    "specify the amount explicitly or upload a clearer document."
)

#: Invoice SEMANTICS. Each is a thing only a bill says, and each is checked
#: against the text rather than against the media type.
_INVOICE_MARKS = (
    re.compile(r"\btax\s+invoice\b", re.I),
    # BOTH forms of the invoice number, 2026-08-13. `invoice\s*(?:no|number|#)`
    # was the only one, and `Invoice 2451` - the commonest form an Indian
    # supplier prints - matched none of it. Measured: four texts whose first
    # line said TAX INVOICE were read for their invoice NUMBER and posted.
    re.compile(r"\binvoice\s*(?:no\b|number\b|#)", re.I),
    re.compile(r"\binvoice\s+\d", re.I),
    re.compile(r"\bbill\s+(?:no\.?\s*)?\d", re.I),
    re.compile(r"\bhsn\s*/?\s*sac\b", re.I),
    re.compile(r"\bplace\s+of\s+supply\b", re.I),
    re.compile(r"\bgstin\b", re.I),
    # A TOTAL LINE, not the word "total". A line that BEGINS with it is a
    # figure in a column; the same word inside a sentence is somebody talking.
    #
    # `[^\w\n]*` and not `[ \t|]*`: a leader of dots, a rule of dashes or a
    # bullet in front of the word is DECORATION THE PRINTER ADDED, and scoring
    # zero on it made `..... TOTAL 4,200.00` not a total line. What the anchor
    # is actually for is unchanged and is tested - a WORD before the word means
    # somebody is talking, and `paid for the lot, TOTAL 4200` still reads.
    re.compile(r"^[^\w\n]*(?:grand\s+|sub\s*)?total\b", re.I | re.M),
    re.compile(r"^[^\w\n]*amount\s+payable\b", re.I | re.M),
)

#: A line that says nothing but what the document IS. Worth the whole threshold
#: on its own: no sentence contains such a line, and nobody types one into a
#: one-line box, so it is not the ambiguous case the two-signal rule hedges.
_HEADER_LINE = re.compile(
    r"^[^\w\n]*(?:tax\s+)?(?:invoice|bill|cash\s+memo|receipt|delivery\s+challan"
    r"|challan|statement\s+of\s+account)[^\w\n]*$",
    re.I | re.M,
)

#: `SUPPLIER: SHARMA TRADERS` - one line, a label, a colon, a value. Two or
#: more of them is a layout; one is a person writing a note with a colon in it.
_LABEL_LINE = re.compile(r"^[ \t]*[A-Za-z][A-Za-z0-9 /.&()-]{0,40}:[ \t]*\S", re.M)

#: TWO signals, not one. One is a word - "TOTAL 4200" at the start of a typed
#: line is a person stating an amount, and refusing it would delete the
#: backend's whole job. Two independent invoice semantics in one text is a
#: layout. Measured: all 20 corpus cases carry six.
_SIGNALS_FOR_AN_INVOICE = 2

#: A rate is not an amount. `18%` in "including 18% GST" must not count as a
#: second number, or every GST sentence the product is built around refuses -
#: which is a fix that refuses everything wearing the right sentence.
_IS_A_RATE = re.compile(r"\s*%")

#: A number written with a currency word, a decimal point or a digit-grouping
#: comma has been written AS MONEY by the person. That is the escape hatch out
#: of the year and phone checks below: `Rs. 2000` and `2000.00` are amounts,
#: `2000` on its own is not clearly one.
_MONEY_MARKED = re.compile(r"(?:rs\.?|₹)\s*$", re.I)

#: Characters that glue a number into a larger token. `GT/0001`, `INV-2026`,
#: `h1`, `12/08/2026`, `10:00`. A number wedged into one of those is a
#: reference, a date or a time, and never a price.
_GLUE_BEFORE = "/-#:"
_GLUE_AFTER = "/-:"

#: THE SAME QUESTION, ASKED SEMANTICALLY. Glue is positional, so it caught
#: `GT/0001` because of the slash and missed `HSN 998311` because of the space -
#: and the second is an invoice field name, in this corpus, worth ₹9,98,311 when
#: read as money. This is added to the glue check and does not replace it: glue
#: is what catches a reference the writer ran together with its prefix, and
#: deleting it would put `GT/0001` back at 100 paise, which is the original
#: defect.
#:
#: The word list is the owner's. A currency symbol between the word and the
#: number defeats it deliberately - nobody writes a reference number with rupees
#: in front of it, so `phone bill Rs 1200` is an amount and `bill 1200` is not.
#:
#: WHAT IT STILL DOES NOT CATCH, stated rather than left to be found: a bare
#: `Ahmedabad 380015`. A six-digit pincode with no word in front of it is
#: indistinguishable from ₹3,80,015, which is an ordinary payment, and any rule
#: that refuses the first refuses the second.
_LABELS_AN_IDENTIFIER = re.compile(
    r"\b(invoice|bill|ref|reference|order|p\.?o|challan|cheque|upi|hsn|sac"
    r"|gstin|pin|batch)\b\s*(?:no\.?|number|#)?\s*[-#:/]*\s*$",
    re.I,
)

#: A month a person writes by name. `August 2026` and `5 Aug` are dates, and a
#: date component is not a candidate for the total. The risk is named: `may` is
#: also an ordinary English word, so `4200 may be wrong` refuses. That is a
#: question asked of the person, never a number, which is the direction this
#: whole module errs in.
_MONTH_WORD = (
    r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?"
    r"|aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
)
_MONTH_BEFORE = re.compile(rf"\b(?:{_MONTH_WORD})\b[\s,]*$", re.I)
_MONTH_AFTER = re.compile(rf"\s*(?:st|nd|rd|th)?[\s,]*\b(?:{_MONTH_WORD})\b", re.I)

#: THE NUMBER HAS TO BE SHAPED LIKE A DATE PART TOO, measured the same day the
#: rule landed: `salary 45000 August` refused, because a month name beside a
#: number was enough on its own. 45000 is not a day of any month - it is a
#: salary, and the month is what it was for. A number is part of a date when it
#: could be a day (1-31 beside the month) or a year (after it), and `August
#: 4200` is neither, so it reads.
_LAST_DAY_OF_ANY_MONTH = 31

#: A unit of quantity. `50 bags of cement` is fifty bags, not fifty rupees, and
#: counting it as a rival to the price refused the sentence outright.
_UNIT_AFTER = re.compile(
    r"\s*(?:kgs?|gms?|grams?|tonnes?|tons?|quintals?|litres?|liters?|ltrs?"
    r"|bags?|boxes|box|cartons?|packets?|packs?|pieces?|pcs|nos|units?|dozens?"
    r"|bundles?|rolls?|sheets?|bottles?|bricks?|sets?|pairs?)\b",
    re.I,
)

#: The window a bare four-digit number is a year rather than an amount in.
_EARLIEST_YEAR = 1900
_LATEST_YEAR = 2100

#: An Indian mobile number is exactly ten digits. Read as rupees it posts
#: ₹98,76,543.21, which is a plausible enough figure that nothing downstream
#: would blink at it.
#:
#: A FRACTION IS NOT A PHONE NUMBER, and that is what separates the two rather
#: than length alone. Nobody writes a mobile number with paise on the end, so
#: `92233720368547.75` - fourteen digits, and the exact value `tests/test_money.py`
#: exists to protect - is money however long it is, while `Rs. 9876543210` is a
#: phone number however it was written.
_PHONE_DIGITS = 10


@dataclass(frozen=True)
class _Number:
    """One number found in the text, with the context that says what it is."""

    raw: str
    money_marked: bool
    glued: bool
    #: The identifier word immediately in front of it, or "".
    labelled: str = ""
    #: Written beside a month name, so it is part of a date.
    dated: bool = False
    #: Followed by a unit of quantity, so it counts things and not rupees.
    counted: bool = False

    @property
    def digits(self) -> int:
        """How many digits, ignoring the grouping commas.

        `len(raw)` counted the commas, so `1,23,45,678` - ₹1.23 crore, eight
        digits - measured ten characters and was refused as a phone number.
        """
        return sum(c.isdigit() for c in self.raw.split(".")[0])


def _glued(text: str, start: int, end: int) -> bool:
    """Is the number at `text[start:end]` wedged into a larger token?

    Looks one character each way, and asks a different question on each side.
    BEFORE: any letter or digit, or one of the separators - `GT/0001`, `h1`,
    `INV-2026`. AFTER: a letter or a separator, but NOT a digit, because the
    match already ran to the end of its own digits.
    """
    head, tail = text[start - 1 : start] if start else "", text[end : end + 1]
    return bool(
        (head and (head.isalnum() or head in _GLUE_BEFORE))
        or (tail and (tail.isalpha() or tail in _GLUE_AFTER))
    )


def _numbers(text: str) -> list[_Number]:
    """Every monetary-looking number in the text, in the order written.

    A percentage is left out because it is a RATE, and counting rates would
    turn "4200 including 18% GST" into two candidates. Everything else is kept,
    including the things that turn out to be references and dates: the count is
    how ambiguity is detected, so filtering first would hide the ambiguity.
    """
    found: list[_Number] = []
    for match in _AMOUNT.finditer(text):
        start, end = match.span(1)
        if _IS_A_RATE.match(text, end):
            continue
        raw = match.group(1)
        before, label = text[:start], _LABELS_AN_IDENTIFIER.search(text[:start])
        found.append(
            _Number(
                raw=raw,
                money_marked=bool(
                    _MONEY_MARKED.search(before) or "." in raw or "," in raw
                ),
                glued=_glued(text, start, end),
                labelled=label.group(1) if label else "",
                dated=_beside_a_month(raw, before, text, end),
                counted=bool(_UNIT_AFTER.match(text, end)),
            )
        )
    return found


def _beside_a_month(raw: str, before: str, text: str, end: int) -> bool:
    """Is this number the day or the year of a date written with a month name?

    Both halves are required, and the second half is the one that was missing:
    a month name NEXT TO a number does not make the number part of the date.
    `5 Aug` is a day and `August 2026` is a year; `salary 45000 August` is what
    the salary was for, and 45000 is a day of nothing.
    """
    if not raw.isdigit():
        return False
    if _MONTH_AFTER.match(text, end):
        return 0 < int(raw) <= _LAST_DAY_OF_ANY_MONTH
    if _MONTH_BEFORE.search(before):
        return (
            0 < int(raw) <= _LAST_DAY_OF_ANY_MONTH
            or _EARLIEST_YEAR <= int(raw) <= _LATEST_YEAR
        )
    return False


def _invoice_shaped(text: str) -> bool:
    """Is this text laid out like a bill? Decided from the text, never the type.

    A bare header line settles it alone. Everything else needs a second signal,
    for the reason `_SIGNALS_FOR_AN_INVOICE` gives: one word can be a person
    talking, and refusing on one word deletes this backend's job.
    """
    if _HEADER_LINE.search(text):
        return True
    signals = sum(1 for mark in _INVOICE_MARKS if mark.search(text))
    if len(_LABEL_LINE.findall(text)) >= 2:
        signals += 1
    return signals >= _SIGNALS_FOR_AN_INVOICE


def _not_an_amount(number: _Number) -> str:
    """Why this number cannot be the amount, or "" when it could be.

    Each check is separately named because each is separately wrong when it is
    missing, and a check with no name cannot have a test that fails without it.

    THE ORDER IS THE FIX, 2026-08-13. `money_marked` used to return "" third,
    which made every check below it unreachable for any number written with a
    currency word, a decimal point or a grouping comma - so `Rs. 9876543210`
    was ₹98,76,543.21 and `9,876,543,210` was the same, both at full
    confidence. A mobile number does not stop being one because somebody wrote
    `Rs.` in front of it. The escape hatch is kept for the YEAR check alone,
    which is the case it was built for: `Rs. 2000` is a rent.
    """
    if number.glued:
        return (
            f"'{number.raw}' is part of an identifier such as an invoice or "
            "reference number, not an amount"
        )
    if number.labelled:
        return (
            f"'{number.raw}' follows '{number.labelled}', which names an "
            "identifier rather than an amount"
        )
    if number.dated:
        return f"'{number.raw}' is part of a date, not an amount"
    if number.counted:
        return f"'{number.raw}' is a quantity of something, not an amount"
    if "." not in number.raw and number.digits >= _PHONE_DIGITS:
        return (
            f"'{number.raw}' is long enough to be a phone number rather than an amount"
        )
    if number.money_marked:
        # Written as money by the person who typed it. `Rs. 2000` is rent.
        return ""
    plain = number.raw
    if len(plain) == 4 and _EARLIEST_YEAR <= int(plain) <= _LATEST_YEAR:
        return (
            f"'{plain}' is a year, not an amount. Write it as money - Rs. 2000 "
            "or 2000.00 - if it is one"
        )
    return ""


def _amount(text: str) -> tuple[int | None, str]:
    """The total in paise, or None and the sentence the person reads.

    RULE 1 refuses invoice-shaped text outright rather than guessing at it.
    RULE 2 allows exactly one CANDIDATE - a number nothing above disqualified.
    RULE 3 - two candidates or more - refuses, because choosing among several
    numbers is the guess that produced the twenty wrong totals.

    RULE 3 COUNTS CANDIDATES AND NOT NUMBERS, 2026-08-13. It used to count
    every number in the text, including the ones RULE 2 would have thrown out
    on sight, and measured over ten ordinary sentences - which is this
    backend's whole job - six refused: a date, a quantity or a reference number
    became a rival to the price. A number that cannot be the total cannot make
    the total ambiguous. Refusing is not a wrong figure, but a backend that
    refuses the sentences it exists for has been deleted rather than fixed.
    """
    if _invoice_shaped(text):
        return None, INVOICE_SHAPED
    found = _numbers(text)
    if not found:
        return None, ""
    candidates = [number for number in found if not _not_an_amount(number)]
    if not candidates:
        # Every number was disqualified. The first one's reason is the one a
        # person can act on, and each sentence names the number it is about, so
        # it stays true when the text held several.
        return None, _not_an_amount(found[0])
    if len(candidates) > 1:
        return None, AMBIGUOUS_NUMBERS
    return _to_paise(candidates[0].raw), ""


def _media_type(mime: str) -> str:
    """`text/plain; charset=utf-8` -> `text/plain`. Nothing is parsed past the `;`.

    A real form sends the charset parameter, and refusing `text/plain;
    charset=utf-8` because of eight trailing characters would be a refusal
    nobody could act on. Splitting on `;` is the whole of it: this reads the
    caller's own declaration, never the bytes.
    """
    return mime.split(";", 1)[0].strip().lower()


def _to_paise(text: str) -> int | None:
    """Exact paise, or None when the string is not an amount in paise.

    A1 / A2, FIXED 2026-08-09. This was `round(float(text.replace(",", "")) *
    100)`, and it was wrong in two different ways at once.

    Binary floating point cannot hold 0.07 rupees, so above roughly
    ₹99,999,999,999,999.99 the returned integer is simply a different number:
    "92233720368547.75" came back as 9223372036854776 paise, one paise adrift.

    And `round` turned sub-paise precision into silence. "10.005" became 1000
    paise, VALID, and posted. `tallyio.paise_from_rupees` REFUSES that same
    string and always has, because rounding invoice arithmetic is how a
    reconciliation breaks three months later. Two components, one rule,
    opposite behaviour, and the lenient one was the one a person's typing
    reached first.

    None rather than an exception: an unreadable amount is a question for the
    person, and `checks.amount_is_positive` already turns a missing total into
    one. Raising here would be a 500 in the web app for a typo.
    """
    try:
        rupees = Decimal(text.replace(",", ""))
    except InvalidOperation:
        return None

    scaled = rupees * _HUNDRED
    paise = int(scaled)
    return paise if scaled == paise else None


class TypedTextExtractor:
    """A person typed a sentence. Pull the fields out of it.

    This is string parsing, not document reading. It never touches an image.

    IT NOW REFUSES WHAT IT CANNOT READ, RATHER THAN GUESSING AT IT
    -------------------------------------------------------------
    "It never touches an image" was a promise in a docstring, and the
    parameter that could have kept it was named `_mime` and discarded. Handed a
    PDF, a PNG or a JPEG, this class decoded the container with
    `errors="replace"` and ran a money regex over the wreckage. The numbers it
    returned are recorded beside `TYPED_TEXT_MIME` above; each arrived with
    `per_field_source["total_paise"] == "typed_text"`, so nothing downstream
    could tell the invented total from a read one.

    A second, quieter case, measured the same day on real published bytes:
    `"paid Café Ltd 4200 for supplies"` encoded cp1252 came back with
    `party == "Caf"`, sourced `typed_text`. The é became U+FFFD, `_PARTY`
    stopped at it, and a TRUNCATED supplier name went on to
    `propose_account`, where a name that does not match history is a new
    vendor. A wrong party is not a missing party.

    Both are closed the same way and for the same reason: this backend states
    what it can read, and everything else is an explicit `not_found` carrying
    the sentence saying why. Refusing is not reading — no container is parsed,
    no byte is interpreted, and adding a real reader is still somebody else's
    job behind the same Protocol.
    """

    name = "typed_text"

    def _refuse(self, reason: str) -> ExtractedRecord:
        """Every field `not_found`, with this reason on each of them.

        `UnavailableExtractor` and not a second record built here, for the
        reason `registry.GuardedExtractor.outage` already gives: two places
        that build this shape is how one of them ends up without a reason on
        it, which is a silent blank wearing a label.
        """
        return UnavailableExtractor(reason, name=self.name).extract(b"", "")

    def extract(self, data: bytes, mime: str) -> ExtractedRecord:
        declared = _media_type(mime)
        if declared != TYPED_TEXT_MIME:
            return self._refuse(
                f"{self.name} reads a sentence a person typed and was handed "
                f"{declared or 'no media type'}; reading a document is the "
                f"third-party backend's job and no backend is selected"
            )

        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            # Strict, not `errors="replace"`. Replacing produced a party name
            # with the wrong letters in it and no way for a later stage to
            # know. A caller may declare text/plain and send anything.
            return self._refuse(
                f"{self.name} was handed {declared} that is not UTF-8 text "
                f"(byte {exc.start} is not valid UTF-8), and guessing at the "
                f"missing letters would change a supplier's name"
            )

        src: dict[str, str] = {}

        total, why = _amount(text)
        # A refusal is a refusal: no value, and the sentence saying why on the
        # row beside it. `ExtractedRecord` has no confidence column, and it
        # needs none here - a field with no value cannot carry a score, which
        # is exactly how `textlayer._field` already expresses 0.0. What must
        # never happen is the third thing: a number with a low score on it.
        if total is not None:
            src["total_paise"] = self.name
        else:
            src["total_paise"] = f"{NOT_FOUND}: {why}" if why else NOT_FOUND

        tax = None
        m = _GST_PCT.search(text)
        if m and total is not None:
            # Decimal, not float. 18% of ₹1,180 is exactly ₹180; in binary
            # floating point it is 179.99999999999997, and `round` hides that
            # until the amount where it does not - at which point the tax and
            # the net no longer sum to the total the person typed.
            pct = Decimal(m.group(1) or m.group(2))
            # Amount typed is inclusive of GST: tax = total * pct / (100 + pct)
            tax = int((Decimal(total) * pct / (_HUNDRED + pct)).to_integral_value())
        src["tax_paise"] = self.name if tax is not None else NOT_FOUND

        pm = _PARTY.search(text)
        party = pm.group(1).strip() if pm else None
        src["party"] = self.name if party else NOT_FOUND

        src["date"] = NOT_FOUND  # typed text rarely carries one; never guessed

        return ExtractedRecord(
            date=None,
            party=party,
            total_paise=total,
            tax_paise=tax,
            raw_text=text,
            backend=self.name,
            per_field_source=src,
        )


#: What the stub says about a field it was never handed. `NOT_FOUND` plus a
#: reason, in the shape `UnavailableExtractor` already uses, so one rule reads
#: both: a source that starts with `not_found` carries no value, and the text
#: after the colon says why. Never a bare `not_found`.
STUB_NOT_FOUND = (
    f"{NOT_FOUND}: no production reader is configured, "
    "so nothing was read from this document"
)


class StubExtractor:
    """Fixed answers. Lets the whole pipeline be tested with no real reader.

    Slice 5 wires this in before any third-party backend is chosen.
    """

    name = "stub"

    def __init__(
        self,
        date: datetime.date | None = None,
        party: str | None = None,
        total_paise: int | None = None,
        tax_paise: int | None = None,
    ) -> None:
        self.date = date
        self.party = party
        self.total_paise = total_paise
        self.tax_paise = tax_paise

    def extract(self, data: bytes, _mime: str) -> ExtractedRecord:
        # Named fields rather than a dict, so each keeps its own type. A dict
        # collapses them into one union and the record can no longer be built
        # without the type checker guessing.
        supplied: dict[str, object] = {
            "date": self.date,
            "party": self.party,
            "total_paise": self.total_paise,
            "tax_paise": self.tax_paise,
        }
        # A bare `not_found` says nothing a person could act on. EXIT 2 asks for
        # every unread field to be explicit not_found WITH A REASON, and this
        # stub is the backend a JPG meets while no production reader is
        # selected (owner decision Q4 = B). Without the reason, "we have no
        # reader" and "the reader looked and found nothing" are the same string
        # in the audit trail, and those are different facts about the document.
        src = {
            k: (self.name if v is not None else STUB_NOT_FOUND)
            for k, v in supplied.items()
        }
        return ExtractedRecord(
            date=self.date,
            party=self.party,
            total_paise=self.total_paise,
            tax_paise=self.tax_paise,
            raw_text=data.decode("utf-8", errors="replace"),
            backend=self.name,
            per_field_source=src,
        )


class UnavailableExtractor:
    """The backend is down. #15.7: every field comes back not_found with a
    stated reason, and the system carries on so the person can type instead.

    `name` is settable, 2026-08-10. This class is now the ONE place that builds
    an outage record — `ServiceExtractor` hands its own failures here rather
    than restating the shape — and a row that cannot say WHICH backend was down
    is not usable as evidence about any of them. The default is unchanged, so
    every existing caller reads exactly as before.
    """

    name = "unavailable"

    def __init__(
        self, reason: str = "backend unreachable", *, name: str = "unavailable"
    ) -> None:
        self.reason = reason
        self.name = name

    def extract(self, _data: bytes, _mime: str) -> ExtractedRecord:
        return ExtractedRecord(
            date=None,
            party=None,
            total_paise=None,
            tax_paise=None,
            raw_text="",
            backend=self.name,
            per_field_source=dict.fromkeys(
                ExtractedRecord.FIELDS, f"{NOT_FOUND}: {self.reason}"
            ),
        )
