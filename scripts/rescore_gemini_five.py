"""Re-score the five SAVED Gemini answers. Zero cloud calls, zero network.

WHY THIS EXISTS
---------------
`artifacts/docai_five_document_report.md` records `GATE: FAIL - incorrect 2`.
`artifacts/docai_decimal_comma_diagnostic.md` then proved both `incorrect` were
the comparator and not the model: `real-voxel51-03.jpg` prints `$ 7,14` and
`$ 78,58`, Gemini returned both exactly, and `paise_or_none` read the comma as a
thousands separator and the `$` as unparseable.

`measure_gemini_five._paise_for` fixed that afterwards. The report was never
regenerated, so the recorded verdict is measured with a ruler that no longer
exists. This re-runs the CURRENT comparator over the SAVED answers.

WHERE THE MODEL'S ANSWERS COME FROM
------------------------------------
The raw results CSV carries a `<field>_why` column for every field, and the
model's own value is inside it - `read '8,414.40'`, `model said '$ 7,14'`,
`model said UNREADABLE`. That is enough to rebuild what Gemini returned and
score it again. No call is made and no invoice is opened.

THE RAW FILE IS NEVER WRITTEN. It is the record of what the model said, and a
re-score is a second reading of it, not a replacement for it. The re-scored
per-document table is a NEW file; only the summary report - which states a
verdict, and whose verdict is now known to be wrong - is regenerated.

THE COMPARATOR IS IMPORTED, NOT REWRITTEN, for the reason
`measure_gemini_five` gives at length: one ruler, both backends.
"""

from __future__ import annotations

import csv
import json
import pathlib
import re
import sys
from typing import Final

REPO = pathlib.Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.gemini_invoice_poc import FIELDS  # noqa: E402
from scripts.measure_gemini_five import (  # noqa: E402
    CORRECT,
    FALSE_POSITIVE,
    INCORRECT,
    MISSING,
    REVIEW,
    _as_count,  # pyright: ignore[reportPrivateUsage]
    _verdict,  # pyright: ignore[reportPrivateUsage]
)

RAW = REPO / "artifacts" / "docai_five_document_results.csv"
TRUTH = REPO / "artifacts" / "problem1_ground_truth.json"
CSV_OUT = REPO / "artifacts" / "docai_five_document_rescored.csv"
MD_OUT = REPO / "artifacts" / "docai_five_document_report.md"

#: The four shapes `_verdict` writes into a `_why` column. Order matters: the
#: `model said` pattern has to be tried before the bare status one, or
#: `model said 'UNREADABLE MESS'` would be read as the status `UNREADABLE`.
_READ: Final = re.compile(r"^read '(?P<value>.*)'$", re.DOTALL)
_SAID: Final = re.compile(r".*model said '(?P<value>.*)'$", re.DOTALL)
_STATUS: Final = re.compile(r"^model said (?P<status>CONFLICTING|UNREADABLE)$")
_NOTHING: Final = re.compile(r"^nothing to find \((?P<status>[A-Z]+)\)")

ACCEPTED: Final = "ACCEPTED_CANDIDATE"


def _answer_from(why: str) -> dict[str, object] | None:
    """Rebuild what the model returned for one field, from the recorded reason.

    `None` means the row recorded no entry at all, which `_verdict` reads as
    `missing` - the same answer it gave the first time.
    """
    why = why.strip()
    status = _STATUS.match(why)
    if status:
        return {"status": status.group("status"), "value": None}
    read = _READ.match(why)
    if read:
        return {"status": ACCEPTED, "value": read.group("value")}
    said = _SAID.match(why)
    if said:
        return {"status": ACCEPTED, "value": said.group("value")}
    nothing = _NOTHING.match(why)
    if nothing:
        # "nothing to find (ABSENT) and nothing returned" - the model answered
        # with no value, and ground truth agrees there was none to give.
        return {"status": "NOT_DETECTED", "value": None}
    if "model returned nothing" in why:
        return {"status": "NOT_DETECTED", "value": None}
    return None


def main() -> int:
    if not RAW.exists():
        print(f"no saved results at {RAW}")
        return 1

    truth_all: dict[str, dict[str, object]] = json.loads(TRUTH.read_text())
    saved = list(csv.DictReader(RAW.open()))

    rows: list[dict[str, object]] = []
    changed: list[str] = []

    for old in saved:
        document = str(old.get("document", ""))
        truth = truth_all.get(document) or {}
        row: dict[str, object] = {
            "document": document,
            "sha256": old.get("sha256", ""),
            "model": old.get("model", ""),
            "latency_ms": old.get("latency_ms", ""),
        }
        tally: dict[str, int] = dict.fromkeys(
            (CORRECT, INCORRECT, MISSING, FALSE_POSITIVE, REVIEW), 0
        )
        for field in FIELDS:
            was = str(old.get(field, ""))
            said = _answer_from(str(old.get(f"{field}_why", "")))
            verdict, why = _verdict(field, truth.get(field), said)
            row[field] = verdict
            row[f"{field}_why"] = why
            row[f"{field}_was"] = was
            tally[verdict] += 1
            if was and was != verdict:
                changed.append(f"{document}.{field}: {was} -> {verdict}")
        row.update(tally)
        rows.append(row)
        print(
            f"  {document:22} {tally[CORRECT]}/5 correct  "
            f"{tally[INCORRECT]} wrong  {tally[REVIEW]} review"
        )

    totals = {
        name: sum(_as_count(r.get(name)) for r in rows)
        for name in (CORRECT, INCORRECT, MISSING, FALSE_POSITIVE, REVIEW)
    }
    exact = sum(1 for r in rows if _as_count(r.get(CORRECT)) == len(FIELDS))
    gate_ok = totals[INCORRECT] <= 1 and totals[FALSE_POSITIVE] == 0

    fields = sorted({k for r in rows for k in r})
    with CSV_OUT.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    lines = [
        "# Gemini - five development documents (RE-SCORED)",
        "",
        f"model `{rows[0]['model'] if rows else ''}` - {len(rows)} documents - "
        f"{len(rows) * len(FIELDS)} field slots",
        "",
        "**Re-scored 2026-08-16 from the saved answers in "
        "`docai_five_document_results.csv`. Zero cloud calls, zero network "
        "calls, no invoice reopened, raw results unchanged.**",
        "",
        "| metric | count |",
        "|---|---|",
        *[f"| {n} | {v} |" for n, v in totals.items()],
        f"| documents with all five correct | {exact} of {len(rows)} |",
        "",
        f"**GATE: {'PASS' if gate_ok else 'FAIL'}** - incorrect "
        f"{totals[INCORRECT]} (limit 1), false positives "
        f"{totals[FALSE_POSITIVE]} (limit 0)",
        "",
        "## What changed against the first scoring",
        "",
    ]
    lines += [f"- `{entry}`" for entry in changed] or ["- nothing changed"]
    lines += [
        "",
        "The first run recorded `incorrect 2` and therefore `GATE: FAIL`. Both "
        "were `real-voxel51-03`, whose page prints `$ 7,14` and `$ 78,58`; "
        "Gemini returned both exactly and the comparator could parse neither - "
        "`$` was not stripped and the comma was always read as a thousands "
        "separator. `measure_gemini_five._paise_for` reads the comma by the "
        "currency the ground truth states, and it is the only thing that moved. "
        "The model's answers are byte-for-byte the ones it gave.",
        "",
        "Five documents is not the 37-document gate. Validation and locked sets "
        "untouched. No Tally write, no cage submission.",
    ]
    MD_OUT.write_text("\n".join(lines) + "\n")

    print(f"\n{'metric':18} count")
    for name, value in totals.items():
        print(f"  {name:16} {value}")
    print(f"  {'exact match':16} {exact} of {len(rows)}")
    print(f"\n  GATE: {'PASS' if gate_ok else 'FAIL'}")
    print(f"\n  raw (unchanged) {RAW}\n  {CSV_OUT}\n  {MD_OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
