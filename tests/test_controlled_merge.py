"""`scripts/merge-pr-with-codeant` is the only place a pull request may merge.

WHY THIS FILE EXISTS
--------------------
The chokepoint is a process control over a single merging actor. There is no
required GitHub check behind it, so nothing catches it when the inspection
quietly stops inspecting. These tests are that catch.

Every scenario below drives the real script as a subprocess. Nothing is stubbed
inside it. The only substitution is the `gh` binary itself, chosen through
`CODEANT_MERGE_GH`, so argument building, ordering, classification, evidence
writing, the head re-read and the merge call are all the lines that run in
production.

WHAT THIS FILE DOES NOT PROVE
-----------------------------
1. That the `--jq` filters passed to `gh api` are correct. The fake `gh` returns
   already-shaped objects. The shapes were read off the live API on 2026-08-10
   against pull requests 29 and 30 of `Intellora-ai/accountant-dad-core`, and
   `test_the_api_endpoints_asked_for_are_the_measured_ones` pins the endpoints,
   but a wrong jq expression would still pass here.

2. That the merge is impossible to bypass. It is not. A different actor calling
   the raw merge command, or clicking Merge in the web interface, walks straight
   past every line under test. That is recorded, not fixed.

THE FIXTURE VOCABULARY
----------------------
`_gh_fixture` writes a small Python program that impersonates `gh`. It reads a
state file, logs every invocation, and answers four request shapes. `heads` is a
list because the script reads the head twice - once to inspect, once immediately
before merging - and a one-element difference between those two reads is the
whole head-race guard.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

import pytest

import accountant

# Provenance. A measurement taken against a different checkout is not a
# measurement of this checkout.
assert str(Path(accountant.__file__).resolve()).startswith(str(Path.cwd().resolve()))

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "merge-pr-with-codeant"
INSTRUCTIONS = ROOT / "docs" / "CLAUDE_CONTEXT.md"

# Observed on the live API, 2026-08-10. Never invented.
CODEANT_LOGIN = "codeant-ai[bot]"
CODEANT_TYPE = "Bot"
REPO = "Intellora-ai/accountant-dad-core"

HEAD_A = "6686752ba5522f854e85e7b249e5fa31cb992dd6"
HEAD_B = "1111111111111111111111111111111111111111"
OLD_HEAD = "2222222222222222222222222222222222222222"

# The status comment CodeAnt posts, with the machine-readable marker it embeds.
# Copied in shape from the real comment on pull request 30.
STATUS_COMMENT = (
    "## CodeAnt AI - Review Status\n\n"
    "| Status | Commit |\n| --- | --- |\n"
    "| Reviewed your PR | `6686752` |\n\n"
    '<!-- codeant-review-status:[{"label":"Reviewed your PR","commit":"'
    + HEAD_A
    + '","done":true}] -->'
)

# Verbatim from pull request 29, which CodeAnt declined to review.
SKIP_COMMENT = (
    "**Skipping CodeAnt AI review** - this PR changes more than 100 files, "
    "which usually means a migration, codemod, or vendored drop."
)

# Shape copied from the real line comment on `accountant/tallyio/__main__.py`.
FINDING_BODY = (
    "**Suggestion:** The CLI confirms and executes the batch without supplying "
    "an `ActionLogSink`. [api mismatch]\n\n"
    "<details>\n<summary><b>Severity Level:</b> Critical </summary>\n</details>"
)

FAKE_GH = """\
import json, os, sys
from pathlib import Path

state_path = Path(os.environ["FAKE_GH_STATE"])
state = json.loads(state_path.read_text())
log = Path(os.environ["FAKE_GH_LOG"])
argv = sys.argv[1:]

with log.open("a") as handle:
    handle.write(json.dumps(argv) + "\\n")

def emit(rows):
    for row in rows:
        sys.stdout.write(json.dumps(row) + "\\n")

if argv[:3] == ["repo", "view", "--json"]:
    print(json.dumps({"nameWithOwner": state["repository"]}))
elif argv[:2] == ["pr", "view"]:
    calls = state.get("_head_calls", 0)
    heads = state["heads"]
    head = heads[min(calls, len(heads) - 1)]
    state["_head_calls"] = calls + 1
    state_path.write_text(json.dumps(state))
    print(json.dumps({
        "number": state["pr"],
        "headRefOid": head,
        "baseRefName": "main",
        "state": "OPEN",
        "url": "https://github.com/x/y/pull/%d" % state["pr"],
    }))
elif argv[:2] == ["pr", "merge"]:
    # Recording whether the evidence file already existed is how the ordering
    # guarantee is proven, rather than asserted.
    audit = Path(os.environ["CODEANT_MERGE_AUDIT_DIR"])
    existing = sorted(p.name for p in audit.glob("*.json")) if audit.exists() else []
    Path(os.environ["FAKE_GH_MERGED"]).write_text(json.dumps({
        "argv": argv, "evidence_present_at_merge_time": existing,
    }))
elif argv[0] == "api" and argv[1].endswith("/reviews"):
    emit(state["reviews"])
elif argv[0] == "api" and "/pulls/" in argv[1] and argv[1].endswith("/comments"):
    emit(state["line_comments"])
elif argv[0] == "api" and "/issues/" in argv[1] and argv[1].endswith("/comments"):
    emit(state["conversation"])
else:
    sys.stderr.write("fake gh: unhandled %r\\n" % (argv,))
    sys.exit(3)
"""


def _user() -> dict[str, str]:
    return {"login": CODEANT_LOGIN, "type": CODEANT_TYPE}


def review(commit: str, review_id: int = 4894601172) -> dict[str, Any]:
    """A CodeAnt review. `commit_id` is populated - that was measured."""
    return {
        "id": review_id,
        "state": "COMMENTED",
        "commit_id": commit,
        "submitted_at": "2026-08-10T07:41:02Z",
        "body": "",
        "user": _user(),
    }


def line_comment(
    commit: str,
    path: str = "accountant/tallyio/__main__.py",
    line: int | None = 159,
    comment_id: int = 3747526241,
    original_commit: str | None = None,
) -> dict[str, Any]:
    """A CodeAnt line comment.

    `commit_id` and `original_commit_id` are separate arguments because GitHub
    moves the first and never the second. See
    `test_a_line_comment_dragged_onto_the_head_is_not_a_review_of_it`.
    """
    return {
        "id": comment_id,
        "commit_id": commit,
        "original_commit_id": original_commit or commit,
        "path": path,
        "line": line,
        "body": FINDING_BODY,
        "pull_request_review_id": 4894606378,
        "user": _user(),
    }


def conversation(body: str, comment_id: int = 5237246540) -> dict[str, Any]:
    """A conversation comment. It carries NO `commit_id` - that was measured."""
    return {"id": comment_id, "body": body, "user": _user()}


class Run:
    """The result of one controlled-merge run."""

    def __init__(
        self,
        completed: subprocess.CompletedProcess[str],
        audit: Path,
        merged: Path,
        calls: Path,
    ) -> None:
        self.returncode = completed.returncode
        self.stdout = completed.stdout
        self.stderr = completed.stderr
        self.audit = audit
        self._merged = merged
        self._calls = calls

    @property
    def merged(self) -> bool:
        return self._merged.exists()

    @property
    def merge_record(self) -> dict[str, Any]:
        record: Any = json.loads(self._merged.read_text())
        assert isinstance(record, dict)
        return cast(dict[str, Any], record)

    @property
    def calls(self) -> list[list[str]]:
        if not self._calls.exists():
            return []
        return [json.loads(line) for line in self._calls.read_text().splitlines()]

    @property
    def evidence(self) -> dict[str, Any]:
        files = sorted(self.audit.glob("*.json"))
        assert len(files) == 1, f"expected one evidence file, found {files}"
        record: Any = json.loads(files[0].read_text())
        assert isinstance(record, dict)
        return cast(dict[str, Any], record)

    @property
    def evidence_name(self) -> str:
        return sorted(self.audit.glob("*.json"))[0].name


def _gh_fixture(
    tmp_path: Path,
    *,
    pr: int = 30,
    heads: list[str],
    reviews: list[dict[str, Any]] | None = None,
    line_comments: list[dict[str, Any]] | None = None,
    conversation_comments: list[dict[str, Any]] | None = None,
) -> tuple[Path, dict[str, str]]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    gh = tmp_path / "fake-gh"
    gh.write_text(f"#!/usr/bin/env python3\n{FAKE_GH}", encoding="utf-8")
    gh.chmod(0o755)

    state = tmp_path / "state.json"
    state.write_text(
        json.dumps(
            {
                "repository": REPO,
                "pr": pr,
                "heads": heads,
                "reviews": reviews or [],
                "line_comments": line_comments or [],
                "conversation": conversation_comments or [],
            }
        ),
        encoding="utf-8",
    )
    env = {
        "CODEANT_MERGE_GH": str(gh),
        "CODEANT_MERGE_AUDIT_DIR": str(tmp_path / "audit"),
        "FAKE_GH_STATE": str(state),
        "FAKE_GH_LOG": str(tmp_path / "calls.log"),
        "FAKE_GH_MERGED": str(tmp_path / "merged.json"),
    }
    return gh, env


def run_merge(
    tmp_path: Path,
    *,
    pr: int = 30,
    heads: list[str],
    reviews: list[dict[str, Any]] | None = None,
    line_comments: list[dict[str, Any]] | None = None,
    conversation_comments: list[dict[str, Any]] | None = None,
    extra_args: list[str] | None = None,
) -> Run:
    _, env = _gh_fixture(
        tmp_path,
        pr=pr,
        heads=heads,
        reviews=reviews,
        line_comments=line_comments,
        conversation_comments=conversation_comments,
    )
    completed = subprocess.run(  # noqa: S603 - fixed argv, test-owned paths
        [sys.executable, str(SCRIPT), str(pr), *(extra_args or [])],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, **env},
        cwd=tmp_path,
    )
    return Run(
        completed,
        Path(env["CODEANT_MERGE_AUDIT_DIR"]),
        Path(env["FAKE_GH_MERGED"]),
        Path(env["FAKE_GH_LOG"]),
    )


def handled_file(tmp_path: Path, records: list[dict[str, Any]]) -> list[str]:
    path = tmp_path / "handled.json"
    path.write_text(json.dumps(records), encoding="utf-8")
    return ["--handled", str(path)]


# ---------------------------------------------------------------------------
# Outcome A - a review of the exact current head, with no findings
# ---------------------------------------------------------------------------


def test_current_head_review_with_no_findings_merges(tmp_path: Path) -> None:
    result = run_merge(
        tmp_path,
        heads=[HEAD_A, HEAD_A],
        reviews=[review(HEAD_A)],
        conversation_comments=[conversation(STATUS_COMMENT)],
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "CODEANT_REVIEWED" in result.stdout
    assert f"CODEANT_HEAD={HEAD_A}" in result.stdout
    assert "CODEANT_FINDINGS=0" in result.stdout
    assert result.merged
    assert result.evidence["codeant_status"] == "REVIEWED"
    assert result.evidence["codeant_reviewed_exact_head"] is True


def test_the_merge_command_is_squash_and_delete_branch(tmp_path: Path) -> None:
    result = run_merge(
        tmp_path,
        heads=[HEAD_A, HEAD_A],
        reviews=[review(HEAD_A)],
    )
    assert result.merge_record["argv"] == [
        "pr",
        "merge",
        "30",
        "--squash",
        "--delete-branch",
    ]


# ---------------------------------------------------------------------------
# Outcome B - a review of the exact current head, with findings
# ---------------------------------------------------------------------------


def test_current_head_review_with_findings_refuses(tmp_path: Path) -> None:
    result = run_merge(
        tmp_path,
        heads=[HEAD_A, HEAD_A],
        reviews=[review(HEAD_A)],
        line_comments=[line_comment(HEAD_A)],
    )
    assert result.returncode == 1
    assert not result.merged
    assert "CODEANT_FINDINGS=1" in result.stdout
    assert "MERGE_REFUSED" in result.stdout
    assert result.evidence["codeant_status"] == "REVIEWED"
    assert len(result.evidence["codeant_findings"]) == 1


def test_every_finding_is_printed_with_file_line_and_severity(tmp_path: Path) -> None:
    """A finding that is not printed is a finding hidden."""
    result = run_merge(
        tmp_path,
        heads=[HEAD_A, HEAD_A],
        reviews=[review(HEAD_A)],
        line_comments=[
            line_comment(HEAD_A, comment_id=1),
            line_comment(HEAD_A, path="accountant/web/app.py", line=1720, comment_id=2),
        ],
    )
    assert "CODEANT_FINDINGS=2" in result.stdout
    assert "accountant/tallyio/__main__.py:159" in result.stdout
    assert "accountant/web/app.py:1720" in result.stdout
    assert "severity=Critical" in result.stdout
    assert "id=1" in result.stdout
    assert "id=2" in result.stdout


def test_a_fixed_finding_on_the_current_head_still_refuses(tmp_path: Path) -> None:
    """Code changing is not resolution. A fix changes the head."""
    records = [
        {
            "id": "3747526241",
            "file": "accountant/tallyio/__main__.py",
            "line": 159,
            "severity": "Critical",
            "decision": "FIXED",
            "guard": "tests/test_reverse_all_cli.py asserts the sink is passed",
            "test_command": "pytest tests/test_reverse_all_cli.py -q",
            "result": "passed",
        }
    ]
    result = run_merge(
        tmp_path,
        heads=[HEAD_A, HEAD_A],
        reviews=[review(HEAD_A)],
        line_comments=[line_comment(HEAD_A)],
        extra_args=handled_file(tmp_path, records),
    )
    assert result.returncode == 1
    assert not result.merged
    assert "A fix changes the head" in result.stdout


def test_a_false_positive_needs_explicit_confirmation(tmp_path: Path) -> None:
    records = [
        {
            "id": "3747526241",
            "file": "accountant/tallyio/__main__.py",
            "line": 159,
            "severity": "Critical",
            "decision": "FALSE_POSITIVE",
            "guard": "none - the sink is injected by the caller",
            "test_command": "pytest tests/test_reverse_all_cli.py -q",
            "result": "passed",
            "reason": "the CLI receives the sink from its caller, not inline",
        }
    ]
    args = handled_file(tmp_path, records)

    refused = run_merge(
        tmp_path,
        heads=[HEAD_A, HEAD_A],
        reviews=[review(HEAD_A)],
        line_comments=[line_comment(HEAD_A)],
        extra_args=args,
    )
    assert refused.returncode == 1
    assert not refused.merged
    assert "--confirm-exceptions" in refused.stdout

    confirmed = run_merge(
        tmp_path / "confirmed",
        heads=[HEAD_A, HEAD_A],
        reviews=[review(HEAD_A)],
        line_comments=[line_comment(HEAD_A)],
        extra_args=[*args, "--confirm-exceptions"],
    )
    assert confirmed.returncode == 0, confirmed.stdout + confirmed.stderr
    assert confirmed.merged
    assert "reason: the CLI receives the sink" in confirmed.stdout
    assert confirmed.evidence["findings_handled"][0]["decision"] == "FALSE_POSITIVE"


def test_an_exception_without_a_reason_is_rejected(tmp_path: Path) -> None:
    records = [
        {
            "id": "3747526241",
            "file": "accountant/tallyio/__main__.py",
            "line": 159,
            "severity": "Critical",
            "decision": "ACCEPTED_RISK",
            "guard": "none",
            "test_command": "pytest -q",
            "result": "passed",
            "reason": "   ",
        }
    ]
    result = run_merge(
        tmp_path,
        heads=[HEAD_A, HEAD_A],
        reviews=[review(HEAD_A)],
        line_comments=[line_comment(HEAD_A)],
        extra_args=[*handled_file(tmp_path, records), "--confirm-exceptions"],
    )
    assert result.returncode == 1
    assert not result.merged
    assert "must carry a reason" in result.stderr


def test_a_handled_record_missing_a_field_is_rejected(tmp_path: Path) -> None:
    result = run_merge(
        tmp_path,
        heads=[HEAD_A, HEAD_A],
        reviews=[review(HEAD_A)],
        line_comments=[line_comment(HEAD_A)],
        extra_args=handled_file(tmp_path, [{"id": "3747526241"}]),
    )
    assert result.returncode == 1
    assert not result.merged
    assert "missing required fields" in result.stderr
    for field in ("file", "line", "severity", "decision", "guard", "test_command"):
        assert field in result.stderr


# ---------------------------------------------------------------------------
# Outcome C - an older review only
# ---------------------------------------------------------------------------


def test_older_review_only_is_stale_and_refuses(tmp_path: Path) -> None:
    result = run_merge(
        tmp_path,
        heads=[HEAD_A, HEAD_A],
        reviews=[review(OLD_HEAD)],
    )
    assert result.returncode == 1
    assert not result.merged
    assert "CODEANT_REVIEW_STALE" in result.stdout
    assert f"CODEANT_REVIEWED_SHA={OLD_HEAD}" in result.stdout
    assert f"CURRENT_HEAD={HEAD_A}" in result.stdout
    assert result.evidence["codeant_status"] == "STALE"
    assert result.evidence["codeant_reviewed_exact_head"] is False


def test_the_most_recent_review_is_never_a_fallback(tmp_path: Path) -> None:
    """THE mutant that matters.

    "No review matched the head, so use the newest one" is the natural
    shortcut, and it silently converts a stale review into an approval. Three
    reviews here, all of other SHAs, the last one newest. The answer is still
    STALE.
    """
    newest = review(OLD_HEAD, review_id=3)
    newest["submitted_at"] = "2999-01-01T00:00:00Z"
    result = run_merge(
        tmp_path,
        heads=[HEAD_A, HEAD_A],
        reviews=[
            review("3" * 40, review_id=1),
            review("4" * 40, review_id=2),
            newest,
        ],
    )
    assert result.returncode == 1
    assert not result.merged
    assert "CODEANT_REVIEW_STALE" in result.stdout
    assert result.evidence["codeant_status"] == "STALE"
    assert result.evidence["codeant_reviewed_exact_head"] is False
    assert result.evidence["codeant_review_count"] == 3


def test_a_stale_review_is_not_rescued_by_the_status_marker(tmp_path: Path) -> None:
    """The conversation marker corroborates. It never authorises."""
    result = run_merge(
        tmp_path,
        heads=[HEAD_B, HEAD_B],
        reviews=[review(HEAD_A)],
        conversation_comments=[conversation(STATUS_COMMENT)],
    )
    assert result.returncode == 1
    assert not result.merged
    assert result.evidence["codeant_status"] == "STALE"
    assert result.evidence["codeant_status_marker_commit"] == HEAD_A
    assert result.evidence["codeant_reviewed_exact_head"] is False


def test_a_shortened_sha_never_matches_a_head(tmp_path: Path) -> None:
    result = run_merge(
        tmp_path,
        heads=[HEAD_A, HEAD_A],
        reviews=[review(HEAD_A[:7])],
    )
    assert result.returncode == 1
    assert result.evidence["codeant_status"] == "STALE"


# ---------------------------------------------------------------------------
# Outcome D - skipped, or absent
# ---------------------------------------------------------------------------


def test_codeant_skipped_records_the_reason_and_proceeds(tmp_path: Path) -> None:
    """Pull request 29 is the real case: 208 files, over CodeAnt's file limit."""
    result = run_merge(
        tmp_path,
        pr=29,
        heads=[HEAD_A, HEAD_A],
        conversation_comments=[conversation(SKIP_COMMENT)],
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.merged
    assert "CODEANT_SKIPPED" in result.stdout
    assert "CODEANT_STATUS=SKIPPED" in result.stdout
    assert "CODEANT_REASON=changed-file-limit" in result.stdout
    assert "CODEANT_REVIEWED=NO" in result.stdout


def test_skipped_is_never_recorded_as_reviewed(tmp_path: Path) -> None:
    result = run_merge(
        tmp_path,
        pr=29,
        heads=[HEAD_A, HEAD_A],
        conversation_comments=[conversation(SKIP_COMMENT)],
    )
    evidence = result.evidence
    assert evidence["codeant_status"] == "SKIPPED"
    assert evidence["codeant_reviewed_exact_head"] is False
    assert evidence["exception_reason"] == "changed-file-limit"
    assert evidence["codeant_review_count"] == 0
    assert "CodeAnt did not review this exact head." in " ".join(evidence["notes"])
    assert "controlled-merger exception" in " ".join(evidence["notes"])


def test_codeant_absent_records_the_reason_and_proceeds(tmp_path: Path) -> None:
    result = run_merge(tmp_path, heads=[HEAD_A, HEAD_A])
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.merged
    assert "CODEANT_ABSENT" in result.stdout
    assert "CODEANT_REVIEWED=NO" in result.stdout
    evidence = result.evidence
    assert evidence["codeant_status"] == "ABSENT"
    assert evidence["codeant_reviewed_exact_head"] is False
    assert evidence["exception_reason"] == "no-codeant-activity-on-this-pull-request"
    assert "CodeAnt did not review this exact head." in " ".join(evidence["notes"])


def test_an_unrecognised_skip_reason_is_named_not_guessed(tmp_path: Path) -> None:
    result = run_merge(
        tmp_path,
        heads=[HEAD_A, HEAD_A],
        conversation_comments=[
            conversation("**Skipping CodeAnt AI review** - the moon is wrong.")
        ],
    )
    assert "CODEANT_REASON=unrecognised-skip-reason" in result.stdout
    assert result.evidence["codeant_status"] == "SKIPPED"


# ---------------------------------------------------------------------------
# Comment kinds
# ---------------------------------------------------------------------------


def test_a_line_comment_dragged_onto_the_head_is_not_a_review_of_it(
    tmp_path: Path,
) -> None:
    """The trap. Measured on pull request 30 on 2026-08-10.

    GitHub re-anchors a line comment's `commit_id` forward onto the newest
    commit the comment still applies to. `original_commit_id` keeps the SHA it
    was written against. So a comment can point at a head that CodeAnt never
    reviewed - and here the only review still names the old SHA.

    Reading "a comment points at the head" as "the head was reviewed" merges
    code no reviewer saw. The answer must be STALE.
    """
    result = run_merge(
        tmp_path,
        heads=[HEAD_B, HEAD_B],
        reviews=[review(HEAD_A)],
        line_comments=[
            line_comment(HEAD_B, line=1723, original_commit=HEAD_A),
        ],
    )
    assert result.returncode == 1
    assert not result.merged
    assert "CODEANT_REVIEW_STALE" in result.stdout
    assert result.evidence["codeant_status"] == "STALE"
    assert result.evidence["codeant_reviewed_exact_head"] is False
    finding = result.evidence["codeant_findings"][0]
    assert finding["commit_id"] == HEAD_B
    assert finding["original_commit_id"] == HEAD_A


def test_an_outdated_line_comment_with_a_null_line_is_not_a_live_finding(
    tmp_path: Path,
) -> None:
    """`line` goes null once the code has moved past the finding."""
    result = run_merge(
        tmp_path,
        heads=[HEAD_A, HEAD_A],
        reviews=[review(HEAD_A)],
        line_comments=[line_comment(HEAD_A, line=None)],
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "CODEANT_FINDINGS=0" in result.stdout
    assert result.evidence["codeant_line_comment_count"] == 1


def test_a_line_comment_on_the_current_head_is_a_finding(tmp_path: Path) -> None:
    """A line comment carries `commit_id`. That was measured, not assumed."""
    result = run_merge(
        tmp_path,
        heads=[HEAD_A, HEAD_A],
        reviews=[review(HEAD_A)],
        line_comments=[line_comment(HEAD_A)],
    )
    assert result.returncode == 1
    assert not result.merged
    assert result.evidence["codeant_status"] == "REVIEWED"
    finding = result.evidence["codeant_findings"][0]
    assert finding["source"] == "line-comment"
    assert finding["file"] == "accountant/tallyio/__main__.py"
    assert finding["line"] == 159
    assert finding["severity"] == "Critical"
    assert finding["head_attributable"] is True


def test_a_line_comment_on_an_older_head_is_not_current_evidence(
    tmp_path: Path,
) -> None:
    result = run_merge(
        tmp_path,
        heads=[HEAD_A, HEAD_A],
        reviews=[review(HEAD_A)],
        line_comments=[line_comment(OLD_HEAD)],
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "CODEANT_FINDINGS=0" in result.stdout
    assert result.evidence["codeant_line_comment_count"] == 1
    assert result.evidence["codeant_findings"] == []


def test_an_unrecognised_conversation_comment_blocks(tmp_path: Path) -> None:
    """Fail closed. A CodeAnt comment nobody taught this script is a finding."""
    result = run_merge(
        tmp_path,
        heads=[HEAD_A, HEAD_A],
        reviews=[review(HEAD_A)],
        conversation_comments=[
            conversation(STATUS_COMMENT),
            conversation("Two secrets are committed in this branch.", comment_id=99),
        ],
    )
    assert result.returncode == 1
    assert not result.merged
    finding = result.evidence["codeant_findings"][0]
    assert finding["source"] == "conversation"
    assert finding["head_attributable"] is False
    assert "Two secrets" in finding["summary"]


def test_the_status_and_skip_comments_are_not_findings(tmp_path: Path) -> None:
    result = run_merge(
        tmp_path,
        heads=[HEAD_A, HEAD_A],
        reviews=[review(HEAD_A)],
        conversation_comments=[conversation(STATUS_COMMENT)],
    )
    assert "CODEANT_FINDINGS=0" in result.stdout
    assert result.returncode == 0, result.stdout + result.stderr


def test_comments_from_anyone_but_codeant_are_ignored(tmp_path: Path) -> None:
    stranger = conversation("LGTM, merging", comment_id=7)
    stranger["user"] = {"login": "someone-else", "type": "User"}
    other_review = review(HEAD_A, review_id=8)
    other_review["user"] = {"login": "someone-else", "type": "User"}
    result = run_merge(
        tmp_path,
        heads=[HEAD_A, HEAD_A],
        reviews=[other_review],
        conversation_comments=[stranger],
    )
    assert result.evidence["codeant_review_count"] == 0
    assert result.evidence["codeant_status"] == "ABSENT"


# ---------------------------------------------------------------------------
# The head race
# ---------------------------------------------------------------------------


def test_the_head_changing_before_the_merge_aborts(tmp_path: Path) -> None:
    """Never merge a SHA you did not inspect."""
    result = run_merge(
        tmp_path,
        heads=[HEAD_A, HEAD_B],
        reviews=[review(HEAD_A)],
    )
    assert result.returncode == 1
    assert not result.merged
    assert "HEAD_CHANGED_DURING_REVIEW" in result.stdout
    assert f"INSPECTED={HEAD_A}" in result.stdout
    assert f"NOW={HEAD_B}" in result.stdout


def test_the_head_is_read_again_immediately_before_merging(tmp_path: Path) -> None:
    result = run_merge(
        tmp_path,
        heads=[HEAD_A, HEAD_A],
        reviews=[review(HEAD_A)],
    )
    head_reads = [c for c in result.calls if c[:2] == ["pr", "view"]]
    assert len(head_reads) == 2, result.calls
    assert result.calls[-1][:2] == ["pr", "merge"]
    assert result.calls[-2][:2] == ["pr", "view"]


def test_the_evidence_names_the_inspected_head_not_the_new_one(
    tmp_path: Path,
) -> None:
    result = run_merge(
        tmp_path,
        heads=[HEAD_A, HEAD_B],
        reviews=[review(HEAD_A)],
    )
    assert result.evidence_name == f"pr-30-{HEAD_A}.json"
    assert result.evidence["head_sha"] == HEAD_A


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------


def test_evidence_is_written_before_the_merge(tmp_path: Path) -> None:
    """Proven by the merge itself observing the file, not by reading the code."""
    result = run_merge(
        tmp_path,
        heads=[HEAD_A, HEAD_A],
        reviews=[review(HEAD_A)],
    )
    assert result.merged
    present = result.merge_record["evidence_present_at_merge_time"]
    assert present == [f"pr-30-{HEAD_A}.json"], present


def test_evidence_is_written_even_when_the_merge_is_refused(tmp_path: Path) -> None:
    result = run_merge(
        tmp_path,
        heads=[HEAD_A, HEAD_A],
        reviews=[review(OLD_HEAD)],
    )
    assert result.returncode == 1
    assert result.evidence["codeant_status"] == "STALE"
    assert "stale" in str(result.evidence["refused"]).lower()


def test_evidence_carries_every_field_the_design_requires(tmp_path: Path) -> None:
    result = run_merge(
        tmp_path,
        heads=[HEAD_A, HEAD_A],
        reviews=[review(HEAD_A)],
        conversation_comments=[conversation(STATUS_COMMENT)],
    )
    evidence = result.evidence
    for field in (
        "repository",
        "pr",
        "head_sha",
        "checked_at_utc",
        "merger",
        "codeant_status",
        "codeant_reviewed_exact_head",
        "codeant_review_count",
        "codeant_line_comment_count",
        "codeant_findings",
        "findings_handled",
        "exception_reason",
        "direct_github_merge_protection",
    ):
        assert field in evidence, field
    assert evidence["repository"] == REPO
    assert evidence["pr"] == 30
    assert evidence["merger"] == "controlled-merge-script"
    assert re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", evidence["checked_at_utc"]
    )
    assert evidence["codeant_login"] == CODEANT_LOGIN


@pytest.mark.parametrize(
    ("heads", "reviews", "conversation_comments"),
    [
        ([HEAD_A, HEAD_A], [review(HEAD_A)], []),
        ([HEAD_A, HEAD_A], [review(OLD_HEAD)], []),
        ([HEAD_A, HEAD_A], [], [conversation(SKIP_COMMENT)]),
        ([HEAD_A, HEAD_A], [], []),
    ],
    ids=["reviewed", "stale", "skipped", "absent"],
)
def test_direct_github_merge_protection_is_always_false(
    tmp_path: Path,
    heads: list[str],
    reviews: list[dict[str, Any]],
    conversation_comments: list[dict[str, Any]],
) -> None:
    """It is false because it is true of reality. GitHub enforces nothing here."""
    result = run_merge(
        tmp_path,
        heads=heads,
        reviews=reviews,
        conversation_comments=conversation_comments,
    )
    assert result.evidence["direct_github_merge_protection"] is False


@pytest.mark.parametrize(
    ("reviews", "conversation_comments", "expected"),
    [
        ([review(OLD_HEAD)], [], "STALE"),
        ([], [conversation(SKIP_COMMENT)], "SKIPPED"),
        ([], [], "ABSENT"),
    ],
    ids=["stale", "skipped", "absent"],
)
def test_reviewed_exact_head_is_only_ever_true_for_a_matching_review(
    tmp_path: Path,
    reviews: list[dict[str, Any]],
    conversation_comments: list[dict[str, Any]],
    expected: str,
) -> None:
    result = run_merge(
        tmp_path,
        heads=[HEAD_A, HEAD_A],
        reviews=reviews,
        conversation_comments=conversation_comments,
    )
    assert result.evidence["codeant_status"] == expected
    assert result.evidence["codeant_reviewed_exact_head"] is False


def test_the_control_model_tokens_are_printed_verbatim(tmp_path: Path) -> None:
    result = run_merge(tmp_path, heads=[HEAD_A, HEAD_A], reviews=[review(HEAD_A)])
    assert "MERGE_CONTROL_MODEL=single-controlled-merger" in result.stdout
    assert "GITHUB_UI_MERGE_BLOCKING=NOT_ENABLED" in result.stdout
    assert "DIRECT_GH_PR_MERGE_BYPASS=OUTSIDE_CONTROL" in result.stdout


def test_a_dry_run_inspects_and_records_but_does_not_merge(tmp_path: Path) -> None:
    result = run_merge(
        tmp_path,
        heads=[HEAD_A, HEAD_A],
        reviews=[review(HEAD_A)],
        extra_args=["--dry-run"],
    )
    assert result.returncode == 0
    assert not result.merged
    assert "MERGE_NOT_ATTEMPTED_DRY_RUN" in result.stdout
    assert result.evidence["dry_run"] is True


# ---------------------------------------------------------------------------
# The endpoints and the identity - measured, never invented
# ---------------------------------------------------------------------------


def test_the_api_endpoints_asked_for_are_the_measured_ones(tmp_path: Path) -> None:
    result = run_merge(tmp_path, heads=[HEAD_A, HEAD_A], reviews=[review(HEAD_A)])
    endpoints = [c[1] for c in result.calls if c[0] == "api"]
    assert f"repos/{REPO}/pulls/30/reviews" in endpoints
    assert f"repos/{REPO}/pulls/30/comments" in endpoints
    assert f"repos/{REPO}/issues/30/comments" in endpoints
    for call in result.calls:
        if call[0] == "api":
            assert "--paginate" in call


def test_the_observed_codeant_login_is_the_only_one_matched() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert f'CODEANT_LOGIN = "{CODEANT_LOGIN}"' in source
    assert source.count(f'"{CODEANT_LOGIN}"') == 1, (
        "the observed login belongs in exactly one named constant"
    )


# ---------------------------------------------------------------------------
# The chokepoint is the only door
# ---------------------------------------------------------------------------


def _tracked_files() -> list[str]:
    listing = subprocess.run(
        ["git", "ls-files"],  # noqa: S607 - `git` from PATH, no input at all
        capture_output=True,
        text=True,
        check=True,
        cwd=ROOT,
    )
    return [line for line in listing.stdout.splitlines() if line]


RAW_MERGE = "gh pr merge"


#: The one line in this file allowed to carry the raw command: the guard below
#: has to name the thing it is searching for.
RAW_MERGE_DEFINITION = f'RAW_MERGE = "{RAW_MERGE}"'


def test_the_raw_merge_is_absent_from_every_executable_file() -> None:
    """The raw command may live in prose. It may not live anywhere runnable.

    Markdown is prose. `docs/PROJECT_STATE.md` records, in a fenced block, a
    merge that was refused back in the project's history, and the repository
    instructions name the command in order to forbid it. Neither is executable.

    Everything else is scanned, `scripts/merge-pr-with-codeant` included. The
    script builds its merge as an argument list, never as a shell string, so
    even there the literal must not appear - a shell string in the one place
    allowed to merge is the place a shell string does the most damage.

    This file is scanned too. Its single allowed line is the constant the
    search itself needs.
    """
    offenders: list[str] = []
    for name in _tracked_files():
        if name.endswith(".md"):
            continue
        path = ROOT / name
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for number, line in enumerate(text.splitlines(), 1):
            if RAW_MERGE not in line:
                continue
            if name == "tests/test_controlled_merge.py" and (
                RAW_MERGE_DEFINITION in line
            ):
                continue
            offenders.append(f"{name}:{number}: {line.strip()}")
    assert offenders == [], (
        "the raw merge command must not appear in an executable file; "
        "use scripts/merge-pr-with-codeant\n" + "\n".join(offenders)
    )


def test_only_two_documents_talk_about_the_raw_merge() -> None:
    """The instructions that forbid it, and the history that records it.

    A third document mentioning the raw command is a third place someone can
    read it as an instruction. Line numbers are deliberately not pinned - this
    has to survive edits elsewhere in those files.
    """
    documents = {
        name
        for name in _tracked_files()
        if name.endswith(".md")
        and (ROOT / name).is_file()
        and RAW_MERGE in (ROOT / name).read_text(encoding="utf-8")
    }
    assert documents == {
        "docs/CLAUDE_CONTEXT.md",
        "docs/PROJECT_STATE.md",
    }, documents


def test_the_merge_invocation_is_built_in_exactly_one_place() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert source.count('MERGE_SUBCOMMAND: tuple[str, str] = ("pr", "merge")') == 1
    assert source.count("MERGE_SUBCOMMAND") == 2, (
        "one definition and one use - a second use is a second merge path"
    )
    assert source.count("MERGE_FLAGS") == 2


def test_the_repository_instructions_forbid_the_raw_merge() -> None:
    text = INSTRUCTIONS.read_text(encoding="utf-8")
    assert "scripts/merge-pr-with-codeant" in text
    assert RAW_MERGE in text, "the instructions must name the command they forbid"
    assert "MERGE_CONTROL_MODEL=single-controlled-merger" in text
    assert "GITHUB_UI_MERGE_BLOCKING=NOT_ENABLED" in text
    assert "DIRECT_GH_PR_MERGE_BYPASS=OUTSIDE_CONTROL" in text


def test_the_script_is_executable() -> None:
    assert SCRIPT.exists()
    assert os.access(SCRIPT, os.X_OK), "chmod +x scripts/merge-pr-with-codeant"
    assert SCRIPT.read_text(encoding="utf-8").startswith("#!/usr/bin/env python3")


def test_the_script_passes_lint_and_format() -> None:
    """Ruff's directory scan skips extensionless files, so it is named here."""
    ruff = ROOT / ".venv" / "bin" / "ruff"
    if not ruff.exists():
        pytest.skip("no ruff in the project virtualenv")
    for args in (["check", str(SCRIPT)], ["format", "--check", str(SCRIPT)]):
        done = subprocess.run(  # noqa: S603 - the pinned ruff in this virtualenv
            [str(ruff), *args], capture_output=True, text=True, check=False, cwd=ROOT
        )
        assert done.returncode == 0, done.stdout + done.stderr
