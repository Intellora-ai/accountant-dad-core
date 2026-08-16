"""The corpus split, decided by hash and written down before any scoring runs.

WHY A HASH AND NOT A CHOICE
---------------------------
A locked test set exists to stop the tuning loop closing on itself: prompts,
thresholds and label aliases must never be adjusted against documents that later
report the score. That only holds if nobody - including the person writing the
prompt - can influence WHICH documents are locked.

So the split is a function of the file's SHA-256 and nothing else. Sorting by a
content hash is deterministic, reproducible from the files alone, and unrelated
to whether a document is easy or hard. Choosing by eye, by filename, or by "the
first N" would all correlate with something.

    sort every document by sha256
    first 37  -> development   (prompts and thresholds may be tuned here)
    next  12  -> validation
    last  13  -> locked        (touched once, at the end, and never before)

IT REFUSES TO RESHAPE ITSELF. If the corpus does not hold exactly 62 usable
documents the script stops and reports the real count. A split that silently
re-proportions itself would let the locked set shrink as documents are added or
removed, which is the one property it exists to keep.

WRITTEN BEFORE THE FIRST SCORING RUN, not after. `artifacts/docai_split.json`
records the assignment and the per-file hash, so any later claim about which set
a document belonged to is checkable rather than remembered.

NO DOCUMENT CONTENT IS RECORDED - file name and hash only.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
CORPUS = REPO / "data" / "problem1_corpus"
OUT = REPO / "artifacts" / "docai_split.json"

#: Owner-set, 2026-08-16. Not derived from the corpus size and not rounded from
#: a ratio - if the corpus changes size these numbers do not quietly follow it.
DEVELOPMENT = 37
VALIDATION = 12
LOCKED = 13
EXPECTED = DEVELOPMENT + VALIDATION + LOCKED


def main() -> int:
    documents = sorted(p for p in CORPUS.iterdir() if p.is_file())
    if len(documents) != EXPECTED:
        print(
            f"STOP: the corpus holds {len(documents)} usable documents, not "
            f"{EXPECTED}. The split is not reshaped to fit - report the real "
            f"count and get a new one.",
            file=sys.stderr,
        )
        return 1

    hashed = sorted(
        ((hashlib.sha256(p.read_bytes()).hexdigest(), p.name) for p in documents),
    )
    assignment = {
        "development": hashed[:DEVELOPMENT],
        "validation": hashed[DEVELOPMENT : DEVELOPMENT + VALIDATION],
        "locked": hashed[DEVELOPMENT + VALIDATION :],
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(
            {
                "rule": (
                    "sorted by sha256; first 37 development, "
                    "next 12 validation, last 13 locked"
                ),
                "counts": {name: len(rows) for name, rows in assignment.items()},
                "sets": {
                    name: [{"sha256": h, "file": f} for h, f in rows]
                    for name, rows in assignment.items()
                },
            },
            indent=2,
        )
        + "\n"
    )

    for name, rows in assignment.items():
        print(f"  {name:12} {len(rows):3}")
    print(f"\nwritten: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
