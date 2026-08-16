"""One reading in, one versioned result out. Changes no reading behaviour.

WHAT THIS FILE IS FOR
----------------------
`parse.py` finds fields, `validate.py` checks arithmetic and `status.py` names
outcomes. Something has to run them in order, decide which of the ten statuses
this document reached, and hand back one record. That is this file and it is
all this file does.

WHY IT TAKES PRIMITIVES AND NOT A READER'S TYPE
------------------------------------------------
It is handed characters and word scores. It does not import a reader, does not
call one, and cannot change what one returns - which is the whole of the
promise that wiring this in alters no reading behaviour. A caller holding
`extract/freeocr.py`'s words converts them in one line:

    Reading.from_words(
        [[Word(w.text, w.confidence) for w in line] for line in lines],
        source="free_ocr",
    )

and a caller holding a text layer's characters uses `Reading.from_text` with
the confidence that tier is entitled to state. Nothing else is needed, and
nothing in `accountant/extract/` has to change for either.

WHY THE ORDER OF THE CHECKS IS THE ORDER IT IS
-----------------------------------------------
Each step can only be asked once the one before it has an answer:

    no characters at all      -> OCR_FAILED. An engine or image fact. Asking
                                 "is this an invoice" of nothing is meaningless.
    characters, not language  -> UNREADABLE. Classification on noise produces
                                 a verdict about noise.
    language, not a bill      -> NON_INVOICE or UNKNOWN_DOCUMENT.
    a bill, barely any text   -> INVOICE_LOW_TEXT. Re-scan, not re-parse.
    a bill, a field missing   -> INVOICE_MISSING_FIELDS. OURS to fix.
    a bill, figures disagree  -> INVOICE_VALIDATION_FAILED.
    otherwise                 -> READY_FOR_REVIEW, and a person still looks.

MISSING FIELDS OUTRANK A FAILED LAW, and that is a choice worth defending. A
bill with no total makes half the laws unevaluable, so the failures it produces
are downstream of the gap rather than separate from it. Telling somebody "the
figures do not add up" when the real answer is "we never found the total" sends
them to check arithmetic that was never done.

NOTHING HERE POSTS, WRITES, OR REACHES TALLY. No import of `accountant.tallyio`
exists anywhere in this package and a test reads the import graph to prove it.
"""

from __future__ import annotations

import datetime
import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Final

from accountant.cage.conservation import Verdict
from accountant.cage.decision import ASK_FLOOR
from accountant.invoice import parse
from accountant.invoice.fields import Method, ReadField, Where, read_as, unread
from accountant.invoice.parse import Column, Reading, Side
from accountant.invoice.result import (
    ENGINE_NOT_STATED,
    DocumentMeta,
    ExtractionResult,
    InvoiceIdentity,
    Item,
    Party,
    Totals,
    paise_of,
)
from accountant.invoice.status import SAID, DocumentStatus
from accountant.invoice.validate import EXACTLY, Figures, Finding, Law, Tolerance
from accountant.invoice.validate import run as run_laws
from accountant.invoicelike import looks_like_a_bill
from accountant.labels import NET_LABELS, TOTAL_LABELS, Printing
from accountant.rules.place_of_supply import gstin_state_code

#: Labels a supplier prints an address under. Deliberately short: an address
#: found by POSITION rather than by a label is an address this file is not
#: willing to claim, because the line under a party name is the address on most
#: bills and is the second half of the name on the rest.
ADDRESS_LABELS: Final[tuple[str, ...]] = ("ADDRESS",)

#: What a bill calls the figure before its tax lines. Only the two spellings
#: `labels.NET_LABELS` does not already carry as one word.
SUBTOTAL_LABELS: Final[tuple[str, ...]] = ("SUB TOTAL", "SUBTOTAL")

#: The labels a bill prints its date under. `DATE` is last, because
#: `amounts_for` and `values_for` both take the first label that matches and
#: `INVOICE DATE` has to be reported as itself.
DATE_LABELS: Final[tuple[str, ...]] = ("INVOICE DATE", "BILL DATE", "DATED", "DATE")


@dataclass(frozen=True)
class Thresholds:
    """The three numbers this file compares against, in one place.

    **TWO OF THESE THREE ARE NOT MEASURED NUMBERS.** They are shape arguments,
    and they are gathered here so that is impossible to miss and so a caller can
    replace them without editing code. `ARCHITECTURE.md` forbids tuning a
    threshold to make a metric pass; the way to move any of these is to measure
    it first, and `docs/INVOICE_EXTRACTION_FRAMEWORK.md` says what measuring it
    would take.

    `enough_characters` - below this, a document that looks like a bill is
    called `INVOICE_LOW_TEXT` rather than blamed on the parser. For scale: the
    MEASURED median reading of a JPG in `data/` is 107.5 characters, which is
    far too thin to be a whole invoice, and the text layer of a real one runs to
    several hundred. 200 sits between those two observations. It is NOT derived
    from either and nothing has measured it.

    `legible_share` - the share of non-blank characters that must be letters or
    digits before the text counts as language at all. Noise off a photograph of
    something that is not a page comes back mostly punctuation. Also unmeasured.

    `low_confidence_below` - `cage/decision.ASK_FLOOR`, which is the OWNER'S
    number and the only one of the three that is. Imported rather than copied:
    a second 0.70 in this repository is a second place for it to drift.
    """

    enough_characters: int = 200
    legible_share: float = 0.5
    low_confidence_below: float = ASK_FLOOR


#: What a caller gets when it states nothing.
DEFAULT_THRESHOLDS: Final = Thresholds()


def legible_share(text: str) -> float:
    """The share of non-blank characters that are letters or digits.

    `0.0` for an empty string, which never reaches here - `OCR_FAILED` is
    decided before this is asked - but is returned rather than raising, because
    a helper that raises on the empty case is a helper somebody has to guard.
    """
    solid = [character for character in text if not character.isspace()]
    if not solid:
        return 0.0
    return sum(1 for character in solid if character.isalnum()) / len(solid)


# =============================================================================
# the parties
# =============================================================================


def _is_a_heading(line: str) -> bool:
    upper = line.upper()
    return any(
        name in upper for name in (*parse.SUPPLIER_SECTION, *parse.BUYER_SECTION)
    )


def _below_a_heading(
    reading: Reading, headings: tuple[str, ...], *, printing: Printing
) -> ReadField:
    """The first usable line under a bare heading, or nothing.

    Takes the first following line that is not blank, is not another heading,
    and carries no registration number. Its failure mode is written down rather
    than discovered: a bill printing its address above its name reads an address
    as a name, and nothing here can tell. `Method.BELOW_A_HEADING` on the field
    is what carries that warning to whoever reads the record.
    """
    del printing  # positional, so the separator tolerance has nothing to do
    for index, line in enumerate(reading.lines):
        if not any(name in line.upper() for name in headings):
            continue
        for below in range(index + 1, len(reading.lines)):
            whole = reading.lines[below]
            candidate = whole.strip()
            if not candidate:
                continue
            if _is_a_heading(candidate):
                break
            if parse.valid_gstin(candidate) is not None:
                continue
            start = whole.index(candidate)
            where = Where(line=below, start=start, end=start + len(candidate))
            return read_as(
                candidate,
                confidence=parse.score_of(reading, where, format_valid=True),
                source=reading.source,
                method=Method.BELOW_A_HEADING,
                printed=candidate,
                where=where,
            )
        break
    return unread(reading.source)


def party_name(
    reading: Reading, headings: tuple[str, ...], what: str, *, printing: Printing
) -> ReadField:
    """The party's name, from a label if there is one and a line if there is not.

    TWO WAYS, AND THE SECOND ONE IS WEAKER AND SAYS SO. `Supplier: ACME LTD` is
    the document stating the name. A bare `Bill To:` with the name beneath it is
    the document stating where to LOOK, and this records that difference as
    `Method.BELOW_A_HEADING` rather than flattening both into one score.
    """
    labelled = parse.value_under(reading, headings, what, printing=printing)
    if labelled.read:
        return labelled
    return _below_a_heading(reading, headings, printing=printing)


def state_code(reading: Reading, gstin: ReadField) -> ReadField:
    """The state a registration number belongs to, from its first two digits.

    WORKED OUT and labelled as such. `place_of_supply.gstin_state_code` is the
    one place that reads those two digits, so this asks it rather than slicing
    the string here.
    """
    value = gstin.value
    code = gstin_state_code(value) if isinstance(value, str) else None
    if code is None:
        return unread(reading.source)
    return read_as(
        code,
        confidence=gstin.confidence,
        source=reading.source,
        method=Method.WORKED_OUT,
        printed=gstin.printed,
        where=gstin.where,
    )


def party(
    reading: Reading,
    found: tuple[parse.FoundGstin, ...],
    *,
    side: Side,
    headings: tuple[str, ...],
    what: str,
    printing: Printing,
) -> Party:
    gstin = parse.gstin_for(reading, found, side)
    return Party(
        name=party_name(reading, headings, what, printing=printing),
        gstin=gstin,
        state_code=state_code(reading, gstin),
        address=parse.value_under(
            reading, ADDRESS_LABELS, "an address", printing=printing
        ),
    )


# =============================================================================
# what names the bill
# =============================================================================


def shaped(base: ReadField, reading: Reading, shape: re.Pattern[str]) -> ReadField:
    """`base` if its characters match `shape`, unread if they do not.

    A HARD MULTIPLIER, like every other format check here: a sixty-three
    character string is not a low-confidence invoice reference number, it is not
    one. `cage/confidence.py` makes the same argument about a date that will not
    parse.
    """
    if not base.read:
        return base
    return base if shape.match(str(base.value).strip()) else unread(reading.source)


def _identity(reading: Reading, *, printing: Printing) -> InvoiceIdentity:
    printed_date = parse.value_under(
        reading, DATE_LABELS, "its date", printing=printing
    )
    return InvoiceIdentity(
        number=parse.value_under(
            reading, parse.INVOICE_NUMBER_LABELS, "its number", printing=printing
        ),
        date=parse.converted(
            printed_date,
            parse.date_from(str(printed_date.value)) if printed_date.read else None,
            reading,
        ),
        po_number=parse.value_under(
            reading, parse.PO_NUMBER_LABELS, "an order number", printing=printing
        ),
        irn=shaped(
            parse.value_under(
                reading,
                parse.IRN_LABELS,
                "an invoice reference number",
                printing=printing,
            ),
            reading,
            parse.IRN_SHAPE,
        ),
        eway_bill=shaped(
            parse.value_under(
                reading,
                parse.EWAY_BILL_LABELS,
                "an e-way bill number",
                printing=printing,
            ),
            reading,
            parse.EWAY_BILL_SHAPE,
        ),
        place_of_supply=parse.value_under(
            reading,
            parse.PLACE_OF_SUPPLY_LABELS,
            "a place of supply",
            printing=printing,
        ),
    )


# =============================================================================
# what the bill comes to
# =============================================================================


def worked_out_tax(reading: Reading, parts: Sequence[ReadField]) -> ReadField:
    """The tax lines added up, when the bill states no total of its own.

    THE SCORE IS THE WEAKEST PART AND NOT THEIR MEAN. A total worked out from a
    figure read at 0.98 and one read at 0.40 is worth 0.40 - the second is in
    the answer at full weight, and averaging would hide it. `cage/wall.py`
    argues this at length about the same arithmetic.

    THE LAW THAT WOULD CHECK THIS REPORTS INDETERMINATE, and `Totals` carries
    the flag that makes that happen. A sum checked against itself is not a
    check.
    """
    read = [one for one in parts if one.read]
    if not read:
        return unread(reading.source)
    return read_as(
        sum(paise_of(one) or 0 for one in read),
        confidence=min(one.confidence for one in read),
        source=reading.source,
        method=Method.WORKED_OUT,
        printed="",
    )


def _totals(reading: Reading, *, printing: Printing) -> Totals:
    cgst = parse.amount_under(reading, parse.CGST_LABELS, "its CGST")
    sgst = parse.amount_under(reading, parse.SGST_LABELS, "its SGST")
    igst = parse.amount_under(reading, parse.IGST_LABELS, "its IGST")
    cess = parse.amount_under(reading, parse.CESS_LABELS, "its cess")
    stated = parse.amount_under(reading, parse.TOTAL_TAX_LABELS, "its total tax")
    return Totals(
        subtotal=parse.amount_under(reading, SUBTOTAL_LABELS, "its subtotal"),
        taxable=parse.amount_under(reading, NET_LABELS, "its taxable value"),
        cgst=cgst,
        sgst=sgst,
        igst=igst,
        cess=cess,
        total_tax=(
            stated if stated.read else worked_out_tax(reading, (cgst, sgst, igst, cess))
        ),
        round_off=parse.amount_under(reading, parse.ROUND_OFF_LABELS, "its round-off"),
        grand_total=parse.amount_under(reading, TOTAL_LABELS, "its total"),
        amount_in_words=parse.value_under(
            reading,
            parse.AMOUNT_IN_WORDS_LABELS,
            "the amount in words",
            printing=printing,
        ),
        total_tax_was_stated=stated.read,
    )


def _item(row: dict[Column, ReadField], source: str) -> Item:
    """One table row as an `Item`. A column the header did not name is unread."""

    def column(which: Column) -> ReadField:
        return row.get(which, unread(source))

    return Item(
        description=column(Column.DESCRIPTION),
        hsn_sac=column(Column.HSN_SAC),
        quantity=column(Column.QUANTITY),
        unit=column(Column.UNIT),
        rate=column(Column.RATE),
        discount=column(Column.DISCOUNT),
        taxable=column(Column.TAXABLE),
        gst_rate=column(Column.GST_RATE),
        cgst=column(Column.CGST),
        sgst=column(Column.SGST),
        igst=column(Column.IGST),
        cess=column(Column.CESS),
        line_total=column(Column.LINE_TOTAL),
    )


# =============================================================================
# putting the figures in front of the laws
# =============================================================================


@dataclass(frozen=True)
class _Parts:
    """The five groups of fields, before the laws have seen any of them."""

    supplier: Party
    buyer: Party
    invoice: InvoiceIdentity
    totals: Totals
    items: tuple[Item, ...]


def _whole_number(one: ReadField) -> int | None:
    """A field's value as a plain `int`, refusing `bool` for the usual reason."""
    value = one.value
    return None if isinstance(value, bool) or not isinstance(value, int) else value


def _line_taxable(item: Item) -> ReadField:
    """A line's taxable value, or its total when no taxable column existed.

    A bill with no tax columns in its table prints one money column and calls it
    `Amount`. That figure IS the taxable value on such a bill, because there is
    no tax in the row to have been added to it. A bill printing BOTH is not
    guessed at - the taxable column wins, because the document named it.
    """
    return item.taxable if item.taxable.read else item.line_total


def mandatory_found(
    *, supplier: Party, invoice: InvoiceIdentity, totals: Totals
) -> tuple[str, ...]:
    """Which of `validate.MANDATORY` this document actually supplied.

    THE NAMES ARE THE ONES `validate.MANDATORY` USES, not the ones this record
    uses, and the two lists have to agree. A test asserts that rather than
    trusting it: a rename on one side would silently make a mandatory field
    permanently missing, and every bill would fail for a reason nobody could
    find.

    TAKES THE THREE GROUPS AND NOT A WHOLE RECORD, so a test can hand it three
    hand-built fields with no document in sight - and so that adding a field to
    the record cannot silently change what is mandatory.
    """
    present: list[str] = []
    if supplier.name.read or supplier.gstin.read:
        present.append("supplier")
    if invoice.number.read:
        present.append("invoice_number")
    if invoice.date.read:
        present.append("invoice_date")
    if totals.grand_total.read:
        present.append("grand_total")
    return tuple(present)


def _text_of(one: ReadField) -> str | None:
    value = one.value
    return value if isinstance(value, str) else None


def _figures(parts: _Parts) -> Figures:
    items = parts.items
    gstin = _text_of(parts.supplier.gstin)
    return Figures(
        line_quantity_milli=tuple(_whole_number(one.quantity) for one in items),
        line_rate_paise=tuple(paise_of(one.rate) for one in items),
        line_discount_paise=tuple(paise_of(one.discount) for one in items),
        line_taxable_paise=(
            None if not items else tuple(paise_of(_line_taxable(one)) for one in items)
        ),
        taxable_paise=paise_of(parts.totals.taxable),
        cgst_paise=paise_of(parts.totals.cgst),
        sgst_paise=paise_of(parts.totals.sgst),
        igst_paise=paise_of(parts.totals.igst),
        cess_paise=paise_of(parts.totals.cess),
        total_tax_paise=paise_of(parts.totals.total_tax),
        total_tax_was_stated=parts.totals.total_tax_was_stated,
        round_off_paise=paise_of(parts.totals.round_off),
        grand_total_paise=paise_of(parts.totals.grand_total),
        fields_read=mandatory_found(
            supplier=parts.supplier, invoice=parts.invoice, totals=parts.totals
        ),
        supplier_key=gstin if gstin else _text_of(parts.supplier.name),
        invoice_number=_text_of(parts.invoice.number),
    )


# =============================================================================
# the whole walk
# =============================================================================


def _status_from(findings: Sequence[Finding]) -> DocumentStatus:
    """`INVOICE_MISSING_FIELDS`, `INVOICE_VALIDATION_FAILED`, or ready.

    The mandatory-field law is looked at FIRST and on its own. See the module
    docstring: telling somebody the figures disagree when the real answer is
    that the total was never found sends them to check arithmetic nobody did.
    """
    for one in findings:
        if one.law is Law.MANDATORY_FIELDS and one.verdict is Verdict.FAIL:
            return DocumentStatus.INVOICE_MISSING_FIELDS
    if any(one.verdict is Verdict.FAIL for one in findings):
        return DocumentStatus.INVOICE_VALIDATION_FAILED
    return DocumentStatus.READY_FOR_REVIEW


def _reasons(
    status: DocumentStatus,
    findings: Sequence[Finding],
    lowest: float,
    thresholds: Thresholds,
) -> tuple[str, ...]:
    """Every sentence a person needs, in the order they need them.

    The status first, because it is the headline. Then what went wrong, then
    what could not be checked - and `INDETERMINATE` is in here rather than
    silently dropped, because "we could not check this" is the sentence whose
    absence lets an unchecked bill look like a checked one.
    """
    reasons = [SAID[status]]
    reasons.extend(one.said for one in findings if one.verdict is Verdict.FAIL)
    reasons.extend(one.said for one in findings if one.verdict is Verdict.INDETERMINATE)
    if 0.0 < lowest < thresholds.low_confidence_below:
        reasons.append(
            "the weakest figure read off this bill is below the level this "
            "system will act on without asking, so a person has to check it."
        )
    return tuple(reasons)


def _stopped_early(
    text: str, *, bill_like: bool, any_signal: bool, thresholds: Thresholds
) -> DocumentStatus | None:
    """The status this document stopped at before any field was looked for.

    `None` means it did not stop and the fields are worth looking for.
    """
    if legible_share(text) < thresholds.legible_share:
        return DocumentStatus.UNREADABLE
    if not bill_like:
        if any_signal:
            return DocumentStatus.NON_INVOICE
        return DocumentStatus.UNKNOWN_DOCUMENT
    if len(text.strip()) < thresholds.enough_characters:
        return DocumentStatus.INVOICE_LOW_TEXT
    return None


def _meta(
    file_hash: str, page_count: int, engine: str, status: DocumentStatus
) -> DocumentMeta:
    return DocumentMeta(
        file_hash=file_hash, page_count=page_count, engine=engine, status=status
    )


def describe(
    reading: Reading,
    *,
    printing: Printing,
    file_hash: str,
    page_count: int = 1,
    engine: str = ENGINE_NOT_STATED,
    tolerance: Tolerance = EXACTLY,
    thresholds: Thresholds = DEFAULT_THRESHOLDS,
    already_seen: frozenset[tuple[str, str]] = frozenset(),
) -> ExtractionResult:
    """One document, read as far as it can be read, with a status on it.

    `printing` IS KEYWORD-ONLY AND HAS NO DEFAULT, following
    `labels.py::values_for`. A positional argument would be one `True`
    away from turning a PDF into a photograph at a call site nobody re-reads,
    and a default would mean a caller who never thought about the question
    still got an answer to it.

    DETERMINISTIC. No clock is read, no random number is drawn, no file is
    opened and no network is touched. The same reading and the same settings
    produce the same record, field for field and sentence for sentence, and
    `tests/test_invoice_framework.py` asserts that by running it twice and
    comparing the two.
    """
    text = reading.text
    if not text.strip():
        return ExtractionResult.nothing_was_read(
            document=_meta(file_hash, page_count, engine, DocumentStatus.OCR_FAILED),
            source=reading.source,
            raw_text=text,
        )

    verdict = looks_like_a_bill(text)
    early = _stopped_early(
        text,
        bill_like=verdict.looks_like_a_bill,
        any_signal=bool(verdict.signals),
        thresholds=thresholds,
    )
    if early is not None:
        return ExtractionResult.nothing_was_read(
            document=_meta(file_hash, page_count, engine, early),
            source=reading.source,
            raw_text=text,
            signals=verdict.signals,
            review_reasons=(SAID[early], verdict.said),
        )

    found = parse.gstins_on(reading)
    parts = _Parts(
        supplier=party(
            reading,
            found,
            side=Side.SUPPLIER,
            headings=parse.SUPPLIER_SECTION,
            what="its supplier",
            printing=printing,
        ),
        buyer=party(
            reading,
            found,
            side=Side.BUYER,
            headings=parse.BUYER_SECTION,
            what="its buyer",
            printing=printing,
        ),
        invoice=_identity(reading, printing=printing),
        totals=_totals(reading, printing=printing),
        items=tuple(_item(row, reading.source) for row in parse.read_rows(reading)),
    )
    findings = run_laws(_figures(parts), tolerance=tolerance, already_seen=already_seen)
    status = _status_from(findings)
    result = ExtractionResult(
        document=_meta(file_hash, page_count, engine, status),
        supplier=parts.supplier,
        buyer=parts.buyer,
        invoice=parts.invoice,
        totals=parts.totals,
        items=parts.items,
        findings=findings,
        raw_text=text,
        said=SAID[status],
        signals=verdict.signals,
    )
    return replace(
        result,
        review_reasons=_reasons(status, findings, result.lowest_confidence, thresholds),
    )


def supplier_key_of(result: ExtractionResult) -> str | None:
    """What this bill is filed under for the repeat check, or `None`.

    The registration number when there is one, because two suppliers can share a
    trading name and no two share a GSTIN. The name only when there is not.
    """
    gstin = _text_of(result.supplier.gstin)
    return gstin if gstin else _text_of(result.supplier.name)


def invoice_number_of(result: ExtractionResult) -> str | None:
    return _text_of(result.invoice.number)


def invoice_date_of(result: ExtractionResult) -> datetime.date | None:
    return result.invoice.invoice_date
