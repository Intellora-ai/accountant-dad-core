"""p50 and p95 for the required check, from the GitHub Actions API.

No separate duration database is built. GitHub already stores every run's start
and end time, so building a second store would be a second thing to keep correct.

There is no owner-selected minimum sample. The sample count is always printed
alongside the numbers, and a small sample is labelled `provisional` rather than
withheld or invented. "p95 over n=3, provisional" is honest: the arithmetic is
real and the reader can see exactly how much to trust it. Withholding it would
hide information; inventing a threshold would be a number nobody set.

    python ci/report_runtimes.py
    python ci/report_runtimes.py --json
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CONTRACT = ROOT / "ci" / "gates.toml"

# Below this many samples the result is labelled provisional. It is a labelling
# threshold, never a gate, and nothing is withheld because of it.
PROVISIONAL_BELOW = 30


def contract() -> dict[str, Any]:
    with CONTRACT.open("rb") as fh:
        data: dict[str, Any] = tomllib.load(fh)
    return data


def gh_api(path: str) -> Any:
    result = subprocess.run(  # noqa: S603
        ["gh", "api", "--paginate", path],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
        cwd=ROOT,
    )
    if result.returncode != 0:
        raise RuntimeError(f"gh api {path} failed: {result.stderr.strip()}")
    return json.loads(result.stdout)


def seconds(started: str, ended: str) -> float:
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    return (
        dt.datetime.strptime(ended, fmt) - dt.datetime.strptime(started, fmt)
    ).total_seconds()


def percentile(values: list[float], p: float) -> float:
    """Nearest-rank percentile. No interpolation, so every reported number is a
    duration that actually happened."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, min(len(ordered), int(-(-p * len(ordered) // 100))))
    return ordered[rank - 1]


def collect(repo: str, workflow: str) -> list[dict[str, Any]]:
    payload = gh_api(
        f"repos/{repo}/actions/workflows/{workflow}/runs?status=success&per_page=100"
    )
    runs: list[dict[str, Any]] = payload.get("workflow_runs", [])
    out: list[dict[str, Any]] = []
    for run in runs:
        started, ended = run.get("run_started_at"), run.get("updated_at")
        if not started or not ended:
            continue
        out.append(
            {
                "id": run["id"],
                "sha": str(run.get("head_sha", ""))[:8],
                "event": run.get("event"),
                "seconds": seconds(started, ended),
            }
        )
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", default=None)
    ap.add_argument("--workflow", default=None, help="workflow file name")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    meta = contract()["meta"]
    repo = args.repo or str(meta["repository"])
    workflow = args.workflow or f"{meta['required_pr_check']}.yml"

    try:
        runs = collect(repo, workflow)
    except RuntimeError as exc:
        print(f"could not read runs: {exc}", file=sys.stderr)
        return 1

    durations = [r["seconds"] for r in runs]
    n = len(durations)

    report: dict[str, Any] = {
        "repository": repo,
        "workflow": workflow,
        "sample_count": n,
        "confidence": "provisional" if n < PROVISIONAL_BELOW else "measured",
        "p50_seconds": round(percentile(durations, 50)) if n else None,
        "p95_seconds": round(percentile(durations, 95)) if n else None,
        "min_seconds": round(min(durations)) if n else None,
        "max_seconds": round(max(durations)) if n else None,
        # Cold and warm cannot be told apart from the runs API alone: it does not
        # report cache hits. Stated as unavailable rather than guessed.
        "cold_warm": "unavailable - the runs API does not report cache hits",
    }

    if args.json:
        print(json.dumps(report, indent=2))
        return 0

    print(f"{workflow} on {repo}\n")
    if n == 0:
        print("  no successful runs retained yet")
        print("  p50: unmeasured\n  p95: unmeasured")
        return 0

    print(f"  sample count   {n}")
    print(f"  p50            {report['p50_seconds']}s")
    print(f"  p95            {report['p95_seconds']}s")
    print(f"  minimum        {report['min_seconds']}s")
    print(f"  maximum        {report['max_seconds']}s")
    print(f"  confidence     {report['confidence']}")
    print(f"  cold/warm      {report['cold_warm']}")

    if report["confidence"] == "provisional":
        print(
            f"\n  Provisional: {n} run(s) is a small sample. The arithmetic is "
            f"real and the sample size is stated, so nothing here is invented "
            f"and nothing is withheld. It sharpens on its own as runs accumulate."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
