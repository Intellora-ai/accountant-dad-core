"""Many documents, one at a time, and one bad one does not stop the rest.

THE FOUR PROMISES
------------------
INDEPENDENT. Each document is described on its own. Nothing one document reads
changes what another reads - the only thing carried between them is the set of
bills already seen, and that only ever ADDS a refusal.

IT KEEPS GOING. A document that raises does not end the run. The exception is
recorded against that document's name and the next one starts. A batch that
stops at the first bad file is a batch somebody runs overnight and finds
one-tenth finished, with no way to tell which tenth.

IDEMPOTENT BY FILE HASH. The same bytes twice produce one result and one
recorded repeat. Not two results, not two drafts, not a draft and a duplicate.
`extract/service.py::document_key` produces the hash this keys on, so a caller
already holding one may pass it straight in.

IT WRITES NOTHING ANYWHERE. No Tally, no database, no file. It returns a
report. `accountant/invoice/` imports no part of `accountant.tallyio` and
`tests/test_invoice_framework.py` reads the import graph to prove it rather
than trusting this paragraph.

WHAT "IDEMPOTENT" DOES NOT MEAN HERE
-------------------------------------
It means WITHIN ONE RUN. Nothing in this repository remembers a file hash or a
bill number between runs: `accountant/memory/` indexes vendors and phrases and
claims OPERATION IDS at the write boundary, and there is no table anywhere keyed
by supplier and invoice number. Running the same folder twice produces two
identical reports and neither knows about the other.

That is a real limitation and it is written here rather than left for somebody
to discover after the second run. `docs/INVOICE_EXTRACTION_FRAMEWORK.md` says
what closing it would take. Papering over it with a module-level set that
quietly empties on restart would be worse than the gap, because the gap is at
least visible.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from accountant.extract.labels import Printing
from accountant.invoice.bridge import (
    DEFAULT_THRESHOLDS,
    Thresholds,
    describe,
    invoice_number_of,
    supplier_key_of,
)
from accountant.invoice.parse import Reading
from accountant.invoice.result import ENGINE_NOT_STATED, ExtractionResult
from accountant.invoice.status import DocumentStatus
from accountant.invoice.validate import EXACTLY, Tolerance


@dataclass(frozen=True)
class Document:
    """One thing to read, already read. This module opens nothing.

    `name` is what a person calls it - a filename, a job reference - and is used
    only in the report. `file_hash` is what IDENTITY is decided on, because two
    people can name the same bytes two things and one person can name two
    different bills the same thing.
    """

    name: str
    file_hash: str
    reading: Reading
    page_count: int = 1
    engine: str = ENGINE_NOT_STATED

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError(
                "a document in a batch must have a name a person can find it "
                "by. A blank one makes two rows of the report into one row."
            )


@dataclass(frozen=True)
class Read:
    """One document that was described, and what it was called."""

    name: str
    result: ExtractionResult


@dataclass(frozen=True)
class Repeat:
    """Bytes this run has already seen, and what they were called the first
    time. Recorded rather than dropped: a folder listing the same file twice is
    ordinary, and a report that silently showed one row would leave somebody
    counting their documents and getting the wrong answer."""

    name: str
    file_hash: str
    first_seen_as: str


@dataclass(frozen=True)
class Broken:
    """A document whose description raised, and what it raised.

    The TYPE and the message, not the traceback. A traceback in a report is a
    thing nobody reads; the type name is what says whether this is one bad file
    or a defect in the parser that is about to affect every file after it.
    """

    name: str
    file_hash: str
    failure: str


@dataclass(frozen=True)
class BatchReport:
    """Everything the run produced, including what it could not produce.

    Four lists rather than one list with a kind on each row. A caller wanting
    the results does not have to filter, and a caller wanting the failures
    cannot forget to.
    """

    read: tuple[Read, ...] = ()
    repeats: tuple[Repeat, ...] = ()
    broken: tuple[Broken, ...] = ()

    @property
    def counts(self) -> Mapping[DocumentStatus, int]:
        """How many documents reached each status. EVERY status, including zero.

        A map with the zeroes left out reads as though those statuses did not
        happen, when what it means is that they did not happen THIS TIME - and
        the difference matters when somebody is comparing two runs.
        """
        tally = dict.fromkeys(DocumentStatus, 0)
        for one in self.read:
            tally[one.result.status] += 1
        return tally

    @property
    def needing_review(self) -> tuple[Read, ...]:
        return tuple(one for one in self.read if one.result.requires_review)

    @property
    def raw_text(self) -> Mapping[str, str]:
        """What each reader returned, kept by name.

        Preserved through the whole run on purpose. When somebody disputes a
        figure the only useful evidence is the characters the parse started
        from, and a report holding conclusions without inputs cannot be argued
        with - which sounds like a strength and is the opposite of one.
        """
        return {one.name: one.result.raw_text for one in self.read}


def run(
    documents: Sequence[Document],
    *,
    printing: Printing,
    tolerance: Tolerance = EXACTLY,
    thresholds: Thresholds = DEFAULT_THRESHOLDS,
) -> BatchReport:
    """Describe every document, in the order given, and report on all of them.

    IN THE ORDER GIVEN, and that is part of the contract. Sorting here would
    make "the first time these bytes were seen" depend on a sort nobody can see
    in the caller, and the repeat rows name that first sighting.

    A DOCUMENT THAT RAISES IS RECORDED AND THE RUN CONTINUES. `Exception` is
    caught deliberately broadly: this loop cannot know which of a parser's
    failure modes is worth stopping for, and stopping on the wrong one leaves
    the operator with a partial report and no list of what was skipped.
    `BaseException` is NOT caught, so a keyboard interrupt still stops the run.
    """
    read: list[Read] = []
    repeats: list[Repeat] = []
    broken: list[Broken] = []
    seen_bytes: dict[str, str] = {}
    seen_bills: set[tuple[str, str]] = set()

    for document in documents:
        first = seen_bytes.get(document.file_hash)
        if first is not None:
            repeats.append(
                Repeat(
                    name=document.name,
                    file_hash=document.file_hash,
                    first_seen_as=first,
                )
            )
            continue
        seen_bytes[document.file_hash] = document.name
        try:
            result = describe(
                document.reading,
                printing=printing,
                file_hash=document.file_hash,
                page_count=document.page_count,
                engine=document.engine,
                tolerance=tolerance,
                thresholds=thresholds,
                already_seen=frozenset(seen_bills),
            )
        except Exception as exc:
            broken.append(
                Broken(
                    name=document.name,
                    file_hash=document.file_hash,
                    failure=f"{type(exc).__name__}: {exc}",
                )
            )
            continue
        supplier = supplier_key_of(result)
        number = invoice_number_of(result)
        if supplier and number:
            seen_bills.add((supplier, number))
        read.append(Read(name=document.name, result=result))

    return BatchReport(read=tuple(read), repeats=tuple(repeats), broken=tuple(broken))
