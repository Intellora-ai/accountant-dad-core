"""Where a document got to, and which of the ways it could stop it took.

THE DISTINCTION THIS FILE EXISTS FOR
-------------------------------------
`OCR_FAILED` and `INVOICE_MISSING_FIELDS` are separate statuses, and that
separation is the whole point of the package.

Before it, both produced the same blank. A person looking at an empty draft
could not tell whether the reading engine had returned nothing or had returned
a perfectly good page that nothing downstream recognised - and those need
different people to fix them. The first is an engine or an image problem; the
second is a vocabulary problem in `parse.py`, fixable by adding a label.

Measured 2026-08-15: 82 of 106 JPGs return text, so the second case is the
common one and it was the one nobody could see.

WHY THIS IS NOT `cage/state.py`, AND WHY IT IS NOT A COPY OF IT EITHER
-----------------------------------------------------------------------
`cage/state.py` is a state machine over a PROPOSAL - seven states, thirteen
events, an audit row per transition, replayed on construction. It starts at
`OBSERVED`, which its own docstring defines as "the reader has spoken".

Everything this file's first six statuses describe happens BEFORE that. There
is no proposal yet, because there is nothing to propose: `OCR_FAILED` means no
characters, `NON_INVOICE` means the characters are a museum catalogue. A
proposal cannot represent them, and adding them to that enum would mean
rewriting a table whose legality is replayed against every stored history.

So this does not extend it and does not duplicate it. It MAPS onto it -
`CAGE_STATE_OF` below - and the map is mostly `None`, which is the honest
answer: most of these documents never became a proposal at all.

THE THREE THAT DO MAP are the three where a proposal exists, and each one is
pinned to the state `cage/state.py` already defines rather than to a new word
meaning nearly the same thing.

NO CLOCK, NO IO, NO NETWORK. Names and two tables.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Final

from accountant.cage.state import State


class DocumentStatus(StrEnum):
    """The ten, in the order a document meets them going right.

    Ordered by how far the document got, so a reader of this list sees the
    pipeline. Nothing depends on the order - `StrEnum` members compare by
    value - but a list nobody can read in order is a list nobody reads.
    """

    #: The reading engine returned nothing at all. Not a short reading - none.
    #: This is an ENGINE or IMAGE fact and never a fact about the fields.
    OCR_FAILED = "ocr_failed"

    #: Characters came back, and they are not language. Below
    #: `LEGIBLE_CHARACTERS` of the text is alphanumeric, so what arrived is the
    #: engine's noise on a photograph of something that is not a page.
    UNREADABLE = "unreadable"

    #: Readable, and confidently not a bill. `invoicelike.looks_like_a_bill`
    #: fired fewer than its threshold of signals AND at least one signal fired,
    #: so something is on the page and none of it is what a bill prints.
    NON_INVOICE = "non_invoice"

    #: Readable, and nothing at all fired. Distinct from `NON_INVOICE` on
    #: purpose: "this is a museum catalogue" and "we have no idea what this is"
    #: are different sentences, and only the second is worth a person's minute.
    UNKNOWN_DOCUMENT = "unknown_document"

    #: Looks like a bill, and there is not enough of it to work with. Fewer
    #: than `ENOUGH_CHARACTERS` came back. The bill is real; the reading is
    #: thin, which is a re-scan and not a re-parse.
    INVOICE_LOW_TEXT = "invoice_low_text"

    #: Looks like a bill, plenty of text, and a field the system must have was
    #: not found. THE READER IS NOT AT FAULT HERE and the status says so.
    INVOICE_MISSING_FIELDS = "invoice_missing_fields"

    #: Every mandatory field was found and the arithmetic does not hold. The
    #: figures are read; they contradict each other. Nothing is repaired.
    INVOICE_VALIDATION_FAILED = "invoice_validation_failed"

    #: Read, complete, and internally consistent. A PERSON HAS NOT LOOKED YET.
    #: This is the furthest this package can take a document on its own.
    READY_FOR_REVIEW = "ready_for_review"

    #: A person looked and said yes. Set by whatever asks the person; never by
    #: anything in this package, and `tests/test_invoice_framework.py` pins it.
    APPROVED = "approved"

    #: In somebody's books. Set by the write path, which is not here.
    POSTED = "posted"


#: Statuses where a person must look before anything else happens. Everything
#: except the two a person or a write has already reached.
#:
#: `READY_FOR_REVIEW` IS IN THIS SET, and the name is why it has to be said out
#: loud: "ready for review" is the good outcome and it still means a person has
#: not looked. A set that excluded it would let the happy path skip the one
#: check the whole package leads up to.
REQUIRES_REVIEW: Final[frozenset[DocumentStatus]] = frozenset(
    status
    for status in DocumentStatus
    if status not in (DocumentStatus.APPROVED, DocumentStatus.POSTED)
)

#: The statuses that mean no usable field came out. Named as a set because
#: three call sites asked the same question three slightly different ways.
NOTHING_WAS_READ: Final[frozenset[DocumentStatus]] = frozenset(
    {
        DocumentStatus.OCR_FAILED,
        DocumentStatus.UNREADABLE,
        DocumentStatus.NON_INVOICE,
        DocumentStatus.UNKNOWN_DOCUMENT,
    }
)

#: Where the proposal machine in `cage/state.py` would be, or `None` because no
#: proposal exists for this document and inventing one would be a lie about
#: what happened.
#:
#: `INVOICE_MISSING_FIELDS` MAPS TO `None` AND NOT TO `BLOCKED`, and that is the
#: entry most likely to be argued with. `BLOCKED` in the proposal machine is
#: terminal and carries a written refusal - it means somebody looked at a
#: candidate write and refused it. A bill whose supplier line was never matched
#: has had no write considered; calling that blocked would put a refusal in the
#: audit trail for a decision nobody made.
#:
#: `INVOICE_VALIDATION_FAILED` DOES map to `BLOCKED`, because that is exactly
#: what `Event.VALIDATION_FAILED` means there and exactly where it lands.
CAGE_STATE_OF: Final[Mapping[DocumentStatus, State | None]] = {
    DocumentStatus.OCR_FAILED: None,
    DocumentStatus.UNREADABLE: None,
    DocumentStatus.NON_INVOICE: None,
    DocumentStatus.UNKNOWN_DOCUMENT: None,
    DocumentStatus.INVOICE_LOW_TEXT: None,
    DocumentStatus.INVOICE_MISSING_FIELDS: None,
    DocumentStatus.INVOICE_VALIDATION_FAILED: State.BLOCKED,
    DocumentStatus.READY_FOR_REVIEW: State.OBSERVED,
    DocumentStatus.APPROVED: State.DECIDED,
    DocumentStatus.POSTED: State.POSTED,
}

#: What a person is told, per status. Plain words, no jargon, no field names a
#: person has never heard - the same rule `questions.py` applies to every
#: sentence that reaches a screen.
#:
#: HELD AS DATA rather than built in an `if` chain, so the whole vocabulary can
#: be read in one place and a test can assert every status has one. A status
#: with no sentence is a blank on somebody's screen.
SAID: Final[Mapping[DocumentStatus, str]] = {
    DocumentStatus.OCR_FAILED: (
        "nothing at all could be read from this file. That is about the file or "
        "the reading program, not about the bill - try a clearer photograph."
    ),
    DocumentStatus.UNREADABLE: (
        "something was read from this file but it is not words. It is most "
        "likely a photograph of something that is not a page."
    ),
    #: "Nothing a bill prints" and not "None of the things a bill prints",
    #: which is what this said first. Both read the same to a person; only one
    #: of them passes a guard that refuses the code word `None` in a sentence a
    #: person sees, and a guard that has to make an exception for a sentence is
    #: a guard somebody will make the next exception to.
    DocumentStatus.NON_INVOICE: (
        "this was read fine, and it is not a bill. Nothing a bill prints is "
        "anywhere on it."
    ),
    DocumentStatus.UNKNOWN_DOCUMENT: (
        "this was read fine and we cannot tell what it is. Nothing on it looks "
        "like a bill and nothing on it looks like anything else we know."
    ),
    DocumentStatus.INVOICE_LOW_TEXT: (
        "this looks like a bill and almost nothing came off it. The bill is "
        "fine; the picture is too poor to read - try scanning it again."
    ),
    DocumentStatus.INVOICE_MISSING_FIELDS: (
        "this looks like a bill and read well, and something we must have was "
        "not on it or was not printed under a name we know. This one is ours to "
        "fix, not yours."
    ),
    DocumentStatus.INVOICE_VALIDATION_FAILED: (
        "the figures on this bill were read and they do not agree with each "
        "other. Nothing has been changed to make them agree."
    ),
    DocumentStatus.READY_FOR_REVIEW: (
        "this bill was read and its figures add up. Nobody has checked it yet."
    ),
    DocumentStatus.APPROVED: "somebody checked this bill and said yes to it.",
    DocumentStatus.POSTED: "this bill is in the books.",
}
