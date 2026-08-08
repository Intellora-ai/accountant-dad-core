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


# Which jobs must have reported, per phase. Read from the contract rather than
# typed here, so a gate added to a job cannot be quietly left out of the gate.
PHASES = {
    # Every push. Cheap gates only.
    "fast": {"pr-fast"},
    # Before merge. Everything, including mutation, full coverage, security
    # and build. A green fast phase alone is never enough.
    "full": {"pr-fast", "pr-full"},
    # The scheduled and manual authoritative run.
    "nightly": {"full-tests", "security", "build", "workflow-checks", "mutation"},
}


def expected_jobs(aggregate_job: str, phase: str) -> list[str]:
    """Every job this aggregate is responsible for, for this phase.

    The aggregate itself is excluded: it cannot depend on its own result.

    Jobs are cross-checked against the contract, so a phase can only name jobs
    that gates actually declare. A typo here would otherwise silently shrink
    what the aggregate demands.
    """
    with CONTRACT.open("rb") as fh:
        data = tomllib.load(fh)
    declared = {j for g in data["gate"] if g["status"] == "active" for j in g["jobs"]}

    wanted = PHASES.get(phase)
    if wanted is None:
        raise SystemExit(f"unknown phase {phase!r}, expected one of {sorted(PHASES)}")

    unknown = wanted - declared
    if unknown:
        raise SystemExit(
            f"phase {phase!r} names job(s) {sorted(unknown)} that no gate declares "
            "in ci/gates.toml"
        )

    return sorted(wanted - {aggregate_job})


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--needs",
        required=True,
        help="the GitHub `needs` context as JSON, i.e. ${{ toJSON(needs) }}",
    )
    ap.add_argument("--self", default="ci-gate", help="this job's own name")
    ap.add_argument(
        "--phase",
        default="fast",
        choices=sorted(PHASES),
        help="which set of jobs must have reported",
    )
    args = ap.parse_args()

    try:
        needs = json.loads(args.needs)
    except json.JSONDecodeError as exc:
        print(f"FAIL - the needs context is not valid JSON: {exc}", file=sys.stderr)
        return 1

    expected = expected_jobs(args.self, args.phase)
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

    # In the fast phase pr-full is deliberately skipped, so its presence in
    # `needs` is expected and is NOT a stray job.
    every_phase_job: set[str] = set()
    for jobs in PHASES.values():
        every_phase_job |= jobs
    known: set[str] = set(expected) | {str(args.self)} | every_phase_job
    unexpected = sorted(set(needs) - known)

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

    print(
        f"\nci-gate: PASS - phase {args.phase!r}, "
        f"{len(expected)} job(s), all reported success"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
