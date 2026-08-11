"""ci/gates.toml is the one source of truth, and this is what keeps it true.

The previous repository had 24 CI jobs, 13 of them permanently-red placeholders,
and nothing tying a job to a reason for existing. Every assertion here exists to
make that specific failure impossible:

  * a gate declared twice
  * two gates running the same command
  * a gate with no threshold, no owner, or no real command
  * a workflow job with no gate behind it
  * a gate whose job does not exist
  * a required check name that does not match what GitHub will publish
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any, cast

import pytest

ROOT = Path(__file__).resolve().parent.parent
CONTRACT = ROOT / "ci" / "gates.toml"
WORKFLOWS = ROOT / ".github" / "workflows"

STUB_MARKERS = (
    "NOT IMPLEMENTED",
    "TODO",
    "placeholder",
    "not implemented",
)


def contract() -> dict[str, Any]:
    with CONTRACT.open("rb") as fh:
        return tomllib.load(fh)


def gates() -> list[dict[str, Any]]:
    return contract()["gate"]


def workflow_text() -> str:
    if not WORKFLOWS.is_dir():
        return ""
    return "\n".join(p.read_text() for p in gate_workflows())


def gate_workflows() -> list[Path]:
    """Workflow files that are expected to carry gates.

    ci/gates.toml lists any workflow that deliberately has none. The exclusion
    is declared in the contract, never assumed here.
    """
    if not WORKFLOWS.is_dir():
        return []
    excluded = set(contract()["meta"].get("non_gate_workflows", []))
    return [p for p in sorted(WORKFLOWS.glob("*.yml")) if p.name not in excluded]


def workflow_job_names() -> set[str]:
    """Job ids, read without a YAML parser so this test has no dependency.

    A job id is a two-space-indented key directly under `jobs:`.
    """
    names: set[str] = set()
    for path in gate_workflows():
        in_jobs = False
        for line in path.read_text().splitlines():
            if re.match(r"^jobs:\s*$", line):
                in_jobs = True
                continue
            if in_jobs:
                if line and not line.startswith(" ") and not line.startswith("#"):
                    in_jobs = False
                    continue
                m = re.match(r"^  ([A-Za-z0-9_-]+):\s*$", line)
                if m:
                    names.add(m.group(1))
    return names


# ---- the contract is well formed -------------------------------------------


def test_the_contract_exists_and_parses():
    assert CONTRACT.is_file(), f"{CONTRACT} is the source of truth and must exist"
    assert gates(), "a contract with no gates is not a contract"


def test_no_duplicate_gate_names():
    names = [g["name"] for g in gates()]
    dupes = {n for n in names if names.count(n) > 1}
    assert not dupes, f"declared more than once: {sorted(dupes)}"


def test_no_duplicate_commands():
    """Two gates running the same command is the shape the previous repository
    took when build, typecheck, lint and tests each existed in two workflows."""
    cmds = [g["command"] for g in gates()]
    dupes = {c for c in cmds if cmds.count(c) > 1}
    assert not dupes, f"same command declared by more than one gate: {sorted(dupes)}"


def test_every_gate_has_every_required_field():
    fields = (
        "name",
        "command",
        "threshold",
        "required",
        "status",
        "owner",
        "jobs",
        "artifact",
        "failure_behaviour",
    )
    for g in gates():
        missing = [f for f in fields if f not in g]
        assert not missing, f"gate {g.get('name')!r} is missing {missing}"


def test_every_gate_has_an_owner_who_is_a_person():
    for g in gates():
        owner = g["owner"]
        assert owner and not owner.endswith("team"), (
            f"gate {g['name']!r} has owner {owner!r}. "
            "A requirement nobody owns is an assumption that got promoted."
        )


def test_every_threshold_is_a_number():
    for g in gates():
        assert isinstance(g["threshold"], int), (
            f"gate {g['name']!r} has a non-numeric threshold {g['threshold']!r}"
        )


def test_the_only_non_zero_threshold_is_the_owner_set_ninety():
    """No number appears here that the owner did not give."""
    for g in gates():
        assert g["threshold"] in (0, 90), (
            f"gate {g['name']!r} uses threshold {g['threshold']}, which the owner "
            "never set. The only owner-set number is 90."
        )


def test_every_gate_runs_a_real_command():
    for g in gates():
        cmd = g["command"].strip()
        assert cmd, f"gate {g['name']!r} has no command"
        assert not cmd.startswith("echo"), (
            f"gate {g['name']!r} only echoes. A gate that cannot fail is not a gate."
        )
        assert not any(m.lower() in cmd.lower() for m in STUB_MARKERS), (
            f"gate {g['name']!r} is a placeholder: {cmd}"
        )


def test_every_gate_blocks_or_says_why_not():
    for g in gates():
        assert g["failure_behaviour"] in ("block", "report"), (
            f"gate {g['name']!r} has failure_behaviour {g['failure_behaviour']!r}"
        )


def test_no_gate_is_declared_and_then_switched_off():
    for g in gates():
        assert g["status"] == "active", (
            f"gate {g['name']!r} is {g['status']!r}. An inactive gate is a gate "
            "removed, and the number of gates may only go up."
        )


def test_the_coverage_core_is_pinned():
    """Measured on this repository: without it, mutation reads 87.92 percent
    instead of 94.34 percent, because test-to-line mapping is silently
    incomplete on the sysmon core."""
    assert contract()["meta"]["coverage_core"] == "pytrace"


# ---- the contract and the workflows agree -----------------------------------


def test_every_gate_names_at_least_one_job():
    """`jobs` is a non-empty list of unique names. A gate that runs nowhere is
    a gate in name only."""
    for g in gates():
        raw = g["jobs"]
        assert isinstance(raw, list), f"gate {g['name']!r} has jobs={raw!r}, not a list"
        js: list[str] = [str(j) for j in cast(list[Any], raw)]
        assert js, f"gate {g['name']!r} runs in no job at all"
        assert len(js) == len(set(js)), f"gate {g['name']!r} lists a job twice: {js}"


def test_every_gate_names_jobs_that_exist():
    if not WORKFLOWS.is_dir():
        return  # workflows not written yet; the next test covers the other side
    jobs = workflow_job_names()
    for g in gates():
        unknown = [j for j in g["jobs"] if j not in jobs]
        assert not unknown, (
            f"gate {g['name']!r} points at {unknown}, which no workflow defines"
        )


def test_every_workflow_job_is_declared_in_the_contract():
    """No job may exist only as an unexplained YAML fragment."""
    if not WORKFLOWS.is_dir():
        return
    declared = {j for g in gates() for j in g["jobs"]}
    excused = set(contract()["meta"].get("non_gate_jobs", []))
    for job in workflow_job_names():
        assert job in declared or job in excused, (
            f"workflow job {job!r} has no gate behind it in ci/gates.toml, "
            "and is not declared in meta.non_gate_jobs"
        )


def test_the_required_check_names_are_exactly_what_github_will_publish():
    """GitHub matches required status checks as exact strings."""
    meta = contract()["meta"]
    jobs = {j for g in gates() for j in g["jobs"]}
    assert meta["required_pr_check"] in jobs
    assert meta["required_mq_check"] in jobs


def test_every_command_in_the_contract_appears_in_a_workflow():
    """A gate declared but never run is worse than no gate: it reads as covered.

    MATCHES THE FIRST TOKEN ONLY, and that is a known weakness rather than an
    oversight. `uv lock --check` reduces to `uv`, which appears in every job in
    every workflow, so this assertion cannot fail for any gate whose command
    starts with `uv` — which is most of them.

    It is left as it is because tightening it here would turn one measured
    defect into a red build for every gate at once.
    `test_the_lockfile_gate_is_actually_enforced` below is the narrow, honest
    version: it names the one gate this weakness is hiding, and it is an xfail
    rather than a deletion, so fixing the workflow turns it green rather than
    leaving a passing test that proves nothing.
    """
    if not WORKFLOWS.is_dir():
        return
    text = workflow_text()
    for g in gates():
        head = g["command"].split()[0]
        assert head in text, (
            f"gate {g['name']!r} runs {head!r}, which appears in no workflow"
        )


def _sync_steps() -> list[str]:
    """Every `uv sync` command line in every workflow, whitespace collapsed."""
    if not WORKFLOWS.is_dir():
        return []
    return [
        " ".join(line.split())
        for line in workflow_text().splitlines()
        if "uv sync" in line
    ]


def test_what_the_lockfile_gate_actually_runs_today() -> None:
    """WHAT IS MEASURED. This test PINS A DEFECT; see the xfail below.

    `ci/gates.toml` declares the `lockfile` gate as `uv lock --check`, threshold
    0, required, active. No workflow runs that command. What runs instead is
    `uv sync --extra dev --frozen`, under a comment claiming --frozen is the
    gate.

    It is the opposite. From uv's own CLI reference:

        --frozen  "Run without updating the uv.lock file. Instead of checking
                   if the lockfile is up-to-date, uses the versions in the
                   lockfile as the source of truth."
        --locked  "Assert that the uv.lock will remain unchanged. Requires that
                   the lockfile is up-to-date. If the lockfile is missing or
                   needs to be updated, uv will exit with an error."

    --frozen is the one flag that guarantees the check is SKIPPED. So a pull
    request may change pyproject.toml, leave uv.lock stale, and go green, with
    CI resolving a dependency set nobody recorded.

    The one-line fix is `--frozen` -> `--locked` in
    `.github/workflows/pr-fast.yml`. It is recorded in `docs/OWNER_WORK.md`
    because `.github/` is not writable from here.
    """
    steps = _sync_steps()
    if not steps:
        return
    assert any("--frozen" in step for step in steps), (
        "if no sync step passes --frozen any more, this pin is stale and the "
        "xfail below should be green - remove this test and keep that one"
    )


@pytest.mark.xfail(
    strict=True,
    reason="the lockfile gate is declared but not enforced; docs/OWNER_WORK.md",
)
def test_the_lockfile_gate_is_actually_enforced() -> None:
    """The behaviour the contract promises, and the workflow does not deliver.

    Strict, so the day somebody changes --frozen to --locked this stops being
    an expected failure and becomes a hard failure if the test is not removed.
    A defect pinned by an xfail is visible in a green run; a defect pinned by a
    comment is not.
    """
    steps = _sync_steps()
    assert steps, "no `uv sync` step exists at all, so nothing installs anything"
    assert all("--locked" in step for step in steps), (
        "every `uv sync` that stands in for the `lockfile` gate must pass "
        "--locked. --frozen skips the very check the gate declares: "
        f"{steps}"
    )


# ---- the migration guard ----------------------------------------------------


def test_the_gate_name_set_matches_the_lock_file():
    """The schema changed from `job` to `jobs`. This proves nothing else did.

    ci/gate_names.lock holds the gate-name set as it stood before the change.
    Any gate added, removed, renamed or duplicated fails here until the lock
    file is updated deliberately, in the same commit, with a stated reason.
    """
    lock = ROOT / "ci" / "gate_names.lock"
    assert lock.is_file(), "ci/gate_names.lock is missing; the migration guard is gone"
    locked = sorted(
        line.strip()
        for line in lock.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    )
    current = sorted(g["name"] for g in gates())

    added = sorted(set(current) - set(locked))
    removed = sorted(set(locked) - set(current))
    assert not removed, (
        f"gate(s) REMOVED: {removed}. The number of gates may only go up. "
        "A diff that shortens this list is a violation regardless of reason."
    )
    assert not added, (
        f"gate(s) added without updating ci/gate_names.lock: {added}. "
        "Adding a gate is allowed; adding one silently is not."
    )
    assert current == locked


def test_the_gate_count_has_not_gone_down():
    lock = ROOT / "ci" / "gate_names.lock"
    locked = [
        line.strip()
        for line in lock.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]
    assert len(gates()) >= len(locked), (
        f"{len(gates())} gates now, {len(locked)} in the lock file. "
        "The count may only go up."
    )
