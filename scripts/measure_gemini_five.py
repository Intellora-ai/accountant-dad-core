"""Five development documents through Gemini, scored by the EXISTING comparators.

WHY FIVE AND NOT THIRTY-SEVEN
------------------------------
One document has already proved the call works. Thirty-seven is roughly seventy
minutes of wall clock and thirty-seven cloud calls. Five is the smallest run that
can still show a WRONG value, which is the only number the gate cares about, and
it costs about ten minutes. Owner's decision, 2026-08-16.

THE COMPARATORS ARE IMPORTED, NOT REWRITTEN
--------------------------------------------
`_same_money`, `_same_date` and `_same_party` come from
`scripts/measure_problem1_corpus.py`. A second implementation would be a second
opinion about the same truth, and this repository has already paid for that
lesson three times: a money comparator once scored 50 exact matches as INCORRECT,
and a date comparator did the same to 33. Both argued for "fixing" a reader that
was already right. One ruler, both backends.

WHAT IS SCORED, AND WHAT REVIEW_REQUIRED MEANS
-----------------------------------------------
    correct           a value came back and it matches ground truth
    incorrect         a value came back and it does not. THE GATE.
    missing           ground truth says PRESENT, the model returned nothing
    false positive    ground truth says ABSENT, the model answered anyway
    review-required   the model said CONFLICTING or UNREADABLE

A `CONFLICTING` on a field ground truth calls PRESENT is counted as
REVIEW-REQUIRED, never as correct. It is safer than a wrong accepted value and
it is still not an extraction success - owner's correction, and the distinction
is the whole reason the two columns are separate.

THE GATE
    incorrect       <= 1
    false positives  = 0

Fields found is reported and never overrides either.

NO CLOUD CALL HAPPENS UNDER pytest - this is a script, run by hand, and nothing
imports it. Nothing here reaches the cage, the pipeline or Tally. The key is
read from the environment and never printed.
"""

from __future__ import annotations

import csv
import json
import pathlib
import re
import statistics
import sys
import time
from typing import Final

REPO = pathlib.Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.gemini_invoice_poc import (  # noqa: E402
    FIELDS,
    _ask,  # pyright: ignore[reportPrivateUsage]
    _configured,  # pyright: ignore[reportPrivateUsage]
    json_object,
)
from scripts.measure_problem1_corpus import (  # noqa: E402
    _same_date,  # pyright: ignore[reportPrivateUsage]
    _same_money,  # pyright: ignore[reportPrivateUsage]
    _same_party,  # pyright: ignore[reportPrivateUsage]
)

CORPUS = REPO / "data" / "problem1_corpus"
TRUTH = REPO / "artifacts" / "problem1_ground_truth.json"
SPLIT = REPO / "artifacts" / "docai_split.json"
CSV_OUT = REPO / "artifacts" / "docai_five_document_results.csv"
MD_OUT = REPO / "artifacts" / "docai_five_document_report.md"

HOW_MANY = 5

#: One comma, then EXACTLY two digits, and no dot anywhere. `7,14` and `78,58`
#: match; `1,234` does not (three digits), `1,35,938.36` does not (a dot), and
#: `1,234,56` does not (two commas). Narrow on purpose - anything this does not
#: match goes to the production parser untouched.
_A_DECIMAL_COMMA: Final = re.compile(r"-?\d+,\d{2}")
NOTHING_TO_FIND = frozenset({"ABSENT", "AMBIGUOUS", "UNREADABLE"})
CORRECT, INCORRECT, MISSING = "correct", "incorrect", "missing"
FALSE_POSITIVE, REVIEW = "false positive", "review-required"

_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".pdf": "application/pdf"}


def _paise_for(text: str, currency: str) -> int | None:
    """Money as integer paise, reading the comma by the STATED currency.

    THIS LIVES IN THE EVALUATOR AND MUST NEVER MOVE INTO `paise_or_none`.
    Owner decision, 2026-08-16, and the reason is that the string is genuinely
    ambiguous:

        "1,35"   ->  INR 135.00   under Indian grouping
                 ->  USD   1.35   under European decimal-comma notation

    `paise_or_none` is handed characters and nothing else - no locale, no
    currency, no document. A rule there would have to GUESS, and the guess that
    gets it wrong is off by a factor of 100. The same argument already settled
    dates: `DateLocale.UNKNOWN` refuses `11/08/2026` rather than pick a
    convention, and this is that decision one field over.

    AN EVALUATOR KNOWS SOMETHING THE PARSER CANNOT. The ground truth records the
    currency beside the text:

        {"paise": 714, "text": "7,14", "currency": "USD"}

    So here, and only here, `USD` licenses reading a single comma before exactly
    two digits as a decimal point. Everything else falls through to the
    production parser unchanged.

    MEASURED, the failure this fixes: `real-voxel51-03.jpg` prints `$ 7,14` and
    `$ 78,58`. Gemini returned both exactly, including the comma. The evaluator
    called them INCORRECT - twice - and that was the fourth time a comparator in
    this project has accused a correct reader.
    """
    from accountant.labels import paise_or_none

    cleaned = text.strip().upper().replace("$", "").replace(" ", "").strip()
    if currency.upper() == "USD" and _A_DECIMAL_COMMA.fullmatch(cleaned):
        return paise_or_none(cleaned.replace(",", "."))
    return paise_or_none(text)


def _as_count(value: object) -> int:
    """One tally cell as a number - `int(value or 0)` with the type made visible.

    The five tally columns are written as integers by the two `main`s that build
    these rows, so this is the same arithmetic `int(r.get(name, 0) or 0)` did.
    It exists so a count can be SEEN to be a count: a row is a
    `dict[str, object]` because it also carries the document name and the
    verdicts, and `int()` will not take an `object`.
    """
    if not value:
        return 0
    if isinstance(value, int | float | str):
        return int(value)
    raise TypeError(f"tally cell {value!r} is not a count")


def _verdict(field: str, truth: object, said: object) -> tuple[str, str]:
    """One of the five outcomes, and the evidence for it."""
    ground = json_object(truth)
    if ground is None:
        return REVIEW, "no ground truth for this field"
    status = str(ground.get("status", "")).upper()
    raw = ground.get("value")
    nested = json_object(raw)
    expected: dict[str, object] | str = nested if nested is not None else str(raw or "")

    answered = json_object(said)
    if answered is None:
        return MISSING, "model returned no entry for this field"
    model_status = str(answered.get("status", ""))
    value = answered.get("value")

    if model_status in ("CONFLICTING", "UNREADABLE"):
        return REVIEW, f"model said {model_status}"
    if value in (None, ""):
        if status in NOTHING_TO_FIND:
            return CORRECT, f"nothing to find ({status}) and nothing returned"
        return MISSING, f"expected {expected!r}, model returned nothing"
    if status in NOTHING_TO_FIND:
        return FALSE_POSITIVE, f"nothing to find ({status}) but model said {value!r}"

    got = str(value)
    if field in ("tax", "total"):
        want = expected.get("paise") if isinstance(expected, dict) else None
        currency = (
            str(expected.get("currency", "")) if isinstance(expected, dict) else ""
        )
        read = _paise_for(got, currency)
        same = read == want if want is not None else _same_money(read, expected)
    elif field == "party":
        same = _same_party(got, str(expected))
    elif field == "invoice_date":
        import datetime

        parsed = None
        for shape in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%m/%d/%Y"):
            try:
                parsed = datetime.datetime.strptime(got, shape).date()
                break
            except ValueError:
                continue
        same = _same_date(parsed, str(expected)) if parsed else False
    else:
        same = got.strip() == str(expected).strip()

    if same:
        return CORRECT, f"read {got!r}"
    return INCORRECT, f"expected {expected!r}, model said {got!r}"


def main() -> int:
    key, model = _configured()
    split = json.loads(SPLIT.read_text())
    chosen = split["sets"]["development"][:HOW_MANY]
    truth_all: dict[str, dict[str, object]] = json.loads(TRUTH.read_text())

    print(f"model: {model}   documents: {len(chosen)}   (development set only)\n")

    rows: list[dict[str, object]] = []
    latencies: list[int] = []
    started_all = time.monotonic()

    for entry in chosen:
        path = CORPUS / str(entry["file"])
        data = path.read_bytes()
        mime = _MIME.get(path.suffix.lower(), "application/octet-stream")
        truth = truth_all.get(path.stem) or truth_all.get(path.name) or {}
        try:
            answer, latency = _ask(data, mime, key, model)
        except Exception as problem:
            print(f"  {path.name:26} CALL FAILED: {type(problem).__name__}")
            rows.append(
                {
                    "document": path.stem,
                    "sha256": str(entry["sha256"])[:16],
                    "model": model,
                    "latency_ms": "",
                    "outcome": f"CALL_FAILED:{type(problem).__name__}",
                }
            )
            continue
        latencies.append(latency)
        row: dict[str, object] = {
            "document": path.stem,
            "sha256": str(entry["sha256"])[:16],
            "model": model,
            "latency_ms": latency,
        }
        tally: dict[str, int] = dict.fromkeys(
            (CORRECT, INCORRECT, MISSING, FALSE_POSITIVE, REVIEW), 0
        )
        for field in FIELDS:
            verdict, why = _verdict(field, truth.get(field), answer.get(field))
            row[field] = verdict
            row[f"{field}_why"] = why
            tally[verdict] += 1
        row.update(tally)
        rows.append(row)
        print(
            f"  {path.name:26} {tally[CORRECT]}/5 correct  "
            f"{tally[INCORRECT]} wrong  {tally[MISSING]} missing  {latency // 1000}s"
        )

    elapsed = int(time.monotonic() - started_all)
    totals = {
        name: sum(_as_count(r.get(name)) for r in rows)
        for name in (CORRECT, INCORRECT, MISSING, FALSE_POSITIVE, REVIEW)
    }

    CSV_OUT.parent.mkdir(parents=True, exist_ok=True)
    fields = sorted({k for r in rows for k in r})
    with CSV_OUT.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    gate_ok = totals[INCORRECT] <= 1 and totals[FALSE_POSITIVE] == 0
    lines = [
        "# Gemini — five development documents",
        "",
        f"model `{model}` · {len(rows)} documents · "
        f"{len(rows) * len(FIELDS)} field slots",
        "",
        "| metric | count |",
        "|---|---|",
        *[f"| {n} | {v} |" for n, v in totals.items()],
        "",
        f"average latency {int(statistics.mean(latencies)) // 1000}s · "
        f"median {int(statistics.median(latencies)) // 1000}s · "
        f"slowest {max(latencies) // 1000}s · total elapsed {elapsed}s"
        if latencies
        else "no successful calls",
        "",
        f"**GATE: {'PASS' if gate_ok else 'FAIL'}** — incorrect "
        f"{totals[INCORRECT]} (limit 1), false positives "
        f"{totals[FALSE_POSITIVE]} (limit 0)",
        "",
        "Validation and locked sets untouched. No Tally write, no cage submission.",
    ]
    MD_OUT.write_text("\n".join(lines) + "\n")

    print(f"\n{'metric':18} count")
    for name, value in totals.items():
        print(f"  {name:16} {value}")
    if latencies:
        print(
            f"\n  latency  avg {int(statistics.mean(latencies)) // 1000}s  "
            f"median {int(statistics.median(latencies)) // 1000}s  "
            f"slowest {max(latencies) // 1000}s  elapsed {elapsed}s"
        )
    print(f"\n  GATE: {'PASS' if gate_ok else 'FAIL'}")
    print(f"\n  {CSV_OUT}\n  {MD_OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
