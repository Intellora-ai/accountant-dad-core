"""Mutation accounting. The score is only trustworthy if the inventory is.

A partial score is a number that looks like evidence and is not. If any mutant
is missing a terminal result, or IDs are missing or duplicated, this reports
FAIL_INCOMPLETE with score_percent = null - never a partial percentage, never
zero, because zero would read as "we measured and it was terrible" rather than
"we did not measure".

Owner rules enforced here:
    exact mutant inventory
    stable mutant IDs
    missing IDs = 0
    duplicate IDs = 0
    every mutant has a terminal result
    survivors reproducible
    score >= 90

Also refuses to grade a run made without COVERAGE_CORE=pytrace. Measured on this
repository 2026-08-07: on the default sysmon core, dynamic contexts are silently
unsupported, mutants run against as few as 1 test instead of 22, and the score
reads 87.92 percent instead of 94.34 percent. Same code, same tests.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, NoReturn, TypedDict, cast


class Mutant(TypedDict, total=False):
    gremlin_id: str
    file_path: str
    line_number: int
    status: str
    description: str


class Summary(TypedDict, total=False):
    total: int
    percentage: float


THRESHOLD = 90  # owner-set 2026-08-07
TERMINAL = frozenset({"zapped", "survived", "timeout", "error", "pardoned"})
REQUIRED_CORE = "pytrace"


def fail(reason: str, *, incomplete: bool = False) -> NoReturn:
    verdict = "FAIL_INCOMPLETE" if incomplete else "FAIL"
    score = "null" if incomplete else "see above"
    print(f"{verdict}\nscore_percent = {score}\n{reason}", file=sys.stderr)
    raise SystemExit(1)


def load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        fail(f"no mutation report at {path}", incomplete=True)
    try:
        with path.open() as fh:
            data: dict[str, Any] = json.load(fh)
    except json.JSONDecodeError as exc:
        fail(f"mutation report at {path} is not valid JSON: {exc}", incomplete=True)
    return data


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--report",
        type=Path,
        default=Path("coverage/gremlins/gremlins.json"),
        help="pytest-gremlins JSON report",
    )
    args = ap.parse_args()

    core = os.environ.get("COVERAGE_CORE", "")
    if core != REQUIRED_CORE:
        fail(
            f"COVERAGE_CORE is {core or 'unset'}, must be {REQUIRED_CORE!r}. "
            "On any other core the test-to-line mapping is silently incomplete "
            "and the score under-reports. Refusing to grade this run.",
            incomplete=True,
        )

    data = load(args.report)
    summary = cast(Summary, data.get("summary") or {})
    results = cast(list[Mutant], data.get("results") or [])

    if not results:
        fail("mutation report contains no mutants", incomplete=True)

    declared = summary.get("total")
    if declared != len(results):
        fail(
            f"inventory is not exact: summary says {declared} mutants, "
            f"results list has {len(results)}",
            incomplete=True,
        )

    ids = [m.get("gremlin_id", "") for m in results]
    missing = sum(1 for i in ids if not i)
    if missing:
        fail(f"missing IDs = {missing}, must be 0", incomplete=True)

    duplicates = len(ids) - len(set(ids))
    if duplicates:
        fail(f"duplicate IDs = {duplicates}, must be 0", incomplete=True)

    non_terminal = [m for m in results if m.get("status") not in TERMINAL]
    if non_terminal:
        sample = ", ".join(m.get("gremlin_id", "?") for m in non_terminal[:5])
        fail(
            f"{len(non_terminal)} mutants have no terminal result (e.g. {sample})",
            incomplete=True,
        )

    survivors = [m for m in results if m.get("status") == "survived"]
    unreproducible = [m for m in survivors if not m.get("file_path")]
    if unreproducible:
        fail(
            f"{len(unreproducible)} survivors cannot be reproduced: no file path",
            incomplete=True,
        )

    # The mapping must be COMPLETE, not merely present. A mutant with no tests
    # selected was never really challenged: nothing ran against it, so
    # "survived" says nothing about the test suite.
    #
    # This is the concrete shape of the sysmon failure. pytest-gremlins switches
    # coverage context once per mutant; on a core that honours only the first
    # switch, later mutants map to too few tests or none at all. Isolated
    # 2026-08-08: three switches, sysmon recorded one, pytrace recorded three.
    unmapped = [m for m in results if not m.get("selected_tests")]
    if unmapped:
        sample = ", ".join(
            f"{m.get('file_path', '?').split('/')[-1]}:{m.get('line_number', '?')}"
            for m in unmapped[:5]
        )
        fail(
            f"{len(unmapped)} of {len(results)} mutants had NO tests selected "
            f"(e.g. {sample}). The test-to-line mapping is incomplete, so the "
            "score is not evidence. Check COVERAGE_CORE and see "
            "tests/test_mutation_environment.py.",
            incomplete=True,
        )

    thin = min(len(m.get("selected_tests", [])) for m in results)
    print(f"fewest tests selected for any mutant: {thin}")

    raw = summary.get("percentage")
    if not isinstance(raw, int | float):
        fail(f"summary has no numeric percentage: {raw!r}", incomplete=True)
    score = float(raw)

    print(f"mutants          {len(results)}")
    print(f"unique IDs       {len(set(ids))}")
    print("missing IDs      0")
    print("duplicate IDs    0")
    print(f"terminal results {len(results)}/{len(results)}")
    print(f"survivors        {len(survivors)}")
    print(f"score_percent    {score:.2f}")
    print(f"threshold        {THRESHOLD}")

    if score < THRESHOLD:
        print(f"\nFAIL\nscore {score:.2f} is below the threshold of {THRESHOLD}")
        for m in survivors[:20]:
            print(
                f"  survived {m.get('file_path')}:{m.get('line_number')} "
                f"{m.get('description', '')}"
            )
        return 1

    print("\nPASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
