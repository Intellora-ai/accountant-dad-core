"""Where every field slot dies, counted at each boundary, over the real corpus.

WHY THIS EXISTS
---------------
"The reader found nothing" is not a diagnosis. It is five completely different
documents wearing the same sentence, and until they were counted separately the
work went to the wrong place twice.

MEASURED 2026-08-15 BY THIS SCRIPT, at its default `--limit 60`, over
`data/real_invoices_indian` then `data/real_invoices`, five field slots each:

    OCR word rows received                                   2596
    rows carrying characters                                 2367
    slots dying at NO OCR WORDS AT ALL                          10
    slots dying at WORDS PRESENT, NO LABEL MATCHED             287
    slots reaching an agreed candidate                           3

THE NUMBERS IN THIS BLOCK ARE THE ONES THIS SCRIPT PRODUCES, and that sentence
had to be earned. An earlier draft of this docstring quoted 6210 rows and 296
no-label slots, taken from a hand-written probe that sorted the two folders
TOGETHER before slicing 60. This script takes 60 from the Indian folder first.
Same corpus, different sixty, and therefore different totals - and a second
reader could not reproduce the figure, which is the only reason it was caught.

The conclusion is identical either way and is the point: ZERO slots die because
the engine found no words on a page it could open, and the overwhelming
majority die at label matching. Tesseract is not the bottleneck - the join
between the words it returns and the labels this reader knows is.

AND THE SECOND NUMBER REDIRECTED THE WORK AGAIN. Of 18 lines that mention a
total-ish word at all, only 4 carried a number on that same line - and all 4
already matched. The list of "has a number and is unmatched" came back EMPTY.
The other 14 print the label with the value somewhere else:

    'SUB TOTAL'   'GRAND TOTAL'   'Total Cost'   'Total des'
    'TOTAL QUANTITY:'   'AMOUNT IN WORDS: TWO THOUSAND NINE'

Which says plainly: adding label spellings buys about zero, and finding the
value on a neighbouring line is where the remaining documents are. Two agents
independently confirmed the same direction from the other end - seven new party
spellings produced 7 values across 413 documents and 7 of 7 were wrong.

HOW TO READ THE OUTPUT
----------------------
Every count is a COUNT OF SLOTS, where one slot is one field on one document.
Five fields over N documents is 5N slots. A slot lands in exactly one bucket, so
the buckets add up to 5N and nothing is lost between them - which is the whole
point, and the property `test_field_slots_are_never_lost` asserts.

WHAT THIS DOES NOT MEASURE
--------------------------
Whether a value that WAS read is CORRECT. This counts reach, not accuracy. A
document that confidently reads the wrong total lands in `candidate_agreed`
here and looks like a success. Accuracy needs labelled truth, and
`artifacts/ground_truth` is the only place this repository has any - 100
synthetic cases, not these documents. Nothing here is evidence about real-world
accuracy and it must never be quoted as if it were.

USAGE
-----
    .venv/bin/python scripts/measure_field_slots.py            # 60 documents
    .venv/bin/python scripts/measure_field_slots.py --limit 0  # all of them
    .venv/bin/python scripts/measure_field_slots.py --json out.json
"""

from __future__ import annotations

import argparse
import collections
import json
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    # A measurement script run from outside the tree it is measuring will import
    # the EDITABLE-INSTALLED package instead of this one. That has already
    # produced a silent before/after pair where the "after" never loaded the
    # change. Pin it, and print the path that was used.
    sys.path.insert(0, str(REPO))

from accountant.extract.labels import (  # noqa: E402
    DATE_LABEL,
    PARTY_LABELS,
    TAX_WHOLE,
    TOTAL_LABELS,
    Printing,
    amounts_for,
    values_for,
)
from accountant.extract.pagereader import read_lines  # noqa: E402

#: The five the owner named for the fast pass. `net` is deliberately absent: it
#: is not one of the five and counting it would change the denominator.
FIELDS: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("party", PARTY_LABELS, "text"),
    ("date", DATE_LABEL, "text"),
    ("total", TOTAL_LABELS, "amount"),
    ("tax", TAX_WHOLE, "amount"),
    ("invoice_number", ("INVOICE NO", "INVOICE NUMBER", "BILL NO", "INV NO"), "text"),
)

#: The corpus is photographs and scans, so the tolerant printing is the correct
#: one here. A text layer would get `EXACT_CHARACTERS`; using the tolerant rule
#: on a PDF is the defect `tests/test_labels.py` holds the control for.
PRINTING = Printing.READ_OFF_A_PHOTOGRAPH

PICTURES = {".jpg", ".jpeg", ".png"}

NO_WORDS = "1. no OCR words at all"
NO_LABEL = "2. words present, NO LABEL MATCHED"
DISAGREED = "3. label matched, printings disagreed"
AGREED = "4. label matched, candidate agreed"
ENGINE_REFUSED = "0. the engine refused the file"


def documents(limit: int) -> list[pathlib.Path]:
    """Every picture in the two real-document folders, in a stable order.

    Sorted, because an unsorted directory listing makes two runs of this script
    incomparable and the whole purpose is a before and an after.
    """
    found: list[pathlib.Path] = []
    for folder in ("real_invoices_indian", "real_invoices"):
        here = REPO / "data" / folder
        if here.is_dir():
            found.extend(
                p for p in sorted(here.iterdir()) if p.suffix.lower() in PICTURES
            )
    return found[:limit] if limit else found


def measure(limit: int) -> dict[str, object]:
    """Count every slot into exactly one bucket."""
    paths = documents(limit)
    where: collections.Counter[str] = collections.Counter()
    per_field: collections.Counter[str] = collections.Counter()
    rows_received = rows_with_characters = 0
    refused = 0

    for path in paths:
        try:
            lines = read_lines(path.read_bytes(), deadline_seconds=30.0)
        except Exception as exc:
            refused += 1
            for name, _, _ in FIELDS:
                where[f"{name}: {ENGINE_REFUSED} ({type(exc).__name__})"] += 1
            continue

        words = [word for line in lines for word in line]
        rows_received += len(words)
        rows_with_characters += sum(1 for word in words if word.text.strip())
        page = tuple(" ".join(word.text for word in line) for line in lines)

        for name, family, kind in FIELDS:
            found = (
                values_for(page, family, printing=PRINTING)
                if kind == "text"
                else amounts_for(page, family)
            )
            per_field[f"{name}: candidates"] += len(found)
            if not words:
                where[f"{name}: {NO_WORDS}"] += 1
            elif not found:
                where[f"{name}: {NO_LABEL}"] += 1
            else:
                seen = {
                    one.printed if kind == "text" else one.paise  # pyright: ignore[reportAttributeAccessIssue]
                    for one in found
                }
                where[f"{name}: {DISAGREED if len(seen) > 1 else AGREED}"] += 1

    slots = len(paths) * len(FIELDS)
    counted = sum(where.values())
    if counted != slots:
        # RAISED AND NOT ASSERTED. `python -O` strips an assert, and a
        # measurement whose only integrity check disappears under an optimisation
        # flag is a measurement nobody can trust. Every slot must land in exactly
        # one bucket or the buckets are not a partition and the percentages below
        # are arithmetic on a set with holes in it.
        raise RuntimeError(
            f"{counted} slots counted against {slots} expected - a slot fell "
            "between two buckets, which is the one thing this script exists to "
            "prevent"
        )
    return {
        "documents": len(paths),
        "fields_per_document": len(FIELDS),
        "slots": slots,
        "engine_refused_documents": refused,
        "ocr_rows_received": rows_received,
        "ocr_rows_with_characters": rows_with_characters,
        "candidates_per_field": dict(sorted(per_field.items())),
        "where_each_slot_died": dict(sorted(where.items())),
        "slots_reaching_a_candidate": sum(
            count for key, count in where.items() if key.endswith(AGREED)
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=60, help="0 means every document")
    parser.add_argument("--json", type=pathlib.Path, default=None)
    args = parser.parse_args()

    print(f"package read from: {REPO}")
    result = measure(args.limit)

    print(f"\ndocuments probed            : {result['documents']}")
    print(f"field slots (5 per document): {result['slots']}")
    print(f"OCR word rows received      : {result['ocr_rows_received']}")
    print(f"rows carrying characters    : {result['ocr_rows_with_characters']}")
    print(f"engine refused              : {result['engine_refused_documents']}")

    print("\ncandidates generated per field:")
    for key, count in result["candidates_per_field"].items():  # pyright: ignore[reportAttributeAccessIssue]
        print(f"  {key:34} {count}")

    print("\nwhere each field slot died:")
    for key, count in result["where_each_slot_died"].items():  # pyright: ignore[reportAttributeAccessIssue]
        print(f"  {key:52} {count}")

    reached = result["slots_reaching_a_candidate"]
    print(f"\nSLOTS REACHING A CANDIDATE  : {reached} of {result['slots']}")

    if args.json:
        args.json.write_text(json.dumps(result, indent=2, sort_keys=True))
        print(f"\nwritten to {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
