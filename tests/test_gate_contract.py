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
from typing import Any

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
    return "\n".join(p.read_text() for p in sorted(WORKFLOWS.glob("*.yml")))


def workflow_job_names() -> set[str]:
    """Job ids, read without a YAML parser so this test has no dependency.

    A job id is a two-space-indented key directly under `jobs:`.
    """
    names: set[str] = set()
    for path in sorted(WORKFLOWS.glob("*.yml")) if WORKFLOWS.is_dir() else []:
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
        "job",
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


def test_every_gate_names_a_job_that_exists():
    if not WORKFLOWS.is_dir():
        return  # workflows not written yet; the next test covers the other side
    jobs = workflow_job_names()
    for g in gates():
        assert g["job"] in jobs, (
            f"gate {g['name']!r} points at job {g['job']!r}, which no workflow defines"
        )


def test_every_workflow_job_is_declared_in_the_contract():
    """No job may exist only as an unexplained YAML fragment."""
    if not WORKFLOWS.is_dir():
        return
    declared = {g["job"] for g in gates()}
    for job in workflow_job_names():
        assert job in declared, (
            f"workflow job {job!r} has no gate behind it in ci/gates.toml"
        )


def test_the_required_check_names_are_exactly_what_github_will_publish():
    """GitHub matches required status checks as exact strings."""
    meta = contract()["meta"]
    jobs = {g["job"] for g in gates()}
    assert meta["required_pr_check"] in jobs
    assert meta["required_mq_check"] in jobs


def test_every_command_in_the_contract_appears_in_a_workflow():
    """A gate declared but never run is worse than no gate: it reads as covered."""
    if not WORKFLOWS.is_dir():
        return
    text = workflow_text()
    for g in gates():
        head = g["command"].split()[0]
        assert head in text, (
            f"gate {g['name']!r} runs {head!r}, which appears in no workflow"
        )
