"""Compare this tree's gates against the copy on the protected default branch.

THE PROBLEM THIS EXISTS FOR
---------------------------
`.github/workflows/pr-fast.yml` is `on: pull_request`. GitHub runs the copy of
that file that is IN the pull request. So the pull request supplies its own
grader. Proven on this repository: PR #12 added three steps to pr-fast.yml and
its own pr-fast run executed exactly those three steps
(run 31236026164, job 93048552980, steps 10-12).

Every in-repo guard shares that defect. `ci/gate_names.lock` cannot detect its
own deletion, because the commit that shortens the lock file is the commit that
shortens ci/gates.toml, and `tests/test_gate_contract.py` compares the two files
inside the same tree.

WHAT THIS DOES ABOUT IT
-----------------------
It reads the policy from a place the pull request cannot edit while it is being
graded: the protected default branch, via `git show origin/main:<path>`. The
comparison is therefore between the branch's proposal and main's standing
answer, not between two files the same author just wrote.

WHAT IT DOES NOT DO - SAY THIS OUT LOUD
---------------------------------------
1. Once a pull request merges, main IS the merged content. This closes the loop
   for the pull request under review; it does not survive the merge. Only a
   GitHub-side rule (file_path_restriction on `.github/**` and `ci/**`, or
   CODEOWNERS with require_code_owner_review and at least one approval) makes a
   `.github` change need a second human.
2. Nothing inside a tree can stop a change from deleting the thing inside that
   tree that would have objected. A pull request that removes a gate AND
   removes this checker is not caught by this checker. Two anchors are required
   (this file and tests/test_gate_integrity.py) so the evasion costs two visible
   deletions instead of one, and so the diff names them. That is a cost
   increase, not a mechanism.

CLASSES OF FINDING
------------------
REMOVAL    a gate, job, step, flag, symbol or fail-closed branch that main has
           and this tree does not. Never acknowledgeable. Standing rule: the
           number of gates may only go up.
WEAKENING  a construct with no legitimate use here - an unsafe trigger, a secret
           reachable from pull-request code, continue-on-error, an unpinned
           action. Never acknowledgeable.
CHANGE     something that has a legitimate use and still needs a human yes - an
           action SHA bump, a changed command, a new job or step. Blocked unless
           its fingerprint is written down in ci/workflow_changes.ack. The ack
           file lives in the repository, so a pull request can write its own ack
           line: it is POLICY, not mechanism. What it buys is that the change
           must be stated in one greppable place instead of hiding in YAML.

    python ci/check_workflow_integrity.py
    python ci/check_workflow_integrity.py --json report.json
    python ci/check_workflow_integrity.py --tree DIR --trusted-tree DIR
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent

WORKFLOW_DIR = ".github/workflows"
GATES = "ci/gates.toml"
GATE_LOCK = "ci/gate_names.lock"
ACK_FILE = "ci/workflow_changes.ack"

# Anchors: the two places this check is invoked from. If main has one and this
# tree does not, the check is being removed rather than satisfied.
ANCHORS = ("ci/check_workflow_integrity.py", "tests/test_gate_integrity.py")

# Guard sources whose shape is compared against main. A function that main has
# and this tree does not is a deleted guard, whatever the commit message says.
PROTECTED_SOURCES = (
    "ci/check_aggregate.py",
    "ci/check_mutation.py",
    "ci/check_ruleset.py",
    "ci/check_stubs.py",
    "ci/check_workflow_integrity.py",
    "ci/test_protection.py",
    "tests/test_gate_contract.py",
    "tests/test_gate_integrity.py",
)

# ci/test_protection.py must not be able to pass by never running. These
# sentinels are what the fixed version uses to distinguish "no local network"
# from "CI is not wired up", and their absence means the distinction is gone.
PROTECTION_SENTINELS = ("GITHUB_ACTIONS", "LOCAL_ENVIRONMENT_UNAVAILABLE")

# Symbols whose DELETION is a strengthening, so GUARD_SYMBOL_REMOVED must not
# fire on them. One entry, with its reason, rather than a general escape hatch:
# `pytestmark` in ci/test_protection.py was the unconditional module-level skip
# that made all nine live protection tests skip on every hosted run. Removing it
# is the fix, and the PROTECTION_TEST_SKIPPABLE check below forbids it coming
# back.
STRENGTHENING_REMOVALS: dict[str, frozenset[str]] = {
    "ci/test_protection.py": frozenset({"pytestmark"}),
}

UNSAFE_TRIGGERS = ("pull_request_target", "workflow_run")
PR_TRIGGERS = ("pull_request", "pull_request_target", "merge_group")

SHA_PIN = re.compile(r"^[0-9a-f]{40}$")
FLAG = re.compile(r"--[A-Za-z0-9][A-Za-z0-9-]*")
PUNCTUATION_ONLY = re.compile(r"^[^A-Za-z0-9]+$")

REMOVAL = "REMOVAL"
WEAKENING = "WEAKENING"
CHANGE = "CHANGE"

EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_NO_TRUSTED_SOURCE = 2


# ---------------------------------------------------------------------------
# where the two trees come from
# ---------------------------------------------------------------------------


class Source:
    """One tree of files. Either the working copy or a ref in git."""

    label: str

    def read(self, rel: str) -> str | None:
        raise NotImplementedError

    def workflows(self) -> list[str]:
        raise NotImplementedError


class DirSource(Source):
    def __init__(self, root: Path, label: str | None = None) -> None:
        self.root = root.resolve()
        self.label = label or str(self.root)

    def read(self, rel: str) -> str | None:
        path = self.root / rel
        if not path.is_file():
            return None
        return path.read_text(encoding="utf-8")

    def workflows(self) -> list[str]:
        directory = self.root / WORKFLOW_DIR
        if not directory.is_dir():
            return []
        names = sorted(
            p.name for p in directory.iterdir() if p.suffix in (".yml", ".yaml")
        )
        return [f"{WORKFLOW_DIR}/{n}" for n in names]


class GitSource(Source):
    """A ref. `git show ref:path` cannot be influenced by the working tree."""

    def __init__(self, ref: str, repo: Path) -> None:
        self.ref = ref
        self.repo = repo
        self.label = f"git {ref}"

    def _git(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603
            ["git", *args],  # noqa: S607
            capture_output=True,
            text=True,
            check=False,
            cwd=self.repo,
        )

    def usable(self) -> bool:
        return (
            self._git(
                "rev-parse", "--verify", "--quiet", f"{self.ref}^{{commit}}"
            ).returncode
            == 0
        )

    def read(self, rel: str) -> str | None:
        result = self._git("show", f"{self.ref}:{rel}")
        if result.returncode != 0:
            return None
        return result.stdout

    def workflows(self) -> list[str]:
        result = self._git("ls-tree", "-r", "--name-only", self.ref, WORKFLOW_DIR)
        if result.returncode != 0:
            return []
        return sorted(
            line.strip()
            for line in result.stdout.splitlines()
            if line.strip().endswith((".yml", ".yaml"))
        )


def trusted_source(repo: Path, ref: str | None) -> GitSource | None:
    """The first readable protected ref. None means there is no trusted policy."""
    candidates = [ref] if ref else ["origin/main", "refs/remotes/origin/main", "main"]
    for candidate in candidates:
        source = GitSource(candidate, repo)
        if source.usable():
            return source
    return None


# ---------------------------------------------------------------------------
# reading a workflow without a YAML dependency (stdlib only, `dependencies = []`)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Step:
    name: str
    uses: str
    body: str

    @property
    def searchable(self) -> str:
        return f"{self.uses}\n{self.body}"


@dataclass(frozen=True)
class Job:
    job_id: str
    steps: tuple[Step, ...]

    def step_names(self) -> list[str]:
        return [s.name for s in self.steps]


@dataclass(frozen=True)
class Workflow:
    path: str
    triggers: tuple[str, ...]
    jobs: tuple[Job, ...]
    text: str
    stripped: str

    def job_ids(self) -> list[str]:
        return [j.job_id for j in self.jobs]


def strip_comments(text: str) -> str:
    """Drop YAML comments. A gate named only in a comment is not a gate.

    `# ` after non-space content, or a line whose first non-space is `#`.
    """
    out: list[str] = []
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            out.append("")
            continue
        out.append(re.sub(r"\s+#.*$", "", line))
    return "\n".join(out)


def parse_workflow(path: str, text: str) -> Workflow:
    lines = text.splitlines()
    triggers: list[str] = []
    jobs: list[Job] = []

    in_on = False
    for line in lines:
        if re.match(r"^on:\s*$", line):
            in_on = True
            continue
        if in_on:
            if line and not line.startswith(" "):
                in_on = False
                continue
            m = re.match(r"^  ([A-Za-z0-9_]+):", line)
            if m:
                triggers.append(m.group(1))

    in_jobs = False
    job_id: str | None = None
    steps: list[Step] = []
    step_name: str | None = None
    step_uses: str = ""
    step_body: list[str] = []

    def close_step() -> None:
        nonlocal step_name, step_uses, step_body
        if step_name is not None:
            steps.append(Step(step_name, step_uses, "\n".join(step_body)))
        step_name, step_uses, step_body = None, "", []

    def close_job() -> None:
        nonlocal job_id, steps
        close_step()
        if job_id is not None:
            jobs.append(Job(job_id, tuple(steps)))
        job_id, steps = None, []

    for line in lines:
        if re.match(r"^jobs:\s*$", line):
            in_jobs = True
            continue
        if not in_jobs:
            continue
        if line and not line.startswith(" ") and not line.startswith("#"):
            close_job()
            in_jobs = False
            continue
        m = re.match(r"^  ([A-Za-z0-9_-]+):\s*$", line)
        if m:
            close_job()
            job_id, steps = m.group(1), []
            continue
        if job_id is None:
            continue
        start = re.match(r"^      - (?:name|uses|run):", line)
        if start:
            close_step()
            step_name = ""
        if step_name is None:
            continue
        named = re.match(r"^      - name:\s*(.+?)\s*$", line)
        if named:
            step_name = named.group(1)
        uses = re.match(r"^\s+(?:- )?uses:\s*([^\s#]+)", line)
        if uses:
            step_uses = uses.group(1)
        step_body.append(line)
    close_job()

    # A step written as `- uses:` with no name is identified by what it uses.
    fixed_jobs: list[Job] = []
    for job in jobs:
        fixed_steps = tuple(
            Step(s.name or (s.uses or "<unnamed>"), s.uses, s.body) for s in job.steps
        )
        fixed_jobs.append(Job(job.job_id, fixed_steps))

    return Workflow(
        path=path,
        triggers=tuple(triggers),
        jobs=tuple(fixed_jobs),
        text=text,
        stripped=strip_comments(text),
    )


def read_workflows(source: Source) -> dict[str, Workflow]:
    out: dict[str, Workflow] = {}
    for rel in source.workflows():
        text = source.read(rel)
        if text is None:
            continue
        out[rel] = parse_workflow(rel, text)
    return out


# ---------------------------------------------------------------------------
# findings
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Finding:
    code: str
    severity: str
    where: str
    detail: str
    #: The exact text being acknowledged, when there is one. Empty for findings
    #: that can never be acknowledged - a REMOVAL or a WEAKENING is refused
    #: whatever the ack file says, so binding one to content buys nothing.
    content: str = ""

    @property
    def fingerprint(self) -> str:
        """`CODE:where:hash-of-what-was-acknowledged`.

        THE HASH IS THE WHOLE POINT, ADDED 2026-08-11 BY OWNER DIRECTIVE.

        This used to be `f"{code}:{where}"` - a LOCATION and nothing else. An
        acknowledgement therefore said "somebody looked at the header of
        pr-fast.yml once", and went on saying it for ever. Two ack lines shipped
        for one authorised two-line change permanently pre-authorised EVERY
        future rewrite of the triggers, permissions, concurrency and env of the
        two most sensitive workflows in this repository.

        Measured: `permissions:\n  contents: read` replaced by
        `permissions: write-all` in pr-fast.yml, nothing else changed, no ack
        line added - and the checker returned PASS, consuming an ack that had
        been written for a completely different change.

        With the content hashed in, an ack expires the instant the thing it
        describes changes. It cannot be inherited by a later edit, because a
        later edit has a different hash and therefore a different fingerprint.
        """
        return f"{self.code}:{self.where}:{acknowledged_hash(self.content)}"

    def __str__(self) -> str:
        # The FINGERPRINT is printed for a CHANGE, and only for a CHANGE.
        #
        # Since 2026-08-11 it carries a hash of the acknowledged content, so
        # nobody can construct it by hand from the code and the path any more.
        # A person acknowledging a change has to copy this line, which is the
        # point: the string they paste is bound to the exact thing they read.
        #
        # Not printed for a REMOVAL or a WEAKENING. Those cannot be
        # acknowledged at all, and printing something that looks like an
        # acknowledgement beside them would invite somebody to try.
        line = f"[{self.severity}] {self.code}  {self.where}\n      {self.detail}"
        if self.severity == CHANGE:
            line += f"\n      acknowledge with: {self.fingerprint}"
        return line


def acknowledged_hash(content: str) -> str:
    """Twelve hex characters of SHA-256 over the acknowledged text.

    TWELVE, not sixty-four, and the reason is that a person has to be able to
    copy this line into `ci/workflow_changes.ack` by hand and read it back
    later. Twelve hex characters is 48 bits; this is not a defence against
    somebody CRAFTING a collision, and it is not asked to be. What it defends
    against is an ack being silently inherited by an unrelated later change,
    and any change at all moves it.

    Whitespace is collapsed first, so a reflow that changes nothing does not
    invalidate an acknowledgement and send somebody back to re-approve a change
    they already approved. An ack that expires for no reason trains people to
    re-add them without reading, which is the same hole with extra steps.
    """
    normalised = " ".join(content.split())
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()[:12]


class Report:
    def __init__(self) -> None:
        self.findings: list[Finding] = []
        self.warnings: list[str] = []
        self.checked: list[str] = []

    def add(
        self, code: str, severity: str, where: str, detail: str, content: str = ""
    ) -> None:
        self.findings.append(Finding(code, severity, where, detail, content))

    def warn(self, message: str) -> None:
        self.warnings.append(message)

    def note(self, description: str) -> None:
        self.checked.append(description)


# ---------------------------------------------------------------------------
# gate contract helpers
# ---------------------------------------------------------------------------


def load_gates(source: Source) -> dict[str, Any] | None:
    text = source.read(GATES)
    if text is None:
        return None
    return tomllib.loads(text)


def lock_names(source: Source) -> list[str]:
    text = source.read(GATE_LOCK)
    if text is None:
        return []
    return sorted(
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    )


def command_tokens(command: str) -> list[str]:
    """The parts of a gate command that must survive for it to be the same gate.

    `--flag=value` keeps `--flag`: the value legitimately differs between the
    contract and the workflow (`--compare-branch=origin/main` versus
    `--compare-branch="origin/${base}"`). Pure punctuation is dropped.
    """
    tokens: list[str] = []
    for raw in command.split():
        token = raw.split("=", 1)[0] if raw.startswith("-") else raw
        if PUNCTUATION_ONLY.match(token):
            continue
        tokens.append(token)
    return tokens


def gate_is_wired(command: str, workflows: dict[str, Workflow]) -> bool:
    """True when some single step actually executes this command.

    Step-level, not file-level: `bandit` appearing in a version print elsewhere
    in the file must not make the bandit gate look wired. Comment-stripped: a
    gate named only in a comment is a gate that does not run.
    """
    tokens = command_tokens(command)
    if not tokens:
        return False
    for workflow in workflows.values():
        for job in workflow.jobs:
            for step in job.steps:
                haystack = strip_comments(step.searchable)
                if all(
                    re.search(rf"(?<![\w-]){re.escape(t)}(?![\w-])", haystack)
                    for t in tokens
                ):
                    return True
    return False


def top_level_symbols(text: str) -> set[str]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return set()
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


def unconditional_skips(text: str) -> list[str]:
    """Module-level skip machinery, found with ast rather than substrings.

    A substring search would match the docstring that explains why the skip was
    removed, which is the kind of false positive that gets a check deleted.
    """
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    found: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "pytestmark" for t in node.targets
        ):
            found.append("pytestmark")
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        if node.attr not in ("skip", "skipif"):
            continue
        parent = node.value
        if (
            isinstance(parent, ast.Attribute)
            and parent.attr == "mark"
            and isinstance(parent.value, ast.Name)
            and parent.value.id == "pytest"
        ):
            found.append(f"pytest.mark.{node.attr}")
    return sorted(set(found))


def fail_closed_branches(text: str) -> int:
    """How many if/elif chains end in an `else`.

    ci/check_aggregate.py's catch-all is exactly that shape: `else:` after the
    known results, so an unrecognised value is a failure rather than a pass.
    Counting them means removing one is visible without hard-coding a line.
    """
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return 0
    total = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        chain = node
        while len(chain.orelse) == 1 and isinstance(chain.orelse[0], ast.If):
            chain = chain.orelse[0]
        if chain.orelse:
            total += 1
    return total


def preamble(workflow: Workflow) -> list[str]:
    """Everything above `jobs:`, comment-stripped and tokenised.

    `on:`, `permissions:`, `concurrency:` and `env:` all live here and none of
    them are inside a step, so the step-by-step comparison above cannot see
    them. A workflow-level `permissions: contents: write` would otherwise be
    invisible.
    """
    out: list[str] = []
    for line in workflow.stripped.splitlines():
        if re.match(r"^jobs:\s*$", line):
            break
        out.extend(line.split())
    return out


def write_scopes(workflow: Workflow) -> set[str]:
    """Every permission scope granted `write`, at workflow or job level."""
    return set(re.findall(r"^\s+([a-z-]+):\s*write\s*$", workflow.stripped, re.M))


def action_pins(workflow: Workflow) -> dict[str, str]:
    pins: dict[str, str] = {}
    for job in workflow.jobs:
        for step in job.steps:
            if not step.uses or step.uses.startswith(("./", "docker://")):
                continue
            action, _, pin = step.uses.partition("@")
            pins[action] = pin
    return pins


# ---------------------------------------------------------------------------
# the comparison
# ---------------------------------------------------------------------------


def compare(tree: Source, trusted: Source) -> Report:
    report = Report()

    theirs = read_workflows(trusted)
    ours = read_workflows(tree)

    _compare_workflows(report, ours, theirs)
    _compare_gate_contract(report, tree, trusted, ours)
    _compare_sources(report, tree, trusted)
    _check_protection_test(report, tree)
    _check_standalone_weakenings(report, ours)

    return report


def _compare_workflows(
    report: Report, ours: dict[str, Workflow], theirs: dict[str, Workflow]
) -> None:
    report.note("every workflow, job and step on main still exists here")

    for path, main_wf in theirs.items():
        ours_wf = ours.get(path)
        if ours_wf is None:
            report.add(
                "WORKFLOW_FILE_REMOVED",
                REMOVAL,
                path,
                "main has this workflow and this tree does not",
            )
            continue

        main_jobs = {j.job_id: j for j in main_wf.jobs}
        our_jobs = {j.job_id: j for j in ours_wf.jobs}

        for job_id, main_job in main_jobs.items():
            our_job = our_jobs.get(job_id)
            if our_job is None:
                report.add(
                    "JOB_REMOVED",
                    REMOVAL,
                    f"{path}:{job_id}",
                    "main defines this job and this tree does not. Renaming a job "
                    "removes it: add the new name alongside the old one.",
                )
                continue
            our_steps = {s.name: s for s in our_job.steps}
            main_steps = {s.name: s for s in main_job.steps}
            for step in main_job.steps:
                mine = our_steps.get(step.name)
                if mine is None:
                    report.add(
                        "STEP_REMOVED",
                        REMOVAL,
                        f"{path}:{job_id}:{step.name}",
                        "main runs this step and this tree does not. The gate "
                        "contract binds a gate to a JOB, so deleting a step is "
                        "invisible to it - this is the check that sees it.",
                    )
                    continue
                if (
                    strip_comments(mine.body).split()
                    != strip_comments(step.body).split()
                ):
                    report.add(
                        "STEP_RUN_CHANGED",
                        CHANGE,
                        f"{path}:{job_id}:{step.name}",
                        "the command this step runs is not the command main runs",
                        content=mine.body,
                    )
            for step_name in sorted(set(our_steps) - set(main_steps)):
                report.add(
                    "STEP_ADDED",
                    CHANGE,
                    f"{path}:{job_id}:{step_name}",
                    "a step main does not have. Adding is allowed; adding "
                    "silently is not.",
                    content=our_steps[step_name].body,
                )

        for job_id in sorted(set(our_jobs) - set(main_jobs)):
            report.add(
                "JOB_ADDED",
                CHANGE,
                f"{path}:{job_id}",
                "a job main does not have",
                # Every step this job runs. Acknowledging a job acknowledges
                # what it does, not merely that it appeared.
                content="\n".join(step.body for step in our_jobs[job_id].steps),
            )

        # everything above `jobs:` - on, permissions, concurrency, env
        if preamble(ours_wf) != preamble(main_wf):
            report.add(
                "WORKFLOW_HEADER_CHANGED",
                CHANGE,
                f"{path}:header",
                "the triggers, permissions, concurrency or workflow-level env "
                "differ from main. None of these sit inside a step, so nothing "
                "else in this check would see them.",
                # THE HEADER ITSELF. This is the finding the location-only
                # fingerprint made dangerous: one ack covered every future
                # rewrite of the triggers and permissions of this file.
                content=" ".join(preamble(ours_wf)),
            )
        for scope in sorted(write_scopes(ours_wf) - write_scopes(main_wf)):
            report.add(
                "PERMISSION_WIDENED",
                WEAKENING,
                f"{path}:permissions.{scope}",
                f"{scope}: write is granted here and not on main",
            )

        # triggers
        new_triggers = set(ours_wf.triggers) - set(main_wf.triggers)
        for trigger in sorted(new_triggers & set(UNSAFE_TRIGGERS)):
            report.add(
                "UNSAFE_TRIGGER",
                WEAKENING,
                f"{path}:on.{trigger}",
                f"{trigger} runs with the base repository's secrets and write "
                "token against pull-request code. main does not use it.",
            )

        # action pins
        main_pins = action_pins(main_wf)
        our_pins = action_pins(ours_wf)
        for action, pin in main_pins.items():
            mine = our_pins.get(action)
            if mine is None:
                continue
            if mine != pin:
                report.add(
                    "ACTION_SHA_CHANGED",
                    CHANGE,
                    f"{path}:{action}",
                    f"main pins {pin}, this tree pins {mine}",
                    # The SHA being moved TO. Acknowledging one bump must not
                    # acknowledge every later bump of the same action.
                    content=mine,
                )

        # flags present on main must still be present
        main_flags = set(FLAG.findall(main_wf.stripped))
        our_flags = set(FLAG.findall(ours_wf.stripped))
        for flag in sorted(main_flags - our_flags):
            report.add(
                "FLAG_REMOVED",
                REMOVAL,
                f"{path}:{flag}",
                f"{flag} is used on main and appears nowhere in this file. "
                "Dropping a flag is how a gate stops failing without being deleted.",
            )

        # continue-on-error
        main_coe = main_wf.stripped.count("continue-on-error")
        our_coe = ours_wf.stripped.count("continue-on-error")
        if our_coe > main_coe:
            report.add(
                "CONTINUE_ON_ERROR_ADDED",
                WEAKENING,
                path,
                f"main has {main_coe}, this tree has {our_coe}. A step that "
                "cannot fail is not a gate.",
            )

    for path in sorted(set(ours) - set(theirs)):
        report.add(
            "WORKFLOW_FILE_ADDED",
            CHANGE,
            path,
            "a workflow main does not have",
            # THE WHOLE FILE. A new workflow is acknowledged as it stands, not
            # as a name that may later hold anything at all - and a name-only
            # ack on this finding is the worst of the six, because the thing it
            # would wave through is an entire workflow nobody read.
            content=ours[path].text,
        )


def _compare_gate_contract(
    report: Report, tree: Source, trusted: Source, ours: dict[str, Workflow]
) -> None:
    report.note("no gate name in main's contract or lock file has disappeared")

    main_gates = load_gates(trusted)
    our_gates = load_gates(tree)

    if main_gates is None:
        report.warn(f"main has no {GATES}: nothing to compare the contract against")
        return
    if our_gates is None:
        report.add("GATES_FILE_REMOVED", REMOVAL, GATES, "the contract is gone")
        return

    main_names = {str(g["name"]) for g in main_gates["gate"]}
    our_names = {str(g["name"]) for g in our_gates["gate"]}
    for name in sorted(main_names - our_names):
        report.add(
            "GATE_REMOVED",
            REMOVAL,
            f"{GATES}:{name}",
            "main declares this gate and this tree does not. The number of "
            "gates may only go up.",
        )

    main_lock = set(lock_names(trusted))
    our_lock = set(lock_names(tree))
    for name in sorted(main_lock - our_lock):
        report.add(
            "LOCK_NAME_REMOVED",
            REMOVAL,
            f"{GATE_LOCK}:{name}",
            "main's lock file holds this name and this tree's does not. Editing "
            "the lock and the contract in one commit satisfies the in-tree test; "
            "it does not satisfy main.",
        )

    # The required check names come from MAIN's contract, so a branch cannot
    # rename the required job and then rename what it says is required.
    job_ids = {jid for wf in ours.values() for jid in wf.job_ids()}
    meta = main_gates["meta"]
    for key in ("required_pr_check", "required_mq_check"):
        wanted = str(meta[key])
        if wanted not in job_ids:
            report.add(
                "REQUIRED_CONTEXT_JOB_MISSING",
                REMOVAL,
                f"{GATES}:meta.{key}={wanted}",
                "GitHub's ruleset requires this context by exact string. No job "
                "in this tree publishes it, so the required check would never "
                "report and the branch would sit unmergeable - or worse, a "
                "different job would answer to the name.",
            )

    # A gate that main executes and this tree does not is a gate switched off.
    main_wfs = read_workflows(trusted)
    unwired_here = {
        str(g["name"])
        for g in our_gates["gate"]
        if not gate_is_wired(str(g["command"]), ours)
    }
    unwired_on_main = {
        str(g["name"])
        for g in main_gates["gate"]
        if not gate_is_wired(str(g["command"]), main_wfs)
    }
    for name in sorted(unwired_here - unwired_on_main):
        report.add(
            "GATE_UNWIRED",
            REMOVAL,
            f"{GATES}:{name}",
            "main runs this gate's command in a workflow step and this tree "
            "does not. Declared but never executed reads as covered.",
        )
    still_unwired = sorted(unwired_here & unwired_on_main)
    if still_unwired:
        report.warn(
            "DECLARED BUT NOT EXECUTED IN ANY WORKFLOW STEP (unchanged from main, "
            f"so not a regression - but not a gate either): {still_unwired}"
        )


def _compare_sources(report: Report, tree: Source, trusted: Source) -> None:
    report.note("no guard function or fail-closed branch has been deleted")

    for rel in PROTECTED_SOURCES:
        main_text = trusted.read(rel)
        our_text = tree.read(rel)
        if main_text is None:
            continue
        if our_text is None:
            report.add(
                "GUARD_FILE_REMOVED",
                REMOVAL,
                rel,
                "main has this guard and this tree does not",
            )
            continue
        exempt = STRENGTHENING_REMOVALS.get(rel, frozenset())
        gone = sorted(
            top_level_symbols(main_text) - top_level_symbols(our_text) - exempt
        )
        if gone:
            report.add(
                "GUARD_SYMBOL_REMOVED",
                REMOVAL,
                rel,
                f"defined on main, absent here: {gone}",
            )
        main_branches = fail_closed_branches(main_text)
        our_branches = fail_closed_branches(our_text)
        if our_branches < main_branches:
            report.add(
                "FAIL_CLOSED_BRANCH_REMOVED",
                REMOVAL,
                rel,
                f"main terminates {main_branches} if-chains in an else, this tree "
                f"terminates {our_branches}. The catch-all is what turns an "
                "unrecognised result into a failure instead of a pass.",
            )

    for anchor in ANCHORS:
        if trusted.read(anchor) is not None and tree.read(anchor) is None:
            report.add(
                "ANCHOR_MISSING",
                REMOVAL,
                anchor,
                "main invokes the integrity check from here and this tree does not",
            )


def _check_protection_test(report: Report, tree: Source) -> None:
    report.note("ci/test_protection.py cannot pass by never running")

    rel = "ci/test_protection.py"
    text = tree.read(rel)
    if text is None:
        return
    for where in unconditional_skips(text):
        report.add(
            "PROTECTION_TEST_SKIPPABLE",
            WEAKENING,
            f"{rel}:{where}",
            "a module-level skip makes the live protection test pass on every "
            "hosted run without calling GitHub once. That is exactly how it "
            "behaved before 2026-08-10.",
        )
    for sentinel in PROTECTION_SENTINELS:
        if sentinel not in text:
            report.add(
                "PROTECTION_TEST_SENTINEL_MISSING",
                REMOVAL,
                f"ci/test_protection.py:{sentinel}",
                "the CI-versus-local distinction has been removed, so an absent "
                "prerequisite in CI no longer fails",
            )


def _check_standalone_weakenings(report: Report, ours: dict[str, Workflow]) -> None:
    report.note("no unpinned action, and no secret reachable from pull-request code")

    for path, workflow in ours.items():
        for job in workflow.jobs:
            for step in job.steps:
                if not step.uses or step.uses.startswith(("./", "docker://")):
                    continue
                _, sep, pin = step.uses.partition("@")
                if not sep or not SHA_PIN.fullmatch(pin):
                    report.add(
                        "ACTION_UNPINNED",
                        WEAKENING,
                        f"{path}:{job.job_id}:{step.name}",
                        f"{step.uses!r} is not pinned to a 40-character commit SHA. "
                        "A tag can be moved under you.",
                    )
        if set(workflow.triggers) & set(PR_TRIGGERS):
            for match in sorted(
                set(re.findall(r"secrets\.[A-Za-z0-9_]+", workflow.stripped))
            ):
                report.add(
                    "SECRET_ON_PR_PATH",
                    WEAKENING,
                    f"{path}:{match}",
                    f"{match} is reachable from a workflow triggered by "
                    f"{sorted(set(workflow.triggers) & set(PR_TRIGGERS))}, so "
                    "pull-request code can reach it",
                )


# ---------------------------------------------------------------------------
# acknowledgements
# ---------------------------------------------------------------------------


def acknowledgements(tree: Source) -> set[str]:
    text = tree.read(ACK_FILE)
    if text is None:
        return set()
    return {
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    }


def verdict(report: Report, acked: set[str]) -> tuple[list[Finding], list[Finding]]:
    """Split findings into blocking and acknowledged."""
    blocking: list[Finding] = []
    allowed: list[Finding] = []
    for finding in report.findings:
        if finding.severity == CHANGE and finding.fingerprint in acked:
            allowed.append(finding)
        else:
            blocking.append(finding)
    return blocking, allowed


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------


def run(
    tree_root: Path, trusted: Source
) -> tuple[Report, list[Finding], list[Finding]]:
    tree = DirSource(tree_root, "working tree")
    report = compare(tree, trusted)
    blocking, allowed = verdict(report, acknowledgements(tree))
    return report, blocking, allowed


def in_ci() -> bool:
    return os.environ.get("GITHUB_ACTIONS") == "true" or os.environ.get("CI") == "true"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tree", type=Path, default=ROOT, help="the tree being graded")
    ap.add_argument("--trusted-ref", default=None, help="git ref holding the policy")
    ap.add_argument(
        "--trusted-tree",
        type=Path,
        default=None,
        help="a directory holding the policy, for testing this checker",
    )
    ap.add_argument("--json", type=Path, default=None, help="write the report here")
    args = ap.parse_args()

    trusted: Source | None
    if args.trusted_tree is not None:
        trusted = DirSource(args.trusted_tree, "trusted tree")
    else:
        trusted = trusted_source(args.tree, args.trusted_ref)

    if trusted is None:
        print(
            "LOCAL_ENVIRONMENT_UNAVAILABLE - no protected ref to compare against.\n"
            "Tried origin/main, refs/remotes/origin/main, main.\n"
            "  fix it with:  git fetch origin main",
            file=sys.stderr,
        )
        if in_ci():
            print(
                "In CI this is a failure, not a skip: without main there is no "
                "policy the pull request cannot edit, and this check would be "
                "grading the pull request against itself.",
                file=sys.stderr,
            )
            return EXIT_FINDINGS
        return EXIT_NO_TRUSTED_SOURCE

    report, blocking, allowed = run(args.tree, trusted)

    print(f"trusted policy read from: {trusted.label}\n")
    for description in report.checked:
        print(f"  checked  {description}")
    print()

    for finding in allowed:
        print(f"  acked    {finding.fingerprint}")
    for message in report.warnings:
        print(f"  WARNING  {message}")
    if allowed or report.warnings:
        print()

    if args.json is not None:
        args.json.write_text(
            json.dumps(
                {
                    "trusted_source": trusted.label,
                    "checked": report.checked,
                    "warnings": report.warnings,
                    "acknowledged": [f.fingerprint for f in allowed],
                    "blocking": [
                        {
                            "code": f.code,
                            "severity": f.severity,
                            "where": f.where,
                            "fingerprint": f.fingerprint,
                            "detail": f.detail,
                        }
                        for f in blocking
                    ],
                    "verdict": "PASS" if not blocking else "FAIL",
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    if not blocking:
        print("workflow integrity: PASS")
        return EXIT_OK

    print(f"workflow integrity: FAIL - {len(blocking)} finding(s)\n")
    for finding in blocking:
        print(f"  {finding}")
    print(
        "\nREMOVAL and WEAKENING findings cannot be acknowledged. CHANGE findings "
        f"are acknowledged by writing the fingerprint into {ACK_FILE} - which the "
        "pull request can also write, so that is a statement, not a permission."
    )
    return EXIT_FINDINGS


if __name__ == "__main__":
    sys.exit(main())
