"""Where does party evidence die? One boundary named per document.

WHY THIS EXISTS
---------------
`party` reads 0 of 59 on the ground-truth corpus. That number alone supports
several incompatible explanations, and picking one without evidence is how a
reader gets "fixed" in the wrong place:

    A  the engine returned no words at all
    B  words came back but no PARTY label is printed on the page
    C  a label is printed but `values_for` does not match it
    D  `values_for` matched and `_words_for` mapped nothing back
    E  words were mapped but the artifact ceiling refused them
    F  `_read_party` refused the characters
    G  a value survived and scored 0.0, so it is dropped as unread

This walks the SAME boundaries the live reader crosses, in the same order, and
records the FIRST one at which the evidence is gone. It changes nothing - it is
a measurement, and the fix it justifies depends on which letter dominates.

WHY NOT JUST READ THE CODE. Every one of A-G is reachable by reading, and
reading cannot tell you WHICH of them actually happens on 59 real documents, or
in what proportion. A fix aimed at C when the corpus is 90% B is a fix that
moves nothing, and the only thing that separates those worlds is this count.

WHAT IT DOES NOT DO. It does not judge whether the party read is CORRECT - that
is `measure_problem1_corpus.py`. A document can pass every boundary here and
still read the buyer where the truth is the supplier.
"""

from __future__ import annotations

import collections
import csv
import json
import pathlib
import sys
import typing

REPO = pathlib.Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from accountant.extract.freeocr import (  # noqa: E402
    _scored,  # pyright: ignore[reportPrivateUsage]
)
from accountant.extract.pagereader import (  # noqa: E402
    _PRINTING,  # pyright: ignore[reportPrivateUsage]
    read_lines,
    read_page,
)
from accountant.labels import PARTY_LABELS, values_for  # noqa: E402

CORPUS = REPO / "data" / "problem1_corpus"
TRUTH = REPO / "artifacts" / "problem1_ground_truth.json"
OUT = REPO / "artifacts" / "problem1_party_diagnostics.csv"

NO_WORDS = "A_no_words_from_engine"
NO_LABEL_PRINTED = "B_no_party_label_on_the_page"
LABEL_NOT_MATCHED = "C_label_printed_but_not_matched"
MATCHED_NO_WORDS = "D_matched_but_no_words_mapped_back"
REFUSED_DOWNSTREAM = "E_words_mapped_but_refused_downstream"
SCORED_ZERO = "F_value_survived_but_scored_zero"
READ = "G_party_read"

#: The characters a human would look for to decide "does this page even name a
#: supplier". DELIBERATELY WIDER THAN `PARTY_LABELS`, because that is the whole
#: point of boundary B versus C: B asks whether the page says anything of the
#: kind, C asks whether OUR vocabulary caught it. If this list were the same as
#: `PARTY_LABELS`, C could never be observed and the measurement would be
#: incapable of finding a vocabulary gap.
LOOKS_LIKE_A_PARTY_LABEL = (
    "SUPPLIER",
    "VENDOR",
    "SELLER",
    "SOLD BY",
    "BILLED BY",
    "BILL FROM",
    "FROM",
    "BUYER",
    "CUSTOMER",
    "BILL TO",
    "BILLED TO",
    "SHIP TO",
    "SOLD TO",
    "CONSIGNEE",
    "M/S",
    "MESSRS",
    "PARTY",
    "CLIENT",
    "ISSUED BY",
    "ISSUED TO",
    "COMPANY",
    "TO,",
)


def _boundary(path: pathlib.Path) -> tuple[str, str]:
    """The first boundary at which party evidence is gone, and the detail."""
    try:
        lines = read_lines(path.read_bytes(), deadline_seconds=30.0)
    except Exception as problem:
        return NO_WORDS, f"reader raised {type(problem).__name__}"

    printed = [" ".join(word.text for word in line) for line in lines]
    if not any(text.strip() for text in printed):
        return NO_WORDS, "engine returned no words"

    page_text = "\n".join(printed).upper()
    looks_labelled = [label for label in LOOKS_LIKE_A_PARTY_LABEL if label in page_text]

    reading = read_page(lines)
    answer = _scored(reading, "free_ocr")

    if answer.party is not None:
        return READ, f"read {answer.party!r}"

    if not looks_labelled:
        return NO_LABEL_PRINTED, f"{len(printed)} lines, none naming a party"

    matched = values_for(tuple(printed), PARTY_LABELS, printing=_PRINTING)
    if not matched:
        return (
            LABEL_NOT_MATCHED,
            f"page prints {looks_labelled[:3]} but PARTY_LABELS matched nothing",
        )

    if not reading.party:
        return MATCHED_NO_WORDS, f"values_for gave {matched[:2]}, words mapped none"

    score = answer.confidences.get("party", 0.0)
    if score == 0.0:
        return SCORED_ZERO, f"words {[w.text for w in reading.party][:3]} scored 0.0"

    return REFUSED_DOWNSTREAM, f"{answer.sources.get('party', '')[:70]}"


def main() -> int:
    truth: dict[str, dict[str, object]] = json.loads(TRUTH.read_text())
    tally: collections.Counter[str] = collections.Counter()
    rows: list[dict[str, object]] = []

    for path in sorted(p for p in CORPUS.iterdir() if p.is_file()):
        entry = truth.get(path.stem) or truth.get(path.name)
        if entry is None:
            continue
        raw_slot: object = entry.get("party")
        if not isinstance(raw_slot, dict):
            continue
        slot = typing.cast(dict[str, object], raw_slot)
        boundary, detail = _boundary(path)
        tally[boundary] += 1
        rows.append(
            {
                "document_id": path.stem,
                "truth_status": slot.get("status"),
                "expected": slot.get("value", ""),
                "boundary": boundary,
                "detail": detail,
            }
        )

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    print(f"{len(rows)} documents with a party slot\n")
    for name in (
        NO_WORDS,
        NO_LABEL_PRINTED,
        LABEL_NOT_MATCHED,
        MATCHED_NO_WORDS,
        REFUSED_DOWNSTREAM,
        SCORED_ZERO,
        READ,
    ):
        print(f"  {name:38} {tally[name]:3}")
    print(f"\nCSV: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
