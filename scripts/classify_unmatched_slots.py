"""Why does a field slot find no label? Eight causes, counted. Read-only.

WHY THIS EXISTS
---------------
MEASURED through the real upload path: 1 of 300 field slots reaches a candidate,
and 287 die at "no label matched". That sentence is not a diagnosis - it is four
or five completely different documents wearing one label, and each one has a
different fix:

    the page has no such label            -> a vocabulary change buys nothing
    the page has a label we do not know   -> add the spelling, cheap
    OCR mangled a label we DO know        -> a matcher change, or better pixels
    the label is there and the matcher
    refused it on spacing or case         -> a normalisation bug, cheapest of all

Until they are separated, any extraction work is guessing at which one it is.
This script separates them.

HOW EACH VERDICT IS REACHED, so the numbers can be argued with
---------------------------------------------------------------
For one field on one document, in this order, first match wins:

    IMAGE_QUALITY_FAILURE       fewer than `LEGIBLE_ENOUGH` word rows carry
                                characters at all. Nothing about labels can be
                                concluded from a page nobody could read.

    LABEL_NORMALIZATION_FAILURE the exact label text IS present in the page's
                                characters, but `values_for` matched nothing.
                                The vocabulary is right and the matcher refused
                                it - the cheapest possible fix.

    OCR_LABEL_CORRUPTION        no exact label, but some word on the page is
                                within `CLOSE_ENOUGH` edits of one. The label was
                                printed and the engine mangled it.

    UNKNOWN_LABEL               no known label and no near-miss, but the page
                                carries a word from `FAMILY_HINTS` for this field
                                - a word that means what the label means, spelled
                                a way the vocabulary does not hold.

    WRONG_FIELD_FAMILY          a label from a DIFFERENT family matched here.

    NO_LABEL_ON_PAGE            none of the above. The page genuinely does not
                                print this field under any name we can detect.

    PARSER_PATH_FAILURE         a label DID match and a value was located, and
                                the field still came back empty. The failure is
                                downstream of matching.

    UNKNOWN                     reserved. Anything the rules above cannot place.

WHAT THIS DOES NOT ESTABLISH. Whether a value, once found, is CORRECT. This
counts reach, not accuracy.
"""

from __future__ import annotations

import argparse
import collections
import csv
import difflib
import json
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from accountant.extract.freeocr import _scored  # noqa: E402
from accountant.extract.pagereader import read_lines, read_page  # noqa: E402
from accountant.labels import (  # noqa: E402
    DATE_LABEL,
    NET_LABELS,
    PARTY_LABELS,
    TAX_WHOLE,
    TOTAL_LABELS,
    Printing,
    amounts_for,
    values_for,
)

PRINTING = Printing.READ_OFF_A_PHOTOGRAPH
PICTURES = {".jpg", ".jpeg", ".png"}

#: Below this many word rows carrying characters, the page is unreadable and
#: nothing about its labels can be concluded. Not a tuned number - it is the
#: smallest count at which "the page has words" is a defensible statement.
LEGIBLE_ENOUGH = 5

#: How close a mangled word has to be to a known label to be called a corruption
#: rather than an unrelated word. `difflib` ratio, not an edit count, because a
#: 4-letter and a 12-letter label need different absolute tolerances.
CLOSE_ENOUGH = 0.78

#: Words that MEAN what a field's label means, in spellings the vocabulary does
#: not hold. A page carrying one of these is a page that names the field under a
#: name we do not know - which is a different fix from a page that never names it.
FAMILY_HINTS: dict[str, tuple[str, ...]] = {
    "total": ("TOTAL", "AMOUNT", "PAYABLE", "DUE", "BALANCE", "SUM", "MONTANT"),
    "tax": ("TAX", "GST", "VAT", "CGST", "SGST", "IGST", "TVA", "DUTY"),
    "net": ("SUBTOTAL", "SUB TOTAL", "NET", "TAXABLE", "PRE-TAX", "BEFORE TAX"),
    "party": (
        "SUPPLIER",
        "VENDOR",
        "SELLER",
        "BILLED",
        "SOLD",
        "FROM",
        "M/S",
        "CUSTOMER",
        "BUYER",
        "PARTY",
        "CONSIGNEE",
    ),
    "date": ("DATE", "DATED", "ISSUED", "INVOICE DATE", "BILL DATE"),
}

FAMILIES: dict[str, tuple[str, ...]] = {
    "total": TOTAL_LABELS,
    "tax": TAX_WHOLE,
    "net": NET_LABELS,
    "party": PARTY_LABELS,
    "date": DATE_LABEL,
}

KINDS = {
    "total": "amount",
    "tax": "amount",
    "net": "amount",
    "party": "text",
    "date": "text",
}

NO_LABEL_ON_PAGE = "NO_LABEL_ON_PAGE"
UNKNOWN_LABEL = "UNKNOWN_LABEL"
OCR_LABEL_CORRUPTION = "OCR_LABEL_CORRUPTION"
LABEL_NORMALIZATION_FAILURE = "LABEL_NORMALIZATION_FAILURE"
WRONG_FIELD_FAMILY = "WRONG_FIELD_FAMILY"
PARSER_PATH_FAILURE = "PARSER_PATH_FAILURE"
IMAGE_QUALITY_FAILURE = "IMAGE_QUALITY_FAILURE"
UNKNOWN = "UNKNOWN"


def documents(limit: int) -> list[pathlib.Path]:
    found: list[pathlib.Path] = []
    for folder in ("real_invoices_indian", "real_invoices"):
        here = REPO / "data" / folder
        if here.is_dir():
            found.extend(
                p for p in sorted(here.iterdir()) if p.suffix.lower() in PICTURES
            )
    return found[:limit] if limit else found


def _near_miss(words: list[str], family: tuple[str, ...]) -> str:
    """The page word closest to a known label, when it is close enough."""
    best, score = "", 0.0
    for word in words:
        if len(word) < 3:
            continue
        for label in family:
            ratio = difflib.SequenceMatcher(None, word, label).ratio()
            if ratio > score:
                best, score = f"{word}~{label}", ratio
    return best if score >= CLOSE_ENOUGH else ""


def classify(
    field: str, page: tuple[str, ...], words: list[str], legible: int, value: object
) -> tuple[str, str]:
    family = FAMILIES[field]
    upper = " ".join(page).upper()

    if legible < LEGIBLE_ENOUGH:
        return IMAGE_QUALITY_FAILURE, f"only {legible} word rows carry characters"

    found = (
        values_for(page, family, printing=PRINTING)
        if KINDS[field] == "text"
        else amounts_for(page, family)
    )
    if found:
        if value is None:
            return PARSER_PATH_FAILURE, f"{len(found)} located, field still empty"
        return "MATCHED", ""

    exact = [label for label in family if label in upper]
    if exact:
        return (
            LABEL_NORMALIZATION_FAILURE,
            f"page contains {exact[0]!r} and the matcher refused it",
        )

    near = _near_miss(words, family)
    if near:
        return OCR_LABEL_CORRUPTION, f"closest word/label pair {near!r}"

    hint = [h for h in FAMILY_HINTS[field] if h in upper]
    if hint:
        return UNKNOWN_LABEL, f"page says {hint[0]!r}, not in the vocabulary"

    other = [
        name
        for name, fam in FAMILIES.items()
        if name != field and any(label in upper for label in fam)
    ]
    if other:
        return WRONG_FIELD_FAMILY, f"only {other[0]} labels are on this page"

    return NO_LABEL_ON_PAGE, "no label and no synonym for this field anywhere"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=60)
    parser.add_argument(
        "--csv", type=pathlib.Path, default=REPO / "artifacts" / "unmatched_slots.csv"
    )
    args = parser.parse_args()

    counts: collections.Counter[str] = collections.Counter()
    per_field: dict[str, collections.Counter[str]] = collections.defaultdict(
        collections.Counter
    )
    rows: list[dict[str, object]] = []
    examples: list[dict[str, str]] = []

    paths = documents(args.limit)
    for path in paths:
        try:
            lines = read_lines(path.read_bytes(), deadline_seconds=30.0)
        except Exception as exc:
            for field in FAMILIES:
                counts[IMAGE_QUALITY_FAILURE] += 1
                per_field[field][IMAGE_QUALITY_FAILURE] += 1
                rows.append(
                    {
                        "document": path.name,
                        "field": field,
                        "category": IMAGE_QUALITY_FAILURE,
                        "why": f"engine refused the file ({type(exc).__name__})",
                        "legible_rows": 0,
                    }
                )
            continue

        page = tuple(" ".join(w.text for w in line) for line in lines)
        words = [w.text.upper() for line in lines for w in line if w.text.strip()]
        legible = len(words)
        scored = _scored(read_page(lines), "free_ocr")
        got = {
            "total": scored.total_paise,
            "tax": scored.tax_paise,
            "net": scored.net_paise,
            "party": scored.party,
            "date": scored.date,
        }

        for field in FAMILIES:
            category, why = classify(field, page, words, legible, got[field])
            counts[category] += 1
            per_field[field][category] += 1
            rows.append(
                {
                    "document": path.name,
                    "field": field,
                    "category": category,
                    "why": why,
                    "legible_rows": legible,
                }
            )
            if category in (OCR_LABEL_CORRUPTION, UNKNOWN_LABEL) and len(examples) < 20:
                examples.append(
                    {
                        "document": path.name,
                        "field": field,
                        "category": category,
                        "why": why,
                    }
                )

    args.csv.parent.mkdir(parents=True, exist_ok=True)
    with args.csv.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    total = sum(counts.values())
    print(f"documents {len(paths)}   slots {total}")
    print("\n=== WHY EACH SLOT FOUND NO LABEL ===")
    for name, count in counts.most_common():
        print(f"  {name:30} {count:4}   {count / total * 100:5.1f}%")
    print("\n=== per field ===")
    for field in FAMILIES:
        print(f"  {field:8} {json.dumps(dict(per_field[field]), sort_keys=True)}")
    print("\n=== up to 20 fixable examples (corruption / unknown spelling) ===")
    for one in examples:
        where = f"{one['document'][:34]:36} {one['field']:7}"
        print(f"  {where} {one['category']:24} {one['why'][:44]}")
    print(f"\nCSV: {args.csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
