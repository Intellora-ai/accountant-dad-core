"""Seventeen malicious pull requests, each one applied to a throwaway copy.

WHY THIS FILE EXISTS
--------------------
`tests/test_gate_contract.py` proves ci/gates.toml and the workflows agree with
each other. It cannot prove they agree with anything OUTSIDE the branch, and
that is the hole: `.github/workflows/pr-fast.yml` is `on: pull_request`, so
GitHub grades a pull request using the copy of the grader that is IN the pull
request. The contract binds a gate to a JOB, never to a STEP, so deleting the
`security-scan` step passes all eighteen contract tests, `ci/check_stubs.py`,
`ci-gate` and the nightly.

`ci/check_workflow_integrity.py` compares this tree against the copy on the
protected default branch. This file attacks it: every fixture is a real
weakening someone could ship, applied to a COPY in a temporary directory, never
to the repository.

The count is seventeen. If a fixture is ever removed to make a number look
better, `test_the_fixture_inventory_is_complete` fails.

WHAT A PASS HERE DOES NOT MEAN
------------------------------
Every fixture below is caught by a checker that lives in the same tree the
fixture is editing. A pull request that removes the gate AND removes the checker
is not caught by anything in this repository. Only a GitHub-side rule can fix
that - see artifacts/gate_integrity_audit.md, "remaining human configuration".
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CHECKER = ROOT / "ci" / "check_workflow_integrity.py"

# Only the files the checker reads. Copying the whole repository would make this
# slow for no gain.
COPIED = (
    ".github/workflows",
    "ci/gates.toml",
    "ci/gate_names.lock",
    "ci/check_aggregate.py",
    "ci/check_mutation.py",
    "ci/check_ruleset.py",
    "ci/check_stubs.py",
    "ci/check_workflow_integrity.py",
    "ci/test_protection.py",
    "ci/workflow_changes.ack",
    "tests/test_gate_contract.py",
    "tests/test_gate_integrity.py",
)

PR_FAST = ".github/workflows/pr-fast.yml"
FULL = ".github/workflows/full.yml"


# ---------------------------------------------------------------------------
# copying and editing a throwaway tree
# ---------------------------------------------------------------------------


def make_copy(destination: Path) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    for rel in COPIED:
        source = ROOT / rel
        target = destination / rel
        if source.is_dir():
            shutil.copytree(source, target, dirs_exist_ok=True)
        elif source.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    return destination


def read(tree: Path, rel: str) -> str:
    return (tree / rel).read_text(encoding="utf-8")


def write(tree: Path, rel: str, text: str) -> None:
    (tree / rel).write_text(text, encoding="utf-8")


def delete_step(tree: Path, rel: str, step_name: str) -> None:
    """Remove one `- name: <step_name>` block from a workflow.

    A step runs from its `      - name:` line to the next line at that indent or
    shallower. Nothing else in the file is touched, which is what makes the
    fixture a realistic single-step deletion rather than a rewrite.
    """
    lines = read(tree, rel).splitlines()
    matches = [
        i
        for i, line in enumerate(lines)
        if line.strip() == f"- name: {step_name}" and line.startswith("      -")
    ]
    assert matches, f"no step named {step_name!r} in {rel} - the fixture is stale"
    start = matches[0]
    end = start + 1
    while end < len(lines):
        line = lines[end]
        if (
            line.strip()
            and not line.startswith("        ")
            and not line.startswith("      #")
        ):
            break
        end += 1
    write(tree, rel, "\n".join(lines[:start] + lines[end:]) + "\n")


def rename_step(tree: Path, rel: str, old: str, new: str) -> None:
    text = read(tree, rel)
    write(tree, rel, text.replace(f"- name: {old}\n", f"- name: {new}\n", 1))


def drop_lock_name(tree: Path, name: str) -> None:
    lines = read(tree, "ci/gate_names.lock").splitlines()
    write(
        tree,
        "ci/gate_names.lock",
        "\n".join(line for line in lines if line.strip() != name) + "\n",
    )


def drop_gate(tree: Path, name: str) -> None:
    text = read(tree, "ci/gates.toml")
    blocks = text.split("[[gate]]")
    kept = [blocks[0]] + [
        b for b in blocks[1:] if f'name              = "{name}"' not in b
    ]
    write(tree, "ci/gates.toml", "[[gate]]".join(kept))


# ---------------------------------------------------------------------------
# the seventeen fixtures
# ---------------------------------------------------------------------------


def f01_delete_security_scan_step(tree: Path) -> None:
    delete_step(tree, PR_FAST, "security-scan")


def f02_delete_dependency_scan_step(tree: Path) -> None:
    delete_step(tree, FULL, "dependency-audit")


def f03_delete_mutation_step(tree: Path) -> None:
    delete_step(tree, FULL, "full-mutation")


def f04_delete_coverage_step(tree: Path) -> None:
    delete_step(tree, PR_FAST, "changed-coverage")


def f05_rename_required_job(tree: Path) -> None:
    text = read(tree, PR_FAST)
    text = text.replace("\n  pr-fast:\n", "\n  pr-fast-trusted:\n", 1)
    text = text.replace("needs: pr-fast\n", "needs: pr-fast-trusted\n")
    text = text.replace(
        "needs: [pr-fast, pr-full]", "needs: [pr-fast-trusted, pr-full]"
    )
    write(tree, PR_FAST, text)


def f06_rename_required_step(tree: Path) -> None:
    rename_step(tree, PR_FAST, "security-scan", "security-review")


def f07_edit_gate_names_lock(tree: Path) -> None:
    drop_lock_name(tree, "security-scan")


def f08_edit_workflow_and_lock_together(tree: Path) -> None:
    """The H2 bypass: shorten the lock AND the contract AND the workflow in one
    commit, so the in-tree comparison still balances at 19 == 19."""
    delete_step(tree, PR_FAST, "security-scan")
    delete_step(tree, FULL, "security-scan")
    drop_gate(tree, "security-scan")
    drop_lock_name(tree, "security-scan")


def f09_change_check_to_frozen_only(tree: Path) -> None:
    """`--check` is what makes `ruff format` fail instead of silently rewriting."""
    text = read(tree, PR_FAST)
    write(
        tree,
        PR_FAST,
        text.replace("uv run ruff format --check .", "uv run ruff format ."),
    )


def f10_make_the_protection_test_skip_in_ci(tree: Path) -> None:
    text = read(tree, "ci/test_protection.py")
    text = text.replace(
        "@pytest.fixture(autouse=True)",
        'pytestmark = pytest.mark.skipif(True, reason="flaky")\n\n\n'
        "@pytest.fixture(autouse=True)",
        1,
    )
    write(tree, "ci/test_protection.py", text)


def f11_replace_a_failure_with_continue_on_error(tree: Path) -> None:
    text = read(tree, PR_FAST)
    write(
        tree,
        PR_FAST,
        text.replace(
            "      - name: security-scan\n",
            "      - name: security-scan\n        continue-on-error: true\n",
            1,
        ),
    )


def f12_add_a_fake_job_with_the_required_old_name(tree: Path) -> None:
    """Rename the real job, then hang the required context on a stub."""
    text = read(tree, PR_FAST)
    text = text.replace("\n  pr-fast:\n", "\n  pr-fast-real:\n", 1)
    text = text.replace("needs: pr-fast\n", "needs: pr-fast-real\n")
    text = text.replace("needs: [pr-fast, pr-full]", "needs: [pr-fast-real, pr-full]")
    stub = (
        "\n  pr-fast:\n"
        "    name: pr-fast\n"
        "    runs-on: ubuntu-24.04\n"
        "    timeout-minutes: 5\n"
        "    steps:\n"
        "      - name: all good\n"
        '        run: uv run python -c "raise SystemExit(0)"\n'
    )
    write(tree, PR_FAST, text.replace("\njobs:\n", "\njobs:\n" + stub, 1))


def f13_remove_the_fail_closed_catchall(tree: Path) -> None:
    text = read(tree, "ci/check_aggregate.py")
    write(
        tree,
        "ci/check_aggregate.py",
        text.replace(
            "        else:\n"
            '            verdicts.append((job, "UNKNOWN", result))\n'
            "            ok = False\n",
            "",
            1,
        ),
    )


def f14_change_an_action_sha(tree: Path) -> None:
    text = read(tree, PR_FAST)
    write(
        tree,
        PR_FAST,
        text.replace(
            "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
            "actions/checkout@0000000000000000000000000000000000000000",
        ),
    )


def f15_add_an_unpinned_action(tree: Path) -> None:
    text = read(tree, PR_FAST)
    write(
        tree,
        PR_FAST,
        text.replace(
            "      - name: lint\n",
            "      - name: extra setup\n"
            "        uses: actions/setup-python@v5\n\n"
            "      - name: lint\n",
            1,
        ),
    )


def f16_add_pull_request_target(tree: Path) -> None:
    text = read(tree, PR_FAST)
    write(
        tree,
        PR_FAST,
        text.replace(
            "\non:\n  pull_request:\n",
            "\non:\n  pull_request_target:\n  pull_request:\n",
            1,
        ),
    )


def f17_expose_a_secret_to_pr_code(tree: Path) -> None:
    text = read(tree, PR_FAST)
    write(
        tree,
        PR_FAST,
        text.replace(
            "      - name: lint\n        run: uv run ruff check .\n",
            "      - name: lint\n        env:\n"
            "          KEY: ${{ secrets.ANTHROPIC_API_KEY }}\n"
            "        run: uv run ruff check .\n",
            1,
        ),
    )


FIXTURES: dict[str, Callable[[Path], None]] = {
    "delete security-scan step": f01_delete_security_scan_step,
    "delete dependency-scan step": f02_delete_dependency_scan_step,
    "delete mutation step": f03_delete_mutation_step,
    "delete coverage step": f04_delete_coverage_step,
    "rename required job": f05_rename_required_job,
    "rename required step": f06_rename_required_step,
    "edit ci/gate_names.lock": f07_edit_gate_names_lock,
    "edit workflow and lock file together": f08_edit_workflow_and_lock_together,
    "change --check to --frozen only": f09_change_check_to_frozen_only,
    "make the protection test skip in CI": f10_make_the_protection_test_skip_in_ci,
    "replace a failure with continue-on-error": (
        f11_replace_a_failure_with_continue_on_error
    ),
    "add a fake job with the required old name": (
        f12_add_a_fake_job_with_the_required_old_name
    ),
    "remove the fail-closed catch-all": f13_remove_the_fail_closed_catchall,
    "change an action SHA": f14_change_an_action_sha,
    "add an unpinned action": f15_add_an_unpinned_action,
    "add pull_request_target": f16_add_pull_request_target,
    "expose a secret to PR code": f17_expose_a_secret_to_pr_code,
}


# ---------------------------------------------------------------------------
# running the checker
# ---------------------------------------------------------------------------


def run_checker(
    tree: Path, trusted: Path, report: Path
) -> tuple[int, dict[str, object]]:
    result = subprocess.run(  # noqa: S603
        [
            sys.executable,
            str(CHECKER),
            "--tree",
            str(tree),
            "--trusted-tree",
            str(trusted),
            "--json",
            str(report),
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=ROOT,
    )
    payload: dict[str, object] = (
        json.loads(report.read_text(encoding="utf-8")) if report.is_file() else {}
    )
    return result.returncode, payload


@pytest.fixture(scope="module")
def pristine(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The policy every fixture is measured against. A copy, never the repository."""
    return make_copy(tmp_path_factory.mktemp("pristine"))


# ---------------------------------------------------------------------------
# the checker itself is honest before it is trusted
# ---------------------------------------------------------------------------


def test_an_untouched_copy_passes(pristine: Path, tmp_path: Path) -> None:
    """If an unchanged tree failed, every rejection below would prove nothing."""
    candidate = make_copy(tmp_path / "candidate")
    code, payload = run_checker(candidate, pristine, tmp_path / "report.json")
    assert code == 0, f"a clean tree was rejected: {payload}"
    assert payload["verdict"] == "PASS"


def test_the_checker_reads_its_policy_from_outside_the_tree() -> None:
    """The point of the whole exercise: the grader is not in the pull request."""
    source = CHECKER.read_text(encoding="utf-8")
    assert '"show", f"{self.ref}:{rel}"' in source, (
        "the trusted read must come from a git ref, not from the working tree"
    )
    assert "origin/main" in source


def test_the_fixture_inventory_is_complete() -> None:
    """Seventeen. Not fifteen, and not seventeen minus the awkward ones."""
    assert len(FIXTURES) == 17, sorted(FIXTURES)
    assert len(set(FIXTURES.values())) == 17, "two names point at the same fixture"


@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_every_malicious_pull_request_is_rejected(
    name: str, pristine: Path, tmp_path: Path
) -> None:
    candidate = make_copy(tmp_path / "candidate")
    FIXTURES[name](candidate)
    code, payload = run_checker(candidate, pristine, tmp_path / "report.json")
    blocking = payload.get("blocking", [])
    assert code == 1, f"{name!r} was ACCEPTED. Report: {json.dumps(payload, indent=2)}"
    assert blocking, f"{name!r} failed without naming a reason"


def test_deleting_the_security_scan_step_names_the_step(
    pristine: Path, tmp_path: Path
) -> None:
    """The specific hole this whole exercise started from, named exactly."""
    candidate = make_copy(tmp_path / "candidate")
    f01_delete_security_scan_step(candidate)
    _, payload = run_checker(candidate, pristine, tmp_path / "report.json")
    codes = {str(f["code"]) for f in payload["blocking"]}  # type: ignore[index]
    assert "STEP_REMOVED" in codes, payload


def test_the_lock_file_bypass_is_caught_by_comparing_with_main(
    pristine: Path, tmp_path: Path
) -> None:
    """Shortening the lock and the contract together balances inside the tree.

    tests/test_gate_contract.py compares two files in the same commit, so 19 == 19
    and `len(gates()) >= len(locked)` both hold. Main still says twenty.
    """
    candidate = make_copy(tmp_path / "candidate")
    f08_edit_workflow_and_lock_together(candidate)
    _, payload = run_checker(candidate, pristine, tmp_path / "report.json")
    codes = {str(f["code"]) for f in payload["blocking"]}  # type: ignore[index]
    assert {"LOCK_NAME_REMOVED", "GATE_REMOVED"} <= codes, payload


def test_a_change_finding_can_be_acknowledged_but_a_removal_cannot(
    pristine: Path, tmp_path: Path
) -> None:
    """The two classes behave differently, and the difference is the whole point.

    An action SHA bump is legitimate and is unblocked by writing it down. A
    deleted step is not legitimate and no amount of writing it down helps.
    """

    def fingerprints(payload: dict[str, object]) -> list[str]:
        blocking: list[dict[str, str]] = payload["blocking"]  # type: ignore[assignment]
        return [f["fingerprint"] for f in blocking]

    def ack_everything_and_rerun(
        tree: Path, payload: dict[str, object], report: Path
    ) -> tuple[int, dict[str, object]]:
        (tree / "ci" / "workflow_changes.ack").write_text(
            "\n".join(fingerprints(payload)) + "\n", encoding="utf-8"
        )
        return run_checker(tree, pristine, report)

    # An action SHA bump. Every finding it raises is CHANGE, so writing them all
    # down clears it.
    bump = make_copy(tmp_path / "bump")
    f14_change_an_action_sha(bump)
    code, payload = run_checker(bump, pristine, tmp_path / "bump.json")
    assert code == 1, "an unacknowledged SHA change should not pass silently"
    severities = {str(f["severity"]) for f in payload["blocking"]}  # type: ignore[index]
    assert severities == {"CHANGE"}, payload
    code, payload = ack_everything_and_rerun(bump, payload, tmp_path / "bump2.json")
    assert code == 0, payload

    # A deleted step. Acknowledging every finding changes nothing.
    deletion = make_copy(tmp_path / "deletion")
    f01_delete_security_scan_step(deletion)
    _, payload2 = run_checker(deletion, pristine, tmp_path / "del.json")
    code2, payload3 = ack_everything_and_rerun(
        deletion, payload2, tmp_path / "del2.json"
    )
    assert code2 == 1, f"a REMOVAL was acknowledged away: {payload3}"
    assert "STEP_REMOVED" in {str(f["code"]) for f in payload3["blocking"]}  # type: ignore[index]


def test_the_checker_cannot_survive_its_own_deletion(
    pristine: Path, tmp_path: Path
) -> None:
    """The negative control. Written down so nobody later reads 17/17 as safety.

    Every fixture above is caught by a checker living in the tree the fixture is
    editing. Remove the gate AND the checker and there is nothing left to object:
    the step that would have run it is gone, and the test that would have run it
    is gone. This asserts that limit rather than hiding it.

    The two things that would fix it are GitHub-side and owner-only:
      * a file_path_restriction ruleset rule on .github/** and ci/**
      * CODEOWNERS with require_code_owner_review and >= 1 approval
    """
    candidate = make_copy(tmp_path / "candidate")
    f01_delete_security_scan_step(candidate)
    (candidate / "ci" / "check_workflow_integrity.py").unlink()
    (candidate / "tests" / "test_gate_integrity.py").unlink()

    # Reality: the pull request's own copy is what CI would execute, and it is
    # not there. Nothing runs, so nothing objects.
    assert not (candidate / "ci" / "check_workflow_integrity.py").is_file()
    result = subprocess.run(  # noqa: S603
        [sys.executable, str(candidate / "ci" / "check_workflow_integrity.py")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0, "a deleted checker somehow ran"
    assert "No such file" in result.stderr or "can't open file" in result.stderr

    # And for completeness: had it run, it would have objected. The problem is
    # not that the check is weak. The problem is that the check is optional.
    code, payload = run_checker(candidate, pristine, tmp_path / "report.json")
    codes = {str(f["code"]) for f in payload["blocking"]}  # type: ignore[index]
    assert code == 1
    assert "ANCHOR_MISSING" in codes, payload
    assert "STEP_REMOVED" in codes, payload


# ---------------------------------------------------------------------------
# the drift audit must notice a silent revert, not just a live green
# ---------------------------------------------------------------------------

RULESET_AUDIT = ROOT / "ci" / "check_ruleset.py"

# Ruleset 20557129 as it actually stands, read live 2026-08-10 after the owner
# pinned both required contexts. Canned so this test needs no network and no
# token: what is under test is the AUDIT, not GitHub.
LIVE_RULESET: dict[str, object] = {
    "id": 20557129,
    "name": "main protection",
    "target": "branch",
    "enforcement": "active",
    "bypass_actors": [],
    "current_user_can_bypass": "never",
    "conditions": {"ref_name": {"exclude": [], "include": ["~DEFAULT_BRANCH"]}},
    "rules": [
        {"type": "deletion", "parameters": {}},
        {"type": "non_fast_forward", "parameters": {}},
        {
            "type": "pull_request",
            "parameters": {
                "required_approving_review_count": 0,
                "dismiss_stale_reviews_on_push": False,
                "required_reviewers": [],
                "require_code_owner_review": False,
                "require_last_push_approval": False,
                "required_review_thread_resolution": False,
                "allowed_merge_methods": ["squash", "merge", "rebase"],
            },
        },
        {
            "type": "required_status_checks",
            "parameters": {
                "strict_required_status_checks_policy": True,
                "do_not_enforce_on_create": False,
                "required_status_checks": [
                    {"context": "pr-fast", "integration_id": 15368},
                    {"context": "ci-gate", "integration_id": 15368},
                ],
            },
        },
    ],
}


def status_checks(ruleset: dict[str, object]) -> list[dict[str, object]]:
    rules: list[dict[str, object]] = ruleset["rules"]  # type: ignore[assignment]
    for rule in rules:
        if rule["type"] == "required_status_checks":
            params: dict[str, object] = rule["parameters"]  # type: ignore[assignment]
            checks: list[dict[str, object]] = params["required_status_checks"]  # type: ignore[assignment]
            return checks
    raise AssertionError("the canned ruleset lost its required_status_checks rule")


def run_ruleset_audit(tmp: Path, ruleset: dict[str, object]) -> tuple[int, str]:
    """Run the real ci/check_ruleset.py against a canned API.

    A fake `gh` on PATH answers the two endpoints the audit reads. Running the
    CLI rather than importing it means the argument handling and the exit code
    are under test too.
    """
    fixtures = tmp / "fixtures"
    fixtures.mkdir(parents=True, exist_ok=True)
    (fixtures / "rulesets.json").write_text(
        json.dumps([{k: ruleset[k] for k in ("id", "name", "target", "enforcement")}]),
        encoding="utf-8",
    )
    (fixtures / "ruleset.json").write_text(json.dumps(ruleset), encoding="utf-8")

    fake_bin = tmp / "bin"
    fake_bin.mkdir(parents=True, exist_ok=True)
    gh = fake_bin / "gh"
    gh.write_text(
        "#!/bin/sh\n"
        f'case "$2" in\n'
        f'  */rulesets) cat "{fixtures}/rulesets.json" ;;\n'
        f'  */rulesets/*) cat "{fixtures}/ruleset.json" ;;\n'
        '  *) echo "unexpected endpoint: $2" >&2; exit 1 ;;\n'
        "esac\n",
        encoding="utf-8",
    )
    gh.chmod(0o755)

    env = dict(os.environ, PATH=f"{fake_bin}:{os.environ['PATH']}")
    result = subprocess.run(  # noqa: S603
        [sys.executable, str(RULESET_AUDIT)],
        capture_output=True,
        text=True,
        check=False,
        cwd=ROOT,
        env=env,
    )
    return result.returncode, result.stdout + result.stderr


def test_the_drift_audit_passes_on_the_ruleset_as_it_actually_stands(
    tmp_path: Path,
) -> None:
    code, out = run_ruleset_audit(tmp_path, deepcopy(LIVE_RULESET))
    assert code == 0, out


def test_the_drift_audit_notices_an_unpinned_required_check(tmp_path: Path) -> None:
    """The gap the coordinator found on 2026-08-10.

    Until then ci/check_ruleset.py asserted the required context NAME and never
    looked at integration_id. An unpinned `pr-fast` reported a clean 9/9, while
    any app on this repository with `commit statuses: write` could write
    `pr-fast: success` and satisfy the requirement with no gate having run.
    """
    ruleset = deepcopy(LIVE_RULESET)
    del status_checks(ruleset)[0]["integration_id"]
    code, out = run_ruleset_audit(tmp_path, ruleset)
    assert code == 1, out
    assert "pr-fast is pinned to a single app" in out


def test_the_drift_audit_notices_a_check_repinned_to_another_app(
    tmp_path: Path,
) -> None:
    """`claude` is app 1236702 on this repository and also reports check suites.

    Repinning is not the same as unpinning and must not be mistaken for it.
    """
    ruleset = deepcopy(LIVE_RULESET)
    status_checks(ruleset)[1]["integration_id"] = 1236702
    code, out = run_ruleset_audit(tmp_path, ruleset)
    assert code == 1, out
    assert "ci-gate is pinned to app 15368" in out


def test_the_drift_audit_notices_the_merge_queue_check_being_dropped(
    tmp_path: Path,
) -> None:
    """It only ever checked meta.required_pr_check. `ci-gate` could vanish."""
    ruleset = deepcopy(LIVE_RULESET)
    del status_checks(ruleset)[1]
    code, out = run_ruleset_audit(tmp_path, ruleset)
    assert code == 1, out
    assert "ci-gate is required" in out


def test_the_drift_audit_notices_a_ruleset_that_matches_no_branch(
    tmp_path: Path,
) -> None:
    """Every rule intact, protecting nothing. The old audit passed 9/9 on this."""
    ruleset = deepcopy(LIVE_RULESET)
    ruleset["conditions"] = {
        "ref_name": {"include": ["refs/heads/nope"], "exclude": []}
    }
    code, out = run_ruleset_audit(tmp_path, ruleset)
    assert code == 1, out
    assert "actually applies to the default branch" in out


def test_the_drift_audit_notices_a_bypass_actor(tmp_path: Path) -> None:
    ruleset = deepcopy(LIVE_RULESET)
    ruleset["bypass_actors"] = [{"actor_id": 1, "actor_type": "Integration"}]
    code, out = run_ruleset_audit(tmp_path, ruleset)
    assert code == 1, out
    assert "no bypass actors" in out


def test_the_review_settings_are_reported_even_though_they_are_not_asserted(
    tmp_path: Path,
) -> None:
    """The honest gap, kept visible.

    The owner has not set a review requirement, so there is no number to assert
    against and inventing one is forbidden. Printing the live values means a
    later silent revert is at least visible in the step summary. This test
    exists so the printing cannot be quietly dropped.
    """
    code, out = run_ruleset_audit(tmp_path, deepcopy(LIVE_RULESET))
    assert code == 0, out
    assert "observed, NOT asserted" in out
    assert "pull_request.required_approving_review_count: 0" in out
    assert "pull_request.require_code_owner_review: False" in out


# ---------------------------------------------------------------------------
# the standing facts this repository must keep
# ---------------------------------------------------------------------------


def test_the_gate_count_is_twenty_and_the_lock_agrees() -> None:
    """Owner rule: the number may only go up. Recording it makes a drop visible."""
    locked = [
        line.strip()
        for line in (ROOT / "ci" / "gate_names.lock").read_text().splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    assert len(locked) == 20, locked


def test_the_declared_but_unexecuted_gates_are_exactly_the_two_known_ones() -> None:
    """`uv lock --check` is gate 1 of 20, required, blocking - and runs nowhere.

    The only `uv lock` in .github/workflows is a comment (pr-fast.yml:65). The
    workflow runs `uv sync --frozen`, and --frozen skips the up-to-date check;
    --locked is the flag that errors. The real check lives in scripts/guards:119,
    whose own header says THIS IS AN ACCELERATOR, NOT A GATE, and `--no-verify`
    skips it.

    `cached-mutation` is deliberately parked, with the reason in ci/gates.toml.

    This test is the record. It fails the moment a third gate goes unwired, and
    it fails when one of these two is wired up - at which point delete the name
    from the list, do not widen it.
    """
    result = subprocess.run(  # noqa: S603
        [sys.executable, str(CHECKER)],
        capture_output=True,
        text=True,
        check=False,
        cwd=ROOT,
    )
    if result.returncode == 2:
        pytest.skip(
            "LOCAL_ENVIRONMENT_UNAVAILABLE: no protected ref to compare against"
        )
    match = re.search(
        r"NOT EXECUTED IN ANY WORKFLOW STEP[^:]*: (\[[^\]]*\])", result.stdout
    )
    assert match, f"the unwired-gate warning is gone entirely:\n{result.stdout}"
    assert match.group(1) == "['cached-mutation', 'lockfile']", match.group(1)


def test_this_tree_still_agrees_with_the_protected_branch() -> None:
    """The checker, run for real, against the real origin/main."""
    result = subprocess.run(  # noqa: S603
        [sys.executable, str(CHECKER)],
        capture_output=True,
        text=True,
        check=False,
        cwd=ROOT,
    )
    if result.returncode == 2:
        pytest.skip(
            "LOCAL_ENVIRONMENT_UNAVAILABLE: no protected ref to compare against"
        )
    assert result.returncode == 0, result.stdout + result.stderr
