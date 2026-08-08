"""Turn a failed nightly run into one issue somebody will actually see.

A red nightly that nobody is notified about is not a quality control. It is a
colour on a page nobody visits. That was the single most likely failure of this
whole design, so it gets a mechanism rather than a habit.

  failure, no open issue    -> open one, with the run URL, the commit, the
                               failed jobs and links to the artifacts
  failure, issue already open-> add a comment and refresh the body. NEVER a
                               second issue for the same unresolved failure
  success, issue open        -> close it, naming the run that fixed it
  success, nothing open      -> say so and do nothing

Deduplication is by label, not by title text, so re-wording the title later
cannot orphan the old issue and start a duplicate chain.

    python ci/report_nightly.py --status failure --run-id 123 --jobs a,b
    python ci/report_nightly.py --status success --run-id 124
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CONTRACT = ROOT / "ci" / "gates.toml"

LABEL = "nightly-failure"
TITLE = "Nightly verification is failing"


def contract() -> dict[str, Any]:
    with CONTRACT.open("rb") as fh:
        data: dict[str, Any] = tomllib.load(fh)
    return data


def gh(*args: str, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        ["gh", *args],  # noqa: S607
        capture_output=True,
        text=True,
        input=stdin,
        check=False,
        cwd=ROOT,
    )


def ensure_label(repo: str) -> None:
    """Create the dedup label if it is missing. Harmless when it already exists."""
    gh(
        "label",
        "create",
        LABEL,
        "--repo",
        repo,
        "--color",
        "B60205",
        "--description",
        "The scheduled full verification failed. Closed automatically when it passes.",
    )


def open_issues(repo: str) -> list[dict[str, Any]]:
    """Every open failure issue, lowest number first.

    Uses the REST endpoint rather than `gh issue list --label`, which is backed
    by GitHub's search index and lags by seconds.

    Even REST lags on a just-created issue, measured 2026-08-08: three failures
    fired back to back produced two issues before the third found the first. So
    uniqueness is not attempted by looking-before-creating alone - see
    reconcile(), which repairs a duplicate rather than pretending the race
    cannot happen. Preventing a race you do not control is a promise; repairing
    it is a mechanism.
    """
    result = gh("api", f"repos/{repo}/issues?labels={LABEL}&state=open&per_page=100")
    if result.returncode != 0:
        # NOT an empty list. "I could not check" is not "there are none".
        # Returning [] here would turn a failed read into a new duplicate
        # issue - failing to know becoming failing open, which is the exact
        # class of bug these gates exist to prevent.
        raise LookupError(
            f"could not list issues: {result.stderr.strip() or result.stdout.strip()}"
        )
    issues: list[dict[str, Any]] = json.loads(result.stdout or "[]")
    # /issues returns pull requests too. A pull request is not a failure report.
    real = [i for i in issues if "pull_request" not in i]
    return sorted(real, key=lambda i: int(i["number"]))


def reconcile(repo: str) -> int:
    """Leave exactly one open failure issue: the oldest. Close any others.

    Converges no matter how many duplicates a race produced, so the invariant
    "one open issue per unresolved failure" holds on the next run even if it
    was briefly broken.
    """
    try:
        issues = open_issues(repo)
    except LookupError:
        return 0  # cannot reconcile blind; the next run will
    for extra in issues[1:]:
        number = str(extra["number"])
        gh(
            "issue",
            "comment",
            number,
            "--repo",
            repo,
            "--body",
            f"Duplicate of #{issues[0]['number']}. Closing to keep one "
            "open issue per unresolved failure.",
        )
        gh("issue", "close", number, "--repo", repo, "--reason", "not planned")
    return len(issues) - 1 if len(issues) > 1 else 0


def body(repo: str, run_id: str, sha: str, jobs: list[str]) -> str:
    run_url = f"https://github.com/{repo}/actions/runs/{run_id}"
    failed = "\n".join(f"- `{j}`" for j in jobs) if jobs else "- (not reported)"
    return f"""The scheduled full verification failed.

**Run:** {run_url}
**Commit:** `{sha}`

### Jobs that failed

{failed}

### Evidence

Artifacts are attached to the run above: coverage, mutation inventory,
security reports and the reproducibility record.

{run_url}#artifacts

---

This issue is opened once and updated on every further failure. It closes
itself when a nightly run passes. It is not a duplicate of any other
`{LABEL}` issue - there is only ever one open at a time.
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--status", required=True, choices=("success", "failure"))
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--sha", default="unknown")
    ap.add_argument("--jobs", default="", help="comma-separated failed job names")
    ap.add_argument("--repo", default=None)
    args = ap.parse_args()

    repo = args.repo or str(contract()["meta"]["repository"])
    run_url = f"https://github.com/{repo}/actions/runs/{args.run_id}"
    jobs = [j.strip() for j in args.jobs.split(",") if j.strip()]

    ensure_label(repo)

    if args.status == "success":
        # Close EVERY open one, not just the first. If a race ever left two, a
        # green nightly must not leave one of them open and stale.
        try:
            issues = open_issues(repo)
        except LookupError as exc:
            print(f"FAIL - {exc}", file=sys.stderr)
            return 1
        if not issues:
            print("nightly passed, no open failure issue - nothing to do")
            return 0
        for issue in issues:
            number = str(issue["number"])
            gh(
                "issue",
                "comment",
                number,
                "--repo",
                repo,
                "--body",
                f"Nightly verification passed again.\n\nFixed by: {run_url}",
            )
            closed = gh(
                "issue", "close", number, "--repo", repo, "--reason", "completed"
            )
            if closed.returncode != 0:
                print(f"could not close #{number}: {closed.stderr}", file=sys.stderr)
                return 1
            print(f"nightly passed - closed issue #{number}")
        return 0

    try:
        existing = open_issues(repo)
    except LookupError as exc:
        # Refuse to create. A duplicate issue born from a failed read is noise
        # that trains people to ignore the label.
        print(f"FAIL - {exc}", file=sys.stderr)
        print(
            "refusing to open an issue without knowing whether one exists",
            file=sys.stderr,
        )
        return 1

    text = body(repo, args.run_id, args.sha, jobs)

    if not existing:
        created = gh(
            "issue",
            "create",
            "--repo",
            repo,
            "--title",
            TITLE,
            "--label",
            LABEL,
            "--body",
            text,
        )
        if created.returncode != 0:
            print(f"could not create the issue: {created.stderr}", file=sys.stderr)
            return 1
        print(f"opened: {created.stdout.strip()}")
        # A concurrent run may have opened one too. Repair rather than hope.
        removed = reconcile(repo)
        if removed:
            print(f"reconciled: closed {removed} duplicate(s) from a race")
        return 0

    number = str(existing[0]["number"])
    gh("issue", "edit", number, "--repo", repo, "--body", text)
    commented = gh(
        "issue",
        "comment",
        number,
        "--repo",
        repo,
        "--body",
        f"Still failing.\n\n**Run:** {run_url}\n**Commit:** `{args.sha}`",
    )
    if commented.returncode != 0:
        print(f"could not comment on #{number}: {commented.stderr}", file=sys.stderr)
        return 1
    removed = reconcile(repo)
    if removed:
        print(f"reconciled: closed {removed} duplicate(s) from a race")
    print(f"already open - updated issue #{number}, no duplicate created")
    return 0


if __name__ == "__main__":
    sys.exit(main())
