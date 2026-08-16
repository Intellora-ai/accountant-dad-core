"""Does this document look like a bill at all, and how strongly.

WHY THIS EXISTS
----------------
Measured 2026-08-15 over 413 real documents: 407 read no field at all. The
reason turned out not to be the reader. Of the 407, 344 HAD text. Of the 77
PDFs, 36 carried no total-type label anywhere, and every one of the 14 that did
carry a label AND a number beside it was foreign - `Total HT 27,99 EUR`,
`Total BeforeTax 32.838,00`, `TOTAL AMOUNT DUE $4.11`.

Then the corpus's own sidecars said why: 300 of 422 documents came from
Wikimedia Commons, and every licence recorded is a redistributable one - 134
CC BY-SA 4.0, 95 public domain, 46 CC BY 4.0. The corpus was selected for
LICENCE, because it lives in a public repository. Nobody licenses their bills
for redistribution, so openly-licensed documents and real commercial invoices
are very nearly disjoint sets.

So most of what the reader is handed is not a bill, and "read nothing" was the
correct answer to a question nobody had asked out loud. This module asks it out
loud.

WHAT IT NEVER DOES, AND THIS IS THE WHOLE DESIGN
--------------------------------------------------
IT NEVER SUPPRESSES A READ. It is a label attached beside the fields, never a
gate in front of them.

The obvious wiring - "if it does not look like an invoice, return nothing" -
adds a brand new way to lose a real bill: one false negative and a genuine
invoice reads as blank, with the reader reporting exactly what it reports for a
photograph of a cat. The reader's failures would become indistinguishable from
the detector's.

This is the replacement rule from `ARCHITECTURE.md` in a smaller costume: an
advisory layer is ADDED to a deterministic one and never subtracts from it.
`conservation.py` makes the same argument about `INDETERMINATE`, and
`freeocr.py` makes it about a confidence that is absent rather than zero.

WHAT IT IS FOR
---------------
A document that reads no field gets a REASON rather than a silence. "Nothing was
read, and this does not look like a bill" is a different sentence from "nothing
was read, and this looks exactly like a bill" - the first is a corpus problem
and the second is a reader problem, and until now they were the same blank.

HOW WELL IT SEPARATES, MEASURED ON DOCUMENTS WHOSE CLASS IS KNOWN
-------------------------------------------------------------------
Seven documents confirmed by hand to be real invoices, five confirmed not to be:

    real invoices        scores 2, 3, 3, 4, 4, 4, 4     7 of 7 at or above 2
    not invoices         scores 0, 0, 0, 0, and one 3   4 of 5 below 2

`ENOUGH_SIGNALS = 2` is the owner's number. Raising it to 3 would lose
`vendor-samples-058.pdf`, which is a real invoice scoring exactly 2.

n IS TWELVE. That is an anecdote, not an accuracy claim, and this docstring
will not pretend otherwise. The one false positive - `gov-and-open-data-003.pdf`
at 3 - matters less than a false negative would, because a document wrongly
called a bill is simply read and produces nothing, which is what happens today.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

#: Each signal is a separate question about the page, and they are counted
#: rather than combined, so no single one can carry a document on its own.
#: A weighted score would need weights, and nothing here has measured any.
SIGNALS: Final[tuple[tuple[str, str], ...]] = (
    ("names itself a bill", r"\b(invoice|bill|receipt|challan)\b"),
    ("mentions tax", r"\b(GST|VAT|tax)\b"),
    ("mentions an amount", r"\b(total|amount|payable|due)\b"),
    ("names a tax identity", r"\b(GSTIN|PAN|TAN)\b"),
    ("prints a currency", r"₹|Rs\.?|INR"),
    #: A GSTIN is 15 characters: 2 digits of state code, 5 letters and 4 digits
    #: of PAN, 1 letter of entity number, 1 character, a literal Z, 1 checksum.
    #: The strongest single signal here, because nothing that is not an Indian
    #: tax document prints one by accident.
    ("carries a GSTIN", r"\b\d{2}[A-Z]{5}\d{4}[A-Z]\d[Z][A-Z\d]\b"),
)

#: The owner's number, 2026-08-15. Measured: at 2, seven of seven known
#: invoices are caught and four of five known non-invoices are refused. At 3, a
#: real invoice is lost. Not tuned to make anything pass - `ARCHITECTURE.md:671`
#: forbids that - and if it ever is moved, the measurement above is what has to
#: be re-run first.
ENOUGH_SIGNALS: Final = 2


@dataclass(frozen=True)
class Verdict:
    """What the page looks like, and the sentence a person would read.

    Frozen, like every other verdict in this repository. A judgement that can be
    edited after the fact is not evidence.
    """

    #: Did enough signals fire? Never consulted before extraction, only after.
    looks_like_a_bill: bool

    #: Which signals fired, by name. The count alone cannot be argued with; the
    #: names can, and somebody reading a refusal deserves to see them.
    signals: tuple[str, ...]

    #: Plain words, no jargon. This is what a person sees when nothing was read.
    said: str


def looks_like_a_bill(text: str) -> Verdict:
    """Count the signals this page carries, and say so in words.

    Takes TEXT and not bytes, and reads nothing itself. It cannot open a file,
    start a program or touch the network, so it cannot fail in any way that
    matters and needs no timeout. Give it whatever the reader already read.
    """
    fired = tuple(
        name for name, pattern in SIGNALS if re.search(pattern, text, re.IGNORECASE)
    )
    enough = len(fired) >= ENOUGH_SIGNALS

    if not text.strip():
        said = "there was no readable text in this file at all."
    elif enough:
        said = (
            f"this looks like a bill: it {', it '.join(fired)}. "
            "If no figures were read from it, that is the reader's fault and "
            "not the document's."
        )
    elif fired:
        said = (
            f"this does not look like a bill. It {', it '.join(fired)}, and "
            "nothing else a bill would print."
        )
    else:
        said = (
            "this does not look like a bill. None of the things a bill prints - "
            "the word invoice, a tax line, an amount, a GSTIN, a rupee figure - "
            "is anywhere on it."
        )

    return Verdict(looks_like_a_bill=enough, signals=fired, said=said)
