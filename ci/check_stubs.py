"""Refuse to let a placeholder workflow reach GitHub.

The previous repository, Intellora-ai/accountant-dad, had 24 CI jobs. Thirteen of
them were `echo "NOT IMPLEMENTED"; exit 1` placeholders. Every run was red, and
nothing could ever merge. This script is the specific mechanism that makes that
impossible here: it runs before the commit, not after the push.

Blocked:
    NOT IMPLEMENTED
    TODO
    placeholder
    exit 1 used as a stub
    echo-only fake validation
    empty job
    missing command
    missing timeout
    unpinned third-party action
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

STUB_WORDS = ("NOT IMPLEMENTED", "TODO", "FIXME", "placeholder", "XXX")
SHA_PIN = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True)
class Problem:
    path: str
    line: int
    detail: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}  {self.detail}"


def check(path: Path) -> list[Problem]:
    text = path.read_text()
    lines = text.splitlines()
    out: list[Problem] = []
    rel = str(path)

    for i, line in enumerate(lines, 1):
        stripped = line.strip()

        for word in STUB_WORDS:
            if word.lower() in stripped.lower():
                out.append(Problem(rel, i, f"placeholder marker {word!r}"))

        # `exit 1` as the only thing a step does, rather than as the tail of a
        # real command that can also succeed.
        if re.fullmatch(r"exit\s+1", stripped):
            prev = lines[i - 2].strip() if i >= 2 else ""
            if prev.startswith("echo") or prev == "" or prev.startswith("run:"):
                out.append(
                    Problem(rel, i, "bare `exit 1` stub: a step that can only fail")
                )

        # Third-party actions must be pinned to a full commit SHA.
        m = re.search(r"uses:\s*([^\s#]+)", stripped)
        if m:
            ref = m.group(1)
            if ref.startswith("./") or ref.startswith("docker://"):
                continue
            if "@" not in ref:
                out.append(Problem(rel, i, f"action {ref!r} has no ref at all"))
            else:
                _, _, pin = ref.partition("@")
                if not SHA_PIN.fullmatch(pin):
                    out.append(
                        Problem(
                            rel,
                            i,
                            f"action {ref!r} is pinned to {pin!r}, not a 40-character "
                            "commit SHA. A tag can be moved under you.",
                        )
                    )

    out.extend(_check_jobs(rel, lines))
    return out


def _check_jobs(rel: str, lines: list[str]) -> list[Problem]:
    """Every job needs a timeout and at least one real command."""
    out: list[Problem] = []
    in_jobs = False
    job_name: str | None = None
    job_start = 0
    body: list[str] = []

    def finish() -> None:
        if job_name is None:
            return
        joined = "\n".join(body)
        if "timeout-minutes:" not in joined:
            out.append(Problem(rel, job_start, f"job {job_name!r} has no timeout"))
        if "steps:" not in joined:
            out.append(Problem(rel, job_start, f"job {job_name!r} has no steps"))
        runs = re.findall(r"run:\s*(.*)", joined)
        real = [
            r
            for r in runs
            if r.strip() and not r.strip().startswith("echo") and r.strip() != "|"
        ]
        multiline = "run: |" in joined or "run: >" in joined
        if not real and not multiline:
            uses = re.findall(r"uses:", joined)
            if not uses:
                out.append(
                    Problem(rel, job_start, f"job {job_name!r} runs no real command")
                )
        if (
            runs
            and all(r.strip().startswith("echo") for r in runs if r.strip())
            and not multiline
        ):
            out.append(
                Problem(
                    rel, job_start, f"job {job_name!r} only echoes: fake validation"
                )
            )

    for i, line in enumerate(lines, 1):
        if re.match(r"^jobs:\s*$", line):
            in_jobs = True
            continue
        if not in_jobs:
            continue
        if line and not line.startswith(" ") and not line.startswith("#"):
            finish()
            job_name, body = None, []
            in_jobs = False
            continue
        m = re.match(r"^  ([A-Za-z0-9_-]+):\s*$", line)
        if m:
            finish()
            job_name, job_start, body = m.group(1), i, []
            continue
        if job_name is not None:
            body.append(line)
    finish()
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--dir", type=Path, default=Path(".github/workflows"), help="workflow directory"
    )
    args = ap.parse_args()

    if not args.dir.is_dir():
        print(f"no workflow directory at {args.dir} - nothing to check")
        return 0

    files = sorted(args.dir.glob("*.yml")) + sorted(args.dir.glob("*.yaml"))
    if not files:
        print(f"no workflows in {args.dir} - nothing to check")
        return 0

    problems: list[Problem] = []
    for f in files:
        problems.extend(check(f))

    if problems:
        print(f"FAIL - {len(problems)} placeholder or unsafe construct(s):\n")
        for p in problems:
            print(f"  {p}")
        print(
            "\nA workflow that cannot succeed is worse than no workflow: it reads "
            "as coverage while blocking every merge."
        )
        return 1

    print(f"PASS - {len(files)} workflow(s), no placeholders, every action SHA-pinned")
    return 0


if __name__ == "__main__":
    sys.exit(main())
