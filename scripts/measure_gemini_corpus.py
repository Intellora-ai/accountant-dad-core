"""The 37 development documents through Gemini, scored by the EXISTING comparators.

This is Step 3 of the plan, and the plan named this file. It is the five-document
run widened to the development split, and it differs from it in three ways only:

    it calls only the documents that have no answer yet     (scored ones resume)
    it records the API's own token counts                    (`_ask_measured`)
    it writes its own artifacts                              (nothing raw is touched)

THE COMPARATOR IS IMPORTED, NOT REWRITTEN. `_verdict` comes from
`measure_gemini_five`, which in turn imports `_same_money`, `_same_date` and
`_same_party` from `measure_problem1_corpus`. One ruler, both backends - the
same rule that caught four comparator faults in this project already.

IT RESUMES RATHER THAN RESTARTS. Every document already carrying a verdict -
from `docai_accuracy_by_document.csv` if it exists, otherwise from the offline
re-score of the first five - is reused as it stands. Only documents with no
verdict, or whose last attempt failed, are called. Re-calling a scored document
would spend money to learn nothing and would overwrite a recorded answer.

WHY THE FAILURE COLUMNS EXIST
------------------------------
The first 37-document run lost 22 of 32 calls and recorded only the exception
CLASS. `HTTPError` alone cannot tell a rate limit from a bad request, so the
run could not say whether retrying was worth anything. Every failure now
records status, provider code, whether it is retryable, how long it ran and how
many attempts it took.

NOTHING SENSITIVE IS WRITTEN. The key is never logged. Provider messages are
truncated and scrubbed of the key. No invoice byte, filename path or document
content reaches the diagnostics file - only the document id the split already
publishes.

Nothing here reaches the cage, the pipeline or Tally. Nothing imports this module.
"""

from __future__ import annotations

import csv
import datetime
import json
import pathlib
import statistics
import sys
import time
import urllib.error
from typing import Final, cast

REPO = pathlib.Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.gemini_invoice_poc import (  # noqa: E402
    FIELDS,
    _ask_measured,  # pyright: ignore[reportPrivateUsage]
    _configured,  # pyright: ignore[reportPrivateUsage]
)
from scripts.measure_gemini_five import (  # noqa: E402
    CORRECT,
    FALSE_POSITIVE,
    INCORRECT,
    MISSING,
    REVIEW,
    _verdict,  # pyright: ignore[reportPrivateUsage]
)

CORPUS = REPO / "data" / "problem1_corpus"
TRUTH = REPO / "artifacts" / "problem1_ground_truth.json"
SPLIT = REPO / "artifacts" / "docai_split.json"
FIVE = REPO / "artifacts" / "docai_five_document_rescored.csv"

BY_DOCUMENT = REPO / "artifacts" / "docai_accuracy_by_document.csv"
BY_FIELD = REPO / "artifacts" / "docai_accuracy_by_field.csv"
FAILURES = REPO / "artifacts" / "docai_call_failures.csv"
REPORT = REPO / "artifacts" / "docai_37_document_report.md"

#: `MAX_ATTEMPTS_PER_DOCUMENT` IS THE OWNER'S, GIVEN 2026-08-16: "try to re
#: run only 2 times". `CALL_CEILING` was chosen here and is
#: written down rather than left implicit so they can be argued with. No call
#: limit and no cost limit is configured anywhere in this repository; "bounded
#: retry" needs a bound, and an unstated one is worse than a stated one.
#: `CALL_CEILING` is the hard stop: the run ends when it is reached, whatever
#: is left unattempted, and says so.
MAX_ATTEMPTS_PER_DOCUMENT: Final = 2  # owner's number, 2026-08-16
CALL_CEILING: Final = 66

#: Seconds to wait before attempt 2 and attempt 3. Two entries because three
#: attempts have two gaps between them.
BACKOFF_SECONDS: Final[tuple[int, ...]] = (5, 20)

#: Ordinary HTTP transport semantics, not a policy: these mean "the request was
#: fine, the server could not serve it now". Everything else - 400, 401, 403,
#: 404 - means the request itself is wrong, and repeating a wrong request is
#: how a bounded retry becomes an unbounded one.
_RETRYABLE_STATUS: Final[frozenset[int]] = frozenset({408, 429, 500, 502, 503, 504})

_MIME: Final = {".png": "image/png", ".jpg": "image/jpeg", ".pdf": "application/pdf"}
_OUTCOMES: Final[tuple[str, ...]] = (
    CORRECT,
    INCORRECT,
    MISSING,
    FALSE_POSITIVE,
    REVIEW,
)
_FAILED_PREFIX: Final = "CALL_FAILED"
_MESSAGE_LIMIT: Final = 200


def _now() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")


def _scrubbed(text: str, key: str) -> str:
    """The provider's words, truncated, with the key removed if it ever appears.

    The key travels in a header and Google does not echo it, so this is a belt
    for a brace that already holds - which is the right way round for a secret.
    """
    safe = text.replace(key, "REDACTED") if key else text
    safe = " ".join(safe.split())
    return safe[:_MESSAGE_LIMIT]


def _diagnose(problem: BaseException, key: str) -> dict[str, object]:
    """Everything knowable about one failed call, and nothing sensitive.

    `HTTPError` IS ITSELF A RESPONSE, so its body carries the provider's own
    error object - `{"error": {"code": 429, "status": "RESOURCE_EXHAUSTED",
    "message": ...}}`. That body is the whole reason this function exists: the
    class name alone said `HTTPError` twenty-two times and explained none of them.
    """
    found: dict[str, object] = {
        "exception": type(problem).__name__,
        "http_status": "",
        "provider_code": "",
        "provider_status": "",
        "provider_message": "",
    }
    if isinstance(problem, urllib.error.HTTPError):
        found["http_status"] = problem.code
        try:
            body = problem.read().decode("utf-8", errors="replace")
        except Exception:  # a body we cannot read is not a crash
            body = ""
        if body:
            whole: dict[str, object] = {}
            try:
                whole = cast("dict[str, object]", json.loads(body))
            except json.JSONDecodeError:
                whole = {}
            stated = whole.get("error")
            if isinstance(stated, dict):
                said = cast("dict[str, object]", stated)
                found["provider_code"] = said.get("code", "")
                found["provider_status"] = said.get("status", "")
                found["provider_message"] = _scrubbed(str(said.get("message", "")), key)
            if not found["provider_message"]:
                found["provider_message"] = _scrubbed(body, key)
    else:
        found["provider_message"] = _scrubbed(str(problem), key)
    return found


def _retryable(found: dict[str, object]) -> bool:
    """Is this worth a second call, or is repeating it just spending money?"""
    status = found.get("http_status")
    if isinstance(status, int):
        return status in _RETRYABLE_STATUS
    # No status at all means the request never got an answer - a timeout or a
    # dropped socket. Those are the transport failures retrying exists for.
    return found["exception"] in {"TimeoutError", "URLError", "socket.timeout"}


def _previous() -> dict[str, dict[str, str]]:
    """Every document that already carries a verdict, keyed by document id.

    `BY_DOCUMENT` first because it is this runner's own output and therefore the
    most complete; the five-document re-score is the fallback for a first run.
    Rows whose source records a failure are NOT returned - those are exactly the
    ones to call again.
    """
    for source in (BY_DOCUMENT, FIVE):
        if not source.exists():
            continue
        rows = {
            str(row["document"]): dict(row)
            for row in csv.DictReader(source.open())
            if not str(row.get("source", "")).startswith(_FAILED_PREFIX)
        }
        if rows:
            return rows
    return {}


def _call_bounded(
    data: bytes, mime: str, key: str, model: str
) -> tuple[dict[str, object] | None, int, dict[str, int], dict[str, object], int]:
    """Up to `MAX_ATTEMPTS_PER_DOCUMENT` attempts. Returns the last outcome.

    Returns `(answer, latency, usage, failure, attempts)`. `answer` is `None`
    exactly when every attempt failed, and `failure` is empty exactly when one
    succeeded - so a caller cannot read a success as a failure by looking at the
    wrong field.
    """
    failure: dict[str, object] = {}
    started = time.monotonic()
    for attempt in range(1, MAX_ATTEMPTS_PER_DOCUMENT + 1):
        try:
            answer, latency, usage = _ask_measured(data, mime, key, model)
        except Exception as problem:  # every failure is data here
            failure = _diagnose(problem, key)
            failure["retryable"] = _retryable(failure)
            failure["attempts"] = attempt
            failure["elapsed_ms"] = int((time.monotonic() - started) * 1000)
            if not failure["retryable"] or attempt == MAX_ATTEMPTS_PER_DOCUMENT:
                return None, 0, {}, failure, attempt
            time.sleep(BACKOFF_SECONDS[min(attempt - 1, len(BACKOFF_SECONDS) - 1)])
            continue
        return answer, latency, usage, {}, attempt
    return None, 0, {}, failure, MAX_ATTEMPTS_PER_DOCUMENT


def main() -> int:
    key, model = _configured()
    development = json.loads(SPLIT.read_text())["sets"]["development"]
    truth_all: dict[str, dict[str, object]] = json.loads(TRUTH.read_text())
    done = _previous()

    outstanding = [
        e for e in development if pathlib.Path(str(e["file"])).stem not in done
    ]
    print(f"model: {model}   documents: {len(development)}   (development only)")
    print(f"already scored: {len(done)}   to call: {len(outstanding)}")
    print(
        f"bounds: {MAX_ATTEMPTS_PER_DOCUMENT} attempts each, {CALL_CEILING} calls max\n"
    )

    rows: list[dict[str, object]] = []
    failures: list[dict[str, object]] = []
    latencies: list[int] = []
    tokens: dict[str, int] = {}
    calls = 0
    ceiling_hit = False

    for entry in development:
        path = CORPUS / str(entry["file"])
        document = path.stem
        truth = truth_all.get(document) or truth_all.get(path.name) or {}
        row: dict[str, object] = {
            "document": document,
            "sha256": str(entry["sha256"])[:16],
            "model": model,
            "at": _now(),
        }

        if document in done:
            old = done[document]
            row["source"] = old.get("source", "previously scored")
            row["at"] = old.get("at", "")
            row["latency_ms"] = old.get("latency_ms", "")
            tally: dict[str, int] = dict.fromkeys(_OUTCOMES, 0)
            for field in FIELDS:
                verdict = str(old.get(field, ""))
                row[field] = verdict
                row[f"{field}_why"] = old.get(f"{field}_why", "")
                if verdict in tally:
                    tally[verdict] += 1
            row.update(tally)
            rows.append(row)
            continue

        if calls >= CALL_CEILING:
            ceiling_hit = True
            row["source"] = "NOT_ATTEMPTED: call ceiling reached"
            row["latency_ms"] = ""
            for field in FIELDS:
                row[field] = ""
                row[f"{field}_why"] = "not attempted - call ceiling reached"
            row.update(dict.fromkeys(_OUTCOMES, 0))
            rows.append(row)
            continue

        data = path.read_bytes()
        mime = _MIME.get(path.suffix.lower(), "application/octet-stream")
        answer, latency, usage, failure, attempts = _call_bounded(
            data, mime, key, model
        )
        calls += attempts

        if answer is None:
            record = {"document": document, "at": _now(), "model": model, **failure}
            failures.append(record)
            row["source"] = f"{_FAILED_PREFIX}:{failure.get('exception', 'Unknown')}"
            row["latency_ms"] = ""
            row["attempts"] = attempts
            for field in FIELDS:
                row[field] = ""
                row[f"{field}_why"] = "call failed"
            row.update(dict.fromkeys(_OUTCOMES, 0))
            rows.append(row)
            print(
                f"  {path.name:26} FAILED {failure.get('exception')} "
                f"status={failure.get('http_status')} "
                f"retryable={failure.get('retryable')} attempts={attempts}"
            )
            continue

        latencies.append(latency)
        for name, count in usage.items():
            tokens[name] = tokens.get(name, 0) + count
        row["source"] = "called"
        row["latency_ms"] = latency
        row["attempts"] = attempts
        for name, count in usage.items():
            row[name] = count
        tally = dict.fromkeys(_OUTCOMES, 0)
        for field in FIELDS:
            verdict, why = _verdict(field, truth.get(field), answer.get(field))
            row[field] = verdict
            row[f"{field}_why"] = why
            tally[verdict] += 1
        row.update(tally)
        rows.append(row)
        print(
            f"  {path.name:26} {tally[CORRECT]}/5 correct  "
            f"{tally[REVIEW]} review  {latency // 1000}s  attempts={attempts}"
        )

    scored = [
        r
        for r in rows
        if not str(r.get("source", "")).startswith((_FAILED_PREFIX, "NOT_ATTEMPTED"))
    ]
    totals = {n: sum(int(str(r.get(n, 0) or 0)) for r in rows) for n in _OUTCOMES}
    exact = sum(1 for r in scored if int(str(r.get(CORRECT, 0) or 0)) == len(FIELDS))

    by_field: list[dict[str, object]] = []
    for field in FIELDS:
        counts: dict[str, int] = dict.fromkeys(_OUTCOMES, 0)
        for r in scored:
            verdict = str(r.get(field, ""))
            if verdict in counts:
                counts[verdict] += 1
        by_field.append({"field": field, **counts})

    BY_DOCUMENT.parent.mkdir(parents=True, exist_ok=True)
    with BY_DOCUMENT.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=sorted({k for r in rows for k in r}))
        writer.writeheader()
        writer.writerows(rows)
    with BY_FIELD.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["field", *_OUTCOMES])
        writer.writeheader()
        writer.writerows(by_field)
    if failures:
        with FAILURES.open("w", newline="") as handle:
            writer = csv.DictWriter(
                handle, fieldnames=sorted({k for r in failures for k in r})
            )
            writer.writeheader()
            writer.writerows(failures)

    gate_ok = totals[INCORRECT] <= 1 and totals[FALSE_POSITIVE] == 0
    token_lines: list[str] = [
        f"{name} {count}" for name, count in sorted(tokens.items())
    ] or ["the API reported no usageMetadata this run"]
    lines: list[str] = [
        "# Gemini - the 37 development documents",
        "",
        f"model `{model}` - {len(scored)} of {len(rows)} documents scored - "
        f"{len(scored) * len(FIELDS)} scored field slots",
        f"cloud calls this run: {calls} (ceiling {CALL_CEILING}, "
        f"{MAX_ATTEMPTS_PER_DOCUMENT} attempts per document)",
        "",
        "| metric | count |",
        "|---|---|",
        *[f"| {n} | {v} |" for n, v in totals.items()],
        f"| documents with all five correct | {exact} of {len(scored)} |",
        "",
        "## By field",
        "",
        "| field | " + " | ".join(_OUTCOMES) + " |",
        "|---|" + "---|" * len(_OUTCOMES),
        *[
            f"| {r['field']} | " + " | ".join(str(r[n]) for n in _OUTCOMES) + " |"
            for r in by_field
        ],
        "",
        f"**GATE: {'PASS' if gate_ok else 'FAIL'}** - incorrect "
        f"{totals[INCORRECT]} (limit 1), false positives "
        f"{totals[FALSE_POSITIVE]} (limit 0)",
        "",
        "## Tokens, as the API reported them",
        "",
        "```",
        *token_lines,
        "```",
        "",
        "Cost is NOT MEASURED: no price table is configured in this repository, "
        "and a rate nobody supplied would be an invented figure.",
        "",
        "LINE ITEMS: NOT MEASURED - neither the response schema "
        "(`gemini_invoice_poc.FIELDS`) nor `problem1_ground_truth.json` carries "
        "them. SUBTOTAL and CURRENCY: not scored fields either.",
        "",
        "Validation and locked sets untouched. No Tally write, no cage submission.",
    ]
    if ceiling_hit:
        lines += [
            "",
            f"**CALL CEILING {CALL_CEILING} REACHED** - documents remain unattempted.",
        ]
    if failures:
        lines += [
            "",
            "## Calls that failed",
            "",
            "| document | exception | status | provider | retryable | attempts |",
            "|---|---|---|---|---|---|",
            *[
                f"| {f['document']} | {f.get('exception')} | {f.get('http_status')} "
                f"| {f.get('provider_status')} | {f.get('retryable')} "
                f"| {f.get('attempts')} |"
                for f in failures
            ],
        ]
    REPORT.write_text("\n".join(lines) + "\n")

    print(f"\n{'metric':18} count")
    for name, value in totals.items():
        print(f"  {name:16} {value}")
    print(f"  {'exact match':16} {exact} of {len(scored)}")
    print(f"\n  cloud calls {calls}   documents failed {len(failures)}")
    if latencies:
        print(
            f"  latency avg {int(statistics.mean(latencies)) // 1000}s  "
            f"slowest {max(latencies) // 1000}s"
        )
    print(f"  tokens {sorted(tokens.items())}")
    print(f"\n  GATE: {'PASS' if gate_ok else 'FAIL'}")
    print(f"\n  {BY_DOCUMENT}\n  {BY_FIELD}\n  {REPORT}")
    if failures:
        print(f"  {FAILURES}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
