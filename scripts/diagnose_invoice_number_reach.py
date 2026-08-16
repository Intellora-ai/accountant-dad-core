"""Is an invoice number REACHABLE on the photo path, before one line is built?

WHY THIS RUNS FIRST
-------------------
`invoice_number` reads 0 of 55. Unlike `party`, that is not a broken path - it
is an ABSENT one. Four things are missing at once and no one of them alone
would fix it:

    labels.py holds no invoice-number vocabulary
    Reading has no invoice_number field
    read_page never searches for one
    _scored never produces one

Building all four is a real change across four files. THIS SCRIPT DECIDES
WHETHER THAT CHANGE WOULD PAY, and it exists because the party step an hour
earlier proved the cost of skipping this question: `SELLER` was added to the
party vocabulary on the strength of 13 documents printing it, and returned ZERO
correct values, because the real blocker was a two-column layout no vocabulary
can reach. The machinery would have been built and the number would not have
moved.

So this asks the cheap question first, over the 55 documents that HAVE an
invoice-number in ground truth:

    A  the engine returned no words at all              -> unreachable, image
    B  the page prints no invoice-number label          -> unreachable, document
    C  a label is printed, and the TRUE VALUE is on the SAME LINE   -> reachable
    D  a label is printed, and the true value is on the NEXT LINE   -> reachable
    E  a label is printed, true value is neither        -> layout, like party
    F  a label is printed and the true value is nowhere in the text -> misread

C and D are what a same-line-plus-next-line reader could win. E is the party
failure wearing different clothes. B and F cannot be won by any vocabulary.

IT USES GROUND TRUTH TO LOCATE THE VALUE, WHICH A READER MAY NEVER DO. That is
legitimate here and only here: this is a measurement of what is POSSIBLE, not a
reader. Knowing the answer is how it can tell C from E at all. Nothing it learns
is written back into the reader, and the reader it justifies has to find the
value without being told.
"""

from __future__ import annotations

import collections
import csv
import json
import pathlib
import re
import sys
import typing

REPO = pathlib.Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from accountant.extract.pagereader import read_lines  # noqa: E402

CORPUS = REPO / "data" / "problem1_corpus"
TRUTH = REPO / "artifacts" / "problem1_ground_truth.json"
OUT = REPO / "artifacts" / "problem1_invoice_number_reach.csv"

NO_WORDS = "A_no_words_from_engine"
NO_LABEL = "B_no_invoice_number_label_printed"
SAME_LINE = "C_label_and_value_on_the_same_line"
NEXT_LINE = "D_value_on_the_next_line"
ELSEWHERE = "E_label_printed_value_somewhere_else"
VALUE_ABSENT = "F_true_value_not_in_the_text_at_all"

#: What a bill calls its own number. This is the UNION of the vocabulary in
#: `accountant/invoice/parse.py` and the shapes the owner's spec named. It is
#: deliberately generous - the job here is to find the CEILING on what any
#: vocabulary could reach, so being too narrow would understate the answer and
#: talk us out of a change that was worth making.
LABELS = (
    "TAX INVOICE NO",
    "INVOICE NUMBER",
    "INVOICE NO",
    "DOCUMENT NO",
    "INVOICE #",
    "INV NO",
    "INV #",
    "BILL NUMBER",
    "BILL NO",
    "VOUCHER NO",
    "REFERENCE NO",
)


def _normalised(text: str) -> str:
    """Upper case with runs of whitespace collapsed, for containment tests."""
    return re.sub(r"\s+", " ", text).strip().upper()


def _reach(path: pathlib.Path, expected: str) -> tuple[str, str]:
    try:
        lines = read_lines(path.read_bytes(), deadline_seconds=30.0)
    except Exception as problem:
        return NO_WORDS, f"reader raised {type(problem).__name__}"

    printed = [" ".join(word.text for word in line) for line in lines]
    if not any(text.strip() for text in printed):
        return NO_WORDS, "engine returned no words"

    want = _normalised(expected)
    if not want:
        return VALUE_ABSENT, "ground truth states no value"

    labelled = [
        index
        for index, text in enumerate(printed)
        if any(label in _normalised(text) for label in LABELS)
    ]
    somewhere = [
        index for index, text in enumerate(printed) if want in _normalised(text)
    ]

    if not labelled:
        found = "value is on the page" if somewhere else "value is not on the page"
        return NO_LABEL, f"no label printed; {found}"

    if not somewhere:
        return (
            VALUE_ABSENT,
            f"label on line {labelled[0]} but {expected!r} is nowhere in the text",
        )

    for index in labelled:
        if index in somewhere:
            return SAME_LINE, f"line {index}: {printed[index][:56]!r}"
    for index in labelled:
        if index + 1 in somewhere:
            return NEXT_LINE, f"line {index + 1}: {printed[index + 1][:56]!r}"

    return (
        ELSEWHERE,
        f"label on {labelled[:2]}, value on {somewhere[:2]}",
    )


def main() -> int:
    truth: dict[str, dict[str, object]] = json.loads(TRUTH.read_text())
    tally: collections.Counter[str] = collections.Counter()
    rows: list[dict[str, object]] = []

    for path in sorted(p for p in CORPUS.iterdir() if p.is_file()):
        entry = truth.get(path.stem) or truth.get(path.name)
        if entry is None:
            continue
        raw_slot: object = entry.get("invoice_number")
        if not isinstance(raw_slot, dict):
            continue
        slot = typing.cast(dict[str, object], raw_slot)
        if str(slot.get("status", "")).upper() != "PRESENT":
            continue
        expected = str(slot.get("value") or "")
        where, detail = _reach(path, expected)
        tally[where] += 1
        rows.append(
            {
                "document_id": path.stem,
                "expected": expected,
                "reach": where,
                "detail": detail,
            }
        )

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    print(f"{len(rows)} documents state an invoice number\n")
    for name in (NO_WORDS, NO_LABEL, SAME_LINE, NEXT_LINE, ELSEWHERE, VALUE_ABSENT):
        print(f"  {name:40} {tally[name]:3}")
    winnable = tally[SAME_LINE] + tally[NEXT_LINE]
    print(f"\n  CEILING for a label-based reader: {winnable} of {len(rows)}")
    print(f"\nCSV: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
