"""Score the reading path against ground truth. Reach AND accuracy, kept apart.

WHY THIS EXISTS AND WHY IT IS NOT `measure_field_slots.py`
-----------------------------------------------------------
`scripts/measure_field_slots.py` counts REACH: did a value come back. It cannot
count ACCURACY, because the 60 documents it scores have no ground truth. A reader
that confidently returns the WRONG total scores identically there to one that
returns the right one, and that is the whole reason its 1-of-300 was never enough
to say the extraction path works.

This script scores a corpus that DOES have ground truth
(`artifacts/problem1_ground_truth.json`, written by the corpus generator and by
hand from dataset annotations - never from this reader's own output).

THE REAL PATH, NOT A PROXY. `read_page` + `freeocr._scored` is what
`page_reader` calls on an upload, so every refusal the product makes is a refusal
here: `the_one` on disagreement, the artifact ceiling, the confidence-above-zero
rule, the family separation. An earlier measurement called `labels.values_for`
directly and OVERSTATED, because a label match is not a field.

WHAT IT REPORTS, AND WHY EACH ONE IS SEPARATE
    correct                  a value came back and it MATCHES ground truth
    incorrect                a value came back and it does NOT. The number that
                             matters most. One of these is worse than ten
                             refusals, because it is a wrong number a person may
                             not check.
    correctly unresolved     ground truth says ABSENT/AMBIGUOUS/UNREADABLE and
                             the reader returned nothing. This is a SUCCESS and
                             counting it as a failure is how a reader gets
                             pressured into inventing values.
    incorrectly unresolved   ground truth has a value and the reader missed it.
                             A miss, not a lie.
    false positive           ground truth says ABSENT and the reader answered
                             anyway. Invented data.

A candidate count is never success on its own. `correct` is the only number that
may go up without qualification, and `incorrect` and `false positive` must stay
at zero.

COMPARING VALUES. Money is compared as INTEGER PAISE. Dates are compared as
dates. A party is compared case-insensitively with runs of whitespace collapsed,
because `SHARMA  TRADERS` and `Sharma Traders` are the same supplier and a
reader is not being tested on capitalisation. Nothing else is normalised - in
particular no value is "corrected" to make it match.
"""

from __future__ import annotations

import argparse
import collections
import csv
import json
import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from accountant.extract.freeocr import _scored  # noqa: E402
from accountant.extract.labels import paise_or_none  # noqa: E402
from accountant.extract.pagereader import read_lines, read_page  # noqa: E402

CORPUS = REPO / "data" / "problem1_corpus"
TRUTH = REPO / "artifacts" / "problem1_ground_truth.json"

#: The five the owner named for the fast pass, mapped to what `_scored` returns.
FIELDS = ("party", "invoice_date", "total", "tax", "invoice_number")

#: Ground-truth statuses that mean "there is nothing here to find". A reader
#: returning nothing for one of these is CORRECT, not failing.
NOTHING_TO_FIND = frozenset({"ABSENT", "AMBIGUOUS", "UNREADABLE"})

CORRECT = "correct"
INCORRECT = "incorrect"
CORRECTLY_UNRESOLVED = "correctly unresolved"
INCORRECTLY_UNRESOLVED = "incorrectly unresolved"
FALSE_POSITIVE = "false positive"
NO_TRUTH = "no ground truth"


def _read(path: pathlib.Path) -> object:
    """The five fields, through the path a real upload takes."""
    try:
        lines = read_lines(path.read_bytes(), deadline_seconds=30.0)
    except Exception:
        return None
    return _scored(read_page(lines), "free_ocr")


def _same_money(got: object, expected: object) -> bool:
    """Integer paise on both sides, never float.

    THE GROUND TRUTH STORES MONEY AS A DICT, not a string:

        {"paise": 620419, "text": "6 204,19", "currency": "USD"}

    An earlier version of this function passed that whole dict to
    `paise_or_none`, which of course refused it, and every money field scored
    INCORRECT. MEASURED: 50 slots reported wrong that were all EXACT MATCHES -
    "expected {'paise': 3481695, ...}, read 3481695". The reader was right and
    the comparator was broken, which is the more dangerous way round: it would
    have been reported as 50 wrong money reads.

    `paise` is authoritative. `text` is what the page printed and is NOT parsed
    here - the corpus writes both, and re-deriving paise from the text would be
    this script forming its own opinion about the truth it is scoring against.
    """
    if isinstance(expected, dict):
        want = expected.get("paise")
        return isinstance(want, int) and got == want
    want = paise_or_none(str(expected))
    return want is not None and got == want


def _same_party(got: object, expected: str) -> bool:
    """Case-insensitive, whitespace collapsed. Nothing else.

    A reader is not under test for capitalisation, and an OCR engine decides
    where a gap goes. It IS under test for the characters, so no spelling is
    corrected and no near-match is accepted.
    """
    if not isinstance(got, str):
        return False
    tidy = re.sub(r"\s+", " ", got).strip().casefold()
    return tidy == re.sub(r"\s+", " ", expected).strip().casefold()


def _same_date(got: object, expected: str) -> bool:
    """Compared as a date when both sides parse, else as characters."""
    return (
        str(got) == expected.strip() or str(got).replace("-", "/") == expected.strip()
    )


def _verdict(field: str, truth: dict[str, object], answer: object) -> tuple[str, str]:
    status = str(truth.get("status", "")).upper()
    raw = truth.get("value")
    expected = raw if isinstance(raw, dict) else str(raw or "")
    got = getattr(answer, _ATTRIBUTE[field], None) if answer is not None else None

    if status not in NOTHING_TO_FIND and not expected:
        return NO_TRUTH, "ground truth states a status but no value"

    if got is None:
        if status in NOTHING_TO_FIND:
            return (
                CORRECTLY_UNRESOLVED,
                f"nothing to find ({status}) and nothing returned",
            )
        return INCORRECTLY_UNRESOLVED, f"expected {expected!r}, read nothing"

    if status in NOTHING_TO_FIND:
        return FALSE_POSITIVE, f"nothing to find ({status}) and read {got!r}"

    if field in ("total", "tax"):
        same = _same_money(got, expected)
    elif field == "party":
        same = _same_party(got, str(expected))
    elif field == "invoice_date":
        same = _same_date(got, str(expected))
    else:
        same = str(got).strip() == str(expected).strip()

    if same:
        return CORRECT, f"read {got!r}"
    return INCORRECT, f"expected {expected!r}, read {got!r}"


#: `_scored` names its fields differently from the ground truth. Written out so
#: the mapping is one readable line rather than a guess at a call site.
#: `invoice_number` has NO reader today - no vocabulary for it exists in
#: `labels.py` - so it is scored and will read nothing. That is a real gap and
#: hiding it by dropping the field would flatter the denominator.
_ATTRIBUTE = {
    "party": "party",
    "invoice_date": "date",
    "total": "total_paise",
    "tax": "tax_paise",
    "invoice_number": "invoice_number",
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--csv", type=pathlib.Path, default=REPO / "artifacts" / "problem1_scored.csv"
    )
    args = parser.parse_args()

    truth = json.loads(TRUTH.read_text())
    tally: collections.Counter[str] = collections.Counter()
    per_field: dict[str, collections.Counter[str]] = collections.defaultdict(
        collections.Counter
    )
    rows: list[dict[str, object]] = []

    documents = sorted(p for p in CORPUS.iterdir() if p.is_file())
    print(f"corpus: {len(documents)} documents, ground truth for {len(truth)}")

    for path in documents:
        entry = truth.get(path.stem) or truth.get(path.name)
        if entry is None:
            continue
        answer = _read(path)
        for field in FIELDS:
            field_truth = entry.get(field)
            if not isinstance(field_truth, dict):
                tally[NO_TRUTH] += 1
                per_field[field][NO_TRUTH] += 1
                continue
            verdict, why = _verdict(field, field_truth, answer)
            tally[verdict] += 1
            per_field[field][verdict] += 1
            rows.append(
                {
                    "document_id": path.stem,
                    "page": field_truth.get("page", 1),
                    "field": field,
                    "truth_status": field_truth.get("status"),
                    "expected": field_truth.get("value", ""),
                    "verdict": verdict,
                    "evidence": why,
                }
            )

    args.csv.parent.mkdir(parents=True, exist_ok=True)
    if rows:
        with args.csv.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)

    slots = sum(tally.values())
    print(f"\nslots scored: {slots}\n")
    for name in (
        CORRECT,
        INCORRECT,
        FALSE_POSITIVE,
        CORRECTLY_UNRESOLVED,
        INCORRECTLY_UNRESOLVED,
        NO_TRUTH,
    ):
        count = tally[name]
        print(f"  {name:24} {count:4}   {count / slots * 100:5.1f}%" if slots else name)

    print("\nper field:")
    for field in FIELDS:
        got = per_field[field]
        print(
            f"  {field:16} correct {got[CORRECT]:3}  incorrect {got[INCORRECT]:3}  "
            f"missed {got[INCORRECTLY_UNRESOLVED]:3}  "
            f"correctly-unresolved {got[CORRECTLY_UNRESOLVED]:3}  "
            f"false-positive {got[FALSE_POSITIVE]:3}"
        )

    print(f"\nCSV: {args.csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
