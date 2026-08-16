"""What came off one document, versioned, frozen, and honest about the gaps.

WHY THERE IS A VERSION ON IT
------------------------------
`SCHEMA_VERSION` is on the record itself and not in a comment. A result stored
today and read back after this package changes shape has to say which shape it
is, or the reader guesses - and a reader guessing at a field layout is how an
amount ends up in a tax column. It is a string and not a number because a
version is an identity, not a quantity: nothing should ever be tempted to
compare two of them with `<`.

WHY EVERY FIELD IS A `ReadField` AND NOT A VALUE
-------------------------------------------------
A record of bare values cannot tell "the tax was zero" from "the tax was never
read", and that single confusion is what `cage/conservation.py` was built to
refuse. Every field here carries its value, its confidence, how it was found
and where - so absence is a first-class fact rather than a missing key.

THE AVERAGE IS FOR READING. THE MINIMUM IS FOR DECIDING.
---------------------------------------------------------
`average_confidence` is on this record because a person looking at a batch
wants one number per document. It must never authorise anything.

`cage/wall.py` states why, and it is not a preference: a bill is not uniformly
legible. A clean printed total beside a smudged letterhead averages to
something that describes neither, and one misread digit ruins an amount. So
`lowest_confidence` is the number a decision uses, it is the one this record
puts in `review_reasons`, and the two are separate properties so nobody can
reach for the wrong one by accident.

NOTHING HERE POSTS ANYTHING
----------------------------
This type has no method that writes, saves, converts itself into a `Voucher` or
reaches Tally, and `tests/test_invoice_framework.py` asserts that it never
grows one - the same guard `cage/wall.py::Observation` carries, for the same
reason. The only road from here to somebody's books runs through the decision
layer and the write door, and neither of them is in this package.
"""

from __future__ import annotations

import datetime
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final

from accountant.cage.conservation import Verdict
from accountant.invoice.fields import ReadField, unread
from accountant.invoice.status import SAID, DocumentStatus
from accountant.invoice.validate import Finding

#: The shape of this record. Bumped when a field is added, removed or changes
#: meaning - never when a docstring changes.
SCHEMA_VERSION: Final = "invoice-extraction-1"

#: What goes in `engine` when the caller did not say which reader ran. Named
#: rather than left as an empty string, so a stored record answers the question
#: "which reader produced this" with a word instead of a blank that could mean
#: anything. Matches `observability.NOT_MEASURED` in spirit: the absence of a
#: measurement is itself recorded.
ENGINE_NOT_STATED: Final = "engine_not_stated"


@dataclass(frozen=True)
class DocumentMeta:
    """What is true about the FILE, as opposed to the bill printed on it.

    `file_hash` identifies these exact bytes and reads nothing inside them.
    `extract/service.py::document_key` produces one the same way, and a caller
    holding that string may put it straight in here.
    """

    file_hash: str
    page_count: int
    engine: str
    status: DocumentStatus
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not self.file_hash.strip():
            raise ValueError(
                "a document must be identified by its bytes. Without that, two "
                "documents' results are one result and neither can be found."
            )
        if self.page_count < 0:
            raise ValueError(
                f"a document cannot have {self.page_count} pages. Zero means "
                "nothing was opened, which is a real answer; negative is not."
            )
        if not self.engine.strip():
            raise ValueError(
                f"a document must say which reader produced it, or "
                f"{ENGINE_NOT_STATED!r}. A blank cannot be told from a reader "
                "whose name nobody recorded."
            )


@dataclass(frozen=True)
class Party:
    """One side of the bill. Every field may be unread and says so."""

    name: ReadField
    gstin: ReadField
    state_code: ReadField
    address: ReadField

    @classmethod
    def nothing(cls, source: str) -> Party:
        """A party nothing was read about, from a named reader."""
        return cls(
            name=unread(source),
            gstin=unread(source),
            state_code=unread(source),
            address=unread(source),
        )

    def named(self) -> tuple[tuple[str, ReadField], ...]:
        return (
            ("name", self.name),
            ("gstin", self.gstin),
            ("state_code", self.state_code),
            ("address", self.address),
        )


@dataclass(frozen=True)
class InvoiceIdentity:
    """What names this particular bill, as distinct from what it charges."""

    number: ReadField
    date: ReadField
    po_number: ReadField
    irn: ReadField
    eway_bill: ReadField
    place_of_supply: ReadField

    @classmethod
    def nothing(cls, source: str) -> InvoiceIdentity:
        return cls(
            number=unread(source),
            date=unread(source),
            po_number=unread(source),
            irn=unread(source),
            eway_bill=unread(source),
            place_of_supply=unread(source),
        )

    def named(self) -> tuple[tuple[str, ReadField], ...]:
        return (
            ("invoice_number", self.number),
            ("invoice_date", self.date),
            ("po_number", self.po_number),
            ("irn", self.irn),
            ("eway_bill", self.eway_bill),
            ("place_of_supply", self.place_of_supply),
        )

    @property
    def invoice_date(self) -> datetime.date | None:
        """The date, when one was read AND it is a date. Otherwise `None`.

        The type check is not paranoia: `ReadField.value` is `object`, because
        a field holds whatever its column holds, and a caller that assumed
        `datetime.date` would get a `TypeError` at whatever line finally did
        arithmetic on it rather than here where the answer is `None`.
        """
        value = self.date.value
        return value if isinstance(value, datetime.date) else None


@dataclass(frozen=True)
class Item:
    """One row of the bill's table. Every column may be unread and says so."""

    description: ReadField
    hsn_sac: ReadField
    quantity: ReadField
    unit: ReadField
    rate: ReadField
    discount: ReadField
    taxable: ReadField
    gst_rate: ReadField
    cgst: ReadField
    sgst: ReadField
    igst: ReadField
    cess: ReadField
    line_total: ReadField

    def named(self) -> tuple[tuple[str, ReadField], ...]:
        return (
            ("description", self.description),
            ("hsn_sac", self.hsn_sac),
            ("quantity", self.quantity),
            ("unit", self.unit),
            ("rate", self.rate),
            ("discount", self.discount),
            ("taxable", self.taxable),
            ("gst_rate", self.gst_rate),
            ("cgst", self.cgst),
            ("sgst", self.sgst),
            ("igst", self.igst),
            ("cess", self.cess),
            ("line_total", self.line_total),
        )


@dataclass(frozen=True)
class Totals:
    """What the bill says it comes to, before anything checked whether it does.

    `total_tax` may be WORKED OUT rather than read - most bills print the parts
    and no total - and `total_tax_was_stated` is what says which happened. That
    flag is not decoration: without it the law that checks the parts against the
    total is checking a number against itself. `validate.py` refuses to run
    that check when the flag is false, and says so in words.
    """

    subtotal: ReadField
    taxable: ReadField
    cgst: ReadField
    sgst: ReadField
    igst: ReadField
    cess: ReadField
    total_tax: ReadField
    round_off: ReadField
    grand_total: ReadField
    amount_in_words: ReadField
    total_tax_was_stated: bool = False

    @classmethod
    def nothing(cls, source: str) -> Totals:
        return cls(
            subtotal=unread(source),
            taxable=unread(source),
            cgst=unread(source),
            sgst=unread(source),
            igst=unread(source),
            cess=unread(source),
            total_tax=unread(source),
            round_off=unread(source),
            grand_total=unread(source),
            amount_in_words=unread(source),
        )

    def named(self) -> tuple[tuple[str, ReadField], ...]:
        return (
            ("subtotal", self.subtotal),
            ("taxable", self.taxable),
            ("cgst", self.cgst),
            ("sgst", self.sgst),
            ("igst", self.igst),
            ("cess", self.cess),
            ("total_tax", self.total_tax),
            ("round_off", self.round_off),
            ("grand_total", self.grand_total),
            ("amount_in_words", self.amount_in_words),
        )


def paise_of(one: ReadField) -> int | None:
    """A field's value as whole paise, or `None` because it is not that.

    A TYPE CHECK AND NOT A CAST. `bool` is an `int` in Python and `True == 1`,
    so a flag that reached an amount field would otherwise balance a one-paisa
    entry - `conservation._paise`, `wall.LedgerEntry.decided` and
    `state.Proposal` all refuse bools in the same place for the same reason,
    and this is the fourth of the same guard rather than a new idea.
    """
    value = one.value
    return None if isinstance(value, bool) or not isinstance(value, int) else value


@dataclass(frozen=True)
class ExtractionResult:
    """Everything one document produced, and everything it did not.

    `raw_text` is kept on the record deliberately. When somebody disputes a
    figure months from now the only useful evidence is what the reader actually
    returned, and a record holding the conclusions without the input cannot be
    argued with - which sounds like a strength and is the opposite of one.
    """

    document: DocumentMeta
    supplier: Party
    buyer: Party
    invoice: InvoiceIdentity
    totals: Totals
    items: tuple[Item, ...] = ()
    findings: tuple[Finding, ...] = ()
    raw_text: str = ""
    said: str = ""
    review_reasons: tuple[str, ...] = ()
    #: Every signal `invoicelike.looks_like_a_bill` fired, by name, so a person
    #: reading a refusal sees the evidence and not only the verdict.
    signals: tuple[str, ...] = field(default_factory=tuple)

    @property
    def status(self) -> DocumentStatus:
        return self.document.status

    @property
    def requires_review(self) -> bool:
        """Whether a person must look before anything else happens.

        Derived from the status rather than stored beside it, so the two can
        never disagree. A stored flag is a second answer to a question the
        status already answers, and two answers is one of them being wrong.
        """
        from accountant.invoice.status import REQUIRES_REVIEW

        return self.document.status in REQUIRES_REVIEW

    def named_fields(self) -> tuple[tuple[str, ReadField], ...]:
        """Every field on this record, flattened, with a name a person reads.

        Line items are numbered from one, because a person holding the bill
        counts its rows from one and nobody has ever called the top row zero.
        """
        found: list[tuple[str, ReadField]] = []
        for prefix, party in (("supplier", self.supplier), ("buyer", self.buyer)):
            found.extend((f"{prefix}_{name}", one) for name, one in party.named())
        found.extend(self.invoice.named())
        found.extend(self.totals.named())
        for index, item in enumerate(self.items, start=1):
            found.extend((f"line_{index}_{name}", one) for name, one in item.named())
        return tuple(found)

    @property
    def field_confidence(self) -> Mapping[str, float]:
        """Every field's own score, by name. Unread fields are in here at 0.0.

        Present rather than omitted, on purpose. A caller iterating this map
        must see that a field exists and scored nothing; a map that dropped
        them would make an unread total indistinguishable from a field this
        package does not have.
        """
        return {name: one.confidence for name, one in self.named_fields()}

    @property
    def read_fields(self) -> tuple[str, ...]:
        """The names of the fields something was actually found for."""
        return tuple(name for name, one in self.named_fields() if one.read)

    @property
    def average_confidence(self) -> float:
        """The mean over the fields that were READ. `0.0` when none were.

        FOR REPORTING ONLY. See the module docstring: a mean hides exactly the
        field that should have stopped a post. `lowest_confidence` is the one a
        decision uses.

        The mean is over READ fields and not over all of them, because a record
        with four fields found and thirty absent would otherwise average to
        something near zero and say nothing about the four.
        """
        scores = [one.confidence for _name, one in self.named_fields() if one.read]
        return sum(scores) / len(scores) if scores else 0.0

    @property
    def lowest_confidence(self) -> float:
        """The weakest field that was read. `0.0` when none were.

        THE NUMBER A DECISION USES. One misread digit ruins an amount, so the
        record is only as trustworthy as its worst field - `cage/wall.py`
        makes this argument at length and this is the same rule one layer out.
        """
        scores = [one.confidence for _name, one in self.named_fields() if one.read]
        return min(scores) if scores else 0.0

    @property
    def failed_laws(self) -> tuple[Finding, ...]:
        return tuple(one for one in self.findings if one.verdict is Verdict.FAIL)

    @property
    def unchecked_laws(self) -> tuple[Finding, ...]:
        return tuple(
            one for one in self.findings if one.verdict is Verdict.INDETERMINATE
        )

    @classmethod
    def nothing_was_read(
        cls,
        *,
        document: DocumentMeta,
        source: str,
        raw_text: str = "",
        signals: Sequence[str] = (),
        review_reasons: Sequence[str] = (),
    ) -> ExtractionResult:
        """The record for a document no field came off, with its status intact.

        Still a full record, with every field present and unread, rather than a
        `None` a caller has to check for. A pipeline branching on "did we get a
        result at all" is a pipeline with two shapes of answer, and the second
        shape is the one nobody tests.
        """
        return cls(
            document=document,
            supplier=Party.nothing(source),
            buyer=Party.nothing(source),
            invoice=InvoiceIdentity.nothing(source),
            totals=Totals.nothing(source),
            raw_text=raw_text,
            said=SAID[document.status],
            signals=tuple(signals),
            review_reasons=tuple(review_reasons),
        )
