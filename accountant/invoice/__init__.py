"""Turning what a reader returned into fields, and saying when it did not.

WHY THIS PACKAGE EXISTS
-----------------------
Measured 2026-08-15: 82 of 106 JPGs in `data/` return non-empty text from
tesseract 5.5.3, median 107.5 characters. The reading works. What did not work
was everything after it - a document that returned 107 characters and a
document that returned nothing produced the same downstream silence, so
"the reader failed" and "the reader worked and nothing recognised a field" were
one blank.

Those are different problems with different fixes and different people to tell,
and this package is what makes them different sentences.

WHAT IS IN HERE, AND WHAT IS DELIBERATELY NOT
----------------------------------------------
    status.py     the ten statuses a document can be in, and how they map onto
                  the proposal machine that already exists
    fields.py     one value, how sure we are, how it was found, and where
    parse.py      text -> fields. Pure, deterministic, no clock, no network
    validate.py   the accounting laws, in integer paise
    result.py     the versioned record the whole thing produces
    bridge.py     primitives in, result out. Changes no reading behaviour
    batch.py      many documents, independently, idempotent by file hash

NOT IN HERE: any reading. Nothing in this package opens a file, starts a
program, decodes an image or touches a network. It is handed characters that
somebody else already read. `accountant/extract/` owns reading and the guard in
`tests/test_no_reader.py` owns that boundary.

NOT IN HERE: any write. Nothing here imports `accountant.tallyio`, and
`tests/test_invoice_framework.py` asserts that by reading the import graph
rather than by trusting this paragraph.

NOT IN HERE: any model, any LLM, any learned weight. Every answer is arithmetic
or a named pattern, so two runs of the same bytes give the same answer on a
machine that has never seen an invoice.

WHAT NONE OF IT PROVES
-----------------------
That any of it finds the right field on a real Indian GST invoice. There is one
GSTIN in the whole of `data/` and it is on a tribunal order. The fixtures prove
PARSER AND VALIDATION BEHAVIOUR and they prove nothing about production reading
accuracy. `docs/INVOICE_EXTRACTION_FRAMEWORK.md` carries the full statement and
the fixture contract that would change it.
"""

from __future__ import annotations
