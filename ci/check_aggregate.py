"""The aggregate gate. Missing evidence blocks the merge.

GitHub's `needs.*.result` is the only thing a merge-queue gate can read, and it
has a failure mode that matters: a job that never ran reports "skipped", which a
naive `if: success()` treats as fine. So does a cancelled job. This script fails
on all of them.

Fails if any expected job is:
    failed
    missing      - the job is not in the results at all
    cancelled
    incomplete   - reported as skipped, so it produced no evidence
    below threshold

The expected job list comes from ci/gates.toml, not from a list typed into a
workflow, so a job added to the contract cannot be silently left out of the gate.
"""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONTRACT = ROOT / "ci" / "gates.toml"

PASSING = frozenset({"success"})
EXPLICIT_FAILURE = frozenset({"failure", "cancelled", "timed_out"})
NO_EVIDENCE = frozenset({"skipped", ""})


def expected_jobs(aggregate_job: str) -> list[str]:
    with CONTRACT.open("rb") as fh:
        data = tomllib.load(fh)
    jobs = {g["job"] for g in data["gate"] if g["status"] == "active"}
    jobs.discard(aggregate_job)
    return sorted(jobs)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--needs",
        required=True,
        help="the GitHub `needs` context as JSON, i.e. ${{ toJSON(needs) }}",
    )
    ap.add_argument("--self", default="ci-gate", help="this job's own name")
    args = ap.parse_args()

    try:
        needs = json.loads(args.needs)
    except json.JSONDecodeError as exc:
        print(f"FAIL - the needs context is not valid JSON: {exc}", file=sys.stderr)
        return 1

    expected = expected_jobs(args.self)
    verdicts: list[tuple[str, str, str]] = []
    ok = True

    for job in expected:
        entry = needs.get(job)
        if entry is None:
            verdicts.append((job, "MISSING", "the job did not run at all"))
            ok = False
            continue
        result = str(entry.get("result", ""))
        if result in PASSING:
            verdicts.append((job, "PASS", result))
        elif result in NO_EVIDENCE:
            verdicts.append(
                (job, "INCOMPLETE", f"{result or 'no result'} - produced no evidence")
            )
            ok = False
        elif result in EXPLICIT_FAILURE:
            verdicts.append((job, "FAIL", result))
            ok = False
        else:
            verdicts.append((job, "UNKNOWN", result))
            ok = False

    unexpected = sorted(set(needs) - set(expected))

    width = max((len(j) for j, _, _ in verdicts), default=10)
    for job, verdict, detail in verdicts:
        print(f"  {job:<{width}}  {verdict:<10} {detail}")
    for job in unexpected:
        print(f"  {job:<{width}}  IGNORED    not in ci/gates.toml")

    if unexpected:
        print(
            "\nFAIL - jobs ran that no gate declares. Every job must be in "
            "ci/gates.toml."
        )
        ok = False

    if not ok:
        print("\nci-gate: FAIL")
        return 1

    print(f"\nci-gate: PASS - {len(expected)} job(s), all reported success")
    return 0


if __name__ == "__main__":
    sys.exit(main())
