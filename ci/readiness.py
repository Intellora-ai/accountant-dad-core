"""PHASE 5B — the operational readiness and repeatability gate.

NOT A FEATURE. A RELEASE GATE.
------------------------------
Owner ruling 2026-08-09, after a phase-number collision was raised: the
"3 clean runs, 30 voucher lifecycles, clean-room install, restart/recovery,
company isolation" work is NOT Phase 6. Phase 6 remains the first detector, as
`docs/ARCHITECTURE.md` §7 has always defined it. This work is Phase 5B.

    Phase 5   controlled Tally write/reversal proof, N = 10
    Phase 5B  operational readiness and repeatability gate   <- this file
    Phase 6   first detector, vendor_switch + dismissal logging

A feature milestone and a release gate are different things and are never
merged into one pass/fail claim. Phase 5B passing does not make Phase 6
complete; Phase 6 detector tests passing does not make Phase 5B pass.

THE QUESTION IT ANSWERS
-----------------------
Was Phase 5 a lucky one-off, or is this repeatable enough for a tightly
controlled pilot? One clean run answers neither. Three do, if they are three
DIFFERENT runs:

    Run A  the normal lifecycle, start to finish
    Run B  interruption — duplicate retry, a delayed response, a restart, then
           recovery and reconciliation
    Run C  partial failure — injected at voucher 4, persisted, reconciled,
           explicitly resumed, and only the outstanding cleanup finished

Three identical clean runs would prove the happy path three times. These prove
the happy path, the interrupted path and the broken path.

WHERE THE FAILURES ARE INJECTED, AND WHERE THEY ARE NOT
--------------------------------------------------------
`FakeTally` and the XML simulator only. A failure is NEVER manufactured in real
statutory books to test a failure path. That is not caution, it is the
difference between testing a system and damaging a customer.
"""

from __future__ import annotations

import datetime
import pathlib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

from accountant import reversal
from accountant.memory.store import MemoryStore
from accountant.reversal import BatchState, VoucherState
from accountant.schema import Voucher
from accountant.tallyio.client import TallyClient
from accountant.tallyio.fake import FakeTally
from ci import acceptance
from ci.acceptance import AcceptanceRun, N

#: Three runs, ten vouchers each. The lifecycle count the gate reports.
RUNS = 3
LIFECYCLES = RUNS * N

RUN_A = "A_normal"
RUN_B = "B_interrupted"
RUN_C = "C_partial_failure"


@dataclass(frozen=True)
class RunResult:
    """One of the three runs, and everything it is asked to prove.

    THE FOUR MEASUREMENTS HAVE NO DEFAULTS, ON PURPOSE
    --------------------------------------------------
    2026-08-10. `wrong_writes` and `duplicate_writes` used to be written
    straight into the gate as `Condition(name, 0, 0, ...)` — both operands
    literal, so "zero wrong writes" and "zero duplicate writes" reported a pass
    for every possible run, including one that made ten of each. Giving these
    fields a default of `0` would rebuild exactly that: a run that forgets to
    measure would report the answer the gate wants to hear.

    So a run has to state all four or it will not construct.
    """

    name: str
    passed: bool
    lifecycles: int
    detail: str
    #: Vouchers this run ever reported as a WRONG_MOVEMENT — Tally's answer and
    #: Tally's own books disagreeing about what moved.
    wrong_writes: int
    #: Vouchers a duplicate operation id actually created. Counted, not
    #: inferred from whether the connector raised: a connector that refused
    #: loudly and wrote anyway is the defect this exists to see.
    duplicate_writes: int
    #: Whether the trial balance came back to its exact prior value in paise.
    trial_balance_restored: bool
    #: The terminal `BatchState` of this run's cleanup batch. A DIFFERENT claim
    #: from the one above: a batch can finish and leave the books wrong, and it
    #: can leave the books right and never finish.
    cleanup_state: str
    conditions: tuple[acceptance.Condition, ...] = ()


@dataclass(frozen=True)
class ReadinessGate:
    """The whole gate. Twelve conditions, and no partial credit."""

    runs: tuple[RunResult, ...]
    conditions: tuple[acceptance.Condition, ...]
    notes: tuple[str, ...] = field(default_factory=tuple[str, ...])

    @property
    def lifecycles(self) -> int:
        return sum(r.lifecycles for r in self.runs)

    @property
    def passed(self) -> bool:
        return all(r.passed for r in self.runs) and all(
            c.passed for c in self.conditions
        )

    @property
    def verdict(self) -> str:
        return "PASSED" if self.passed else "NOT_PASSED"

    def failures(self) -> tuple[acceptance.Condition, ...]:
        return tuple(c for c in self.conditions if not c.passed)


COMPANY = "Demo Co"
ACCOUNTS = ("Purchases", "Cash")


def fresh_company(name: str = COMPANY, *, backed_up: bool = True) -> FakeTally:
    t = FakeTally()
    t.add_company(name, accounts=ACCOUNTS, backed_up=backed_up)
    return t


# ---- Run A — the normal lifecycle -------------------------------------------


def run_a(client: TallyClient | None = None, company: str = COMPANY) -> RunResult:
    """Post ten, verify each, reverse ten, verify each, books back to the paise.

    The four measurements are lifted from the acceptance run rather than
    recomputed here. `run_acceptance` already snapshots the trial balance either
    side and already counts what a duplicate retry created; restating those
    rules in a second place is how two numbers that must agree stop agreeing.
    """
    tally = client if client is not None else fresh_company(company)
    result = acceptance.run_acceptance(
        tally, company, run_id="phase5b-A", evidence_class=acceptance.FAKETALLY
    )
    restored = _condition_passed(result.conditions, "trial_balance_restored")
    return RunResult(
        name=RUN_A,
        passed=result.passed,
        lifecycles=len(result.operation_ids),
        detail=(
            f"{result.verdict}; batch {result.batch_state}; "
            f"trial balance restored: {restored}"
        ),
        wrong_writes=result.voucher_states.count(VoucherState.WRONG_MOVEMENT.value),
        duplicate_writes=_condition_count(
            result.conditions, "duplicate_created_nothing"
        ),
        trial_balance_restored=restored,
        cleanup_state=result.batch_state,
        conditions=result.conditions,
    )


# ---- Run B — interruption, restart, recovery --------------------------------


class DelaysThenDrops(FakeTally):
    """A Tally that answers slowly and then not at all.

    The delay is not simulated with a sleep — a test that waits is a test
    nobody runs. What is simulated is the OUTCOME of a delay: the request went,
    the answer never came back, and the caller cannot say whether the books
    moved. That is the only part of "slow" that changes behaviour.
    """

    drop_after = 0

    def __init__(self) -> None:
        super().__init__()
        self.reversals = 0

    def reverse_by_operation_id(self, company: str, operation_id: str) -> bool:
        self.reversals += 1
        if self.reversals > self.drop_after:
            # The delete DOES land. Only the answer is lost — the case that
            # produces an unknown outcome which reconciliation can settle.
            super().reverse_by_operation_id(company, operation_id)
            raise TimeoutError("the answer never came back")
        return super().reverse_by_operation_id(company, operation_id)


def run_b(store_path: str, company: str = COMPANY) -> RunResult:
    """Ten posted, one retried, the batch interrupted, the process 'restarted',
    the state read back from disk, reconciled and resumed.

    `store_path` is a real file rather than `:memory:` on purpose. The restart
    is the point: a second `MemoryStore` opened on the same path is what a new
    process sees, and if the durable rows were not really durable this is where
    that shows up.
    """
    tally = DelaysThenDrops()
    tally.add_company(company, accounts=ACCOUNTS, backed_up=True)
    DelaysThenDrops.drop_after = 4

    before = tally.trial_balance(company)
    ops = [post_one(tally, company, i, acceptance.DEFAULT_DATE) for i in range(N)]

    # the duplicate retry, mid-run
    duplicate_created = _retry_creates(tally, company, ops[0])

    store = MemoryStore(store_path)
    stopped = reversal.execute(
        reversal.confirm(reversal.preview(tally, company)),
        tally,
        log=store,
        company_key=company,
        run_id="phase5b-B",
    )

    # THE RESTART. A brand new store object on the same file, exactly what a
    # second process gets, and the rows have to still be there.
    reopened = MemoryStore(store_path)
    rows = [r for r in reopened.actions(company) if r.action == reversal.BATCH_ACTION]
    survived = len(rows)

    DelaysThenDrops.drop_after = 10_000  # the cause is corrected
    finished = reversal.resume(
        reversal.reconcile(stopped, tally),
        tally,
        approved=True,
        log=store,
        company_key=company,
        run_id="phase5b-B",
    )

    after = tally.trial_balance(company)
    restored = after == before
    ok = (
        stopped.state is BatchState.UNKNOWN_OUTCOME
        and finished.state is BatchState.COMPLETED
        and restored
        and duplicate_created == 0
        and survived > 0
    )
    return RunResult(
        name=RUN_B,
        passed=ok,
        lifecycles=len(ops),
        detail=(
            f"interrupted at {stopped.state.value}, {survived} durable rows "
            f"survived the restart, resumed to {finished.state.value}, "
            f"trial balance restored: {restored}"
        ),
        wrong_writes=_wrong_movements(stopped, finished),
        duplicate_writes=duplicate_created,
        trial_balance_restored=restored,
        cleanup_state=finished.state.value,
    )


# ---- Run C — partial failure, reconcile, explicit resume --------------------


class RefusesOne(FakeTally):
    """Tally explicitly refuses exactly one reversal. Injected, never real."""

    target = ""

    def reverse_by_operation_id(self, company: str, operation_id: str) -> bool:
        if operation_id == self.target:
            return False
        return super().reverse_by_operation_id(company, operation_id)


def run_c(company: str = COMPANY) -> RunResult:
    """Voucher 4 of 10 refuses. 1-3 stay reversed, 5-10 are never attempted,
    and the resume finishes only what is outstanding."""
    tally = RefusesOne()
    tally.add_company(company, accounts=ACCOUNTS, backed_up=True)

    before = tally.trial_balance(company)
    ops = [post_one(tally, company, i, acceptance.DEFAULT_DATE) for i in range(N)]

    # The same duplicate-retry probe runs A and B carry. Run C had no retry at
    # all, which meant its "zero duplicate writes" was an assumption rather
    # than a reading. It runs BEFORE the refusal is armed, so the injected
    # partial failure is still the only thing this run is testing.
    duplicate_created = _retry_creates(tally, company, ops[0])

    RefusesOne.target = ops[3]

    stopped = reversal.execute(
        reversal.confirm(reversal.preview(tally, company)), tally, company_key=company
    )
    partial_shape = (
        [o.state for o in stopped.outcomes[:3]] == [VoucherState.REVERSED_VERIFIED] * 3
        and stopped.outcomes[3].state is VoucherState.EXPLICIT_REJECTION
        and [o.state for o in stopped.outcomes[4:]]
        == [VoucherState.NOT_ATTEMPTED] * (N - 4)
    )

    RefusesOne.target = ""  # the cause is corrected
    finished = reversal.resume(
        reversal.reconcile(stopped, tally), tally, approved=True, company_key=company
    )

    after = tally.trial_balance(company)
    restored = after == before
    ok = (
        stopped.state is BatchState.PARTIAL_FAILURE
        and partial_shape
        and finished.state is BatchState.COMPLETED
        and restored
        and duplicate_created == 0
    )
    return RunResult(
        name=RUN_C,
        passed=ok,
        lifecycles=len(ops),
        detail=(
            f"stopped at {stopped.state.value} with the expected partial shape: "
            f"{partial_shape}; resumed to {finished.state.value}; "
            f"trial balance restored: {restored}"
        ),
        wrong_writes=_wrong_movements(stopped, finished),
        duplicate_writes=duplicate_created,
        trial_balance_restored=restored,
        cleanup_state=finished.state.value,
    )


# ---- company isolation -------------------------------------------------------


def company_isolation() -> tuple[bool, str]:
    """Two companies on one connector. Neither may see or move the other.

    Not a re-test of `accountant/memory/`'s isolation, which is about the index.
    This is about the CONNECTOR and the batch: `list_our_vouchers` is
    company-scoped, `preview` freezes one company's candidates, and a batch run
    against A must leave B's books untouched to the paise.
    """
    tally = FakeTally()
    tally.add_company("Alpha Traders", accounts=ACCOUNTS, backed_up=True)
    tally.add_company("Beta Supplies", accounts=ACCOUNTS, backed_up=True)

    for i in range(3):
        post_one(tally, "Alpha Traders", i, acceptance.DEFAULT_DATE)
        post_one(tally, "Beta Supplies", i + 50, acceptance.DEFAULT_DATE)

    beta_before = tally.trial_balance("Beta Supplies")
    beta_ops = {
        reversal.operation_id_of(v.narration)
        for v in tally.list_our_vouchers("Beta Supplies")
    }

    batch = reversal.preview(tally, "Alpha Traders")
    alpha_ops = {o.operation_id for o in batch.outcomes}
    reversal.execute(reversal.confirm(batch), tally, company_key="Alpha Traders")

    overlap = alpha_ops & beta_ops
    beta_after = tally.trial_balance("Beta Supplies")
    ok = (
        not overlap
        and beta_after == beta_before
        and len(tally.list_our_vouchers("Beta Supplies")) == 3
    )
    return ok, (
        f"alpha selected {len(alpha_ops)}, beta held {len(beta_ops)}, "
        f"overlap {len(overlap)}, beta trial balance unchanged: "
        f"{beta_after == beta_before}"
    )


# ---- the clean room ------------------------------------------------------------


def clean_room_install() -> tuple[bool, str]:
    """Build a wheel, install it into an empty virtualenv, and run it elsewhere.

    The question this answers is not "do the tests pass", it is "does the thing
    we would hand somebody actually work when it is not sitting in this
    repository". Those come apart in ordinary ways: a package left out of the
    wheel, an import that only resolves because the repo root is on `sys.path`,
    an entry point that exists as a file and not as a module.

    `--no-index --no-deps` because runtime dependencies are `[]`. If that ever
    stops being true this step fails, which is the correct alarm: a runtime
    dependency is a design change and not a packaging detail.

    The last check runs the reversal command from a directory that is not the
    repo and expects it to REFUSE — there is no Tally there. A command that
    "worked" in a clean room with no Tally would be a command that does not
    check.
    """
    import shutil
    import subprocess  # nosec B404 - fixed argv, no shell, no user input
    import sys
    import tempfile

    repo = pathlib.Path(__file__).resolve().parent.parent
    with tempfile.TemporaryDirectory() as tmp:
        out = pathlib.Path(tmp)
        wheels = out / "dist"
        venv = out / "venv"
        elsewhere = out / "elsewhere"
        elsewhere.mkdir()

        def run(
            argv: Sequence[str], cwd: pathlib.Path
        ) -> subprocess.CompletedProcess[str]:
            return subprocess.run(  # noqa: S603  # nosec B603 - fixed argv, shell=False
                list(argv), cwd=cwd, capture_output=True, text=True, check=False
            )

        built = run(
            [sys.executable, "-m", "build", "--wheel", "--outdir", str(wheels)], repo
        )
        if built.returncode != 0:
            return False, f"the wheel would not build: {built.stderr[-400:]}"

        made = sorted(wheels.glob("*.whl"))
        if not made:
            return False, "the build produced no wheel"

        created = run([sys.executable, "-m", "venv", str(venv)], out)
        if created.returncode != 0:
            return False, f"no virtualenv: {created.stderr[-400:]}"

        python = venv / "bin" / "python"
        if not python.exists():  # pragma: no cover - Windows layout
            python = venv / "Scripts" / "python.exe"

        installed = run(
            [
                str(python),
                "-m",
                "pip",
                "install",
                "--no-index",
                "--no-deps",
                str(made[0]),
            ],
            out,
        )
        if installed.returncode != 0:
            return False, f"the wheel would not install: {installed.stderr[-400:]}"

        imported = run(
            [
                str(python),
                "-c",
                "import accountant.pipeline, accountant.reversal, "
                "accountant.tallyio.factory, accountant.web.app; print('imports ok')",
            ],
            elsewhere,
        )
        if imported.returncode != 0:
            return (
                False,
                f"it does not import outside the repo: {imported.stderr[-400:]}",
            )

        refused = run(
            [
                str(python),
                "-m",
                "accountant.tallyio",
                "--reverse-all",
                "--company",
                "Nothing Here",
            ],
            elsewhere,
        )
        if refused.returncode == 0:
            return (
                False,
                "the reversal command did not refuse a Tally that is not there",
            )

        shutil.rmtree(wheels, ignore_errors=True)
        return True, (
            f"built {made[0].name}, installed into an empty venv, imported from "
            f"{elsewhere.name}, and the reversal command refused with exit "
            f"{refused.returncode}"
        )


# ---- helpers ------------------------------------------------------------------


def post_one(client: TallyClient, company: str, n: int, when: datetime.date) -> str:
    from accountant.tallyio.client import new_operation_id

    op = new_operation_id()
    client.write_voucher(company, acceptance.controlled_voucher(n, when), op)
    return op


def _retry_creates(client: TallyClient, company: str, operation_id: str) -> int:
    """How many vouchers a duplicate retry created. Must be zero."""
    before = len(client.list_our_vouchers(company))
    try:
        client.write_voucher(
            company,
            acceptance.controlled_voucher(0, acceptance.DEFAULT_DATE),
            operation_id,
        )
    except Exception as exc:
        # Any refusal at all is the right answer here, and WHICH refusal is not
        # what this measures. The number of vouchers is: a connector that
        # refused loudly and created one anyway would be the actual defect, and
        # only the count can see it.
        del exc
    return len(client.list_our_vouchers(company)) - before


# ---- the gate ------------------------------------------------------------------


def run_gate(
    store_path: str,
    *,
    clean_room: Callable[[], tuple[bool, str]] | None = None,
) -> ReadinessGate:
    """Three runs, isolation, and — if a checker is supplied — the clean room.

    `clean_room` is injected rather than called directly because it builds a
    wheel and a throwaway virtualenv, which is several seconds and does not
    belong inside a function the fast tests call. The gate REPORTS it as
    NOT_RUN when it is absent rather than quietly passing, so a run without it
    cannot be mistaken for a complete one.
    """
    results = (run_a(), run_b(store_path), run_c())
    isolated, isolation_detail = company_isolation()

    clean_ok, clean_detail = (False, "NOT_RUN")
    if clean_room is not None:
        clean_ok, clean_detail = clean_room()

    lifecycles = sum(r.lifecycles for r in results)
    conditions = (
        acceptance.Condition(
            "runs_passed", sum(1 for r in results if r.passed), RUNS, "3 of 3"
        ),
        acceptance.Condition(
            "voucher_lifecycles",
            lifecycles,
            LIFECYCLES,
            f"{LIFECYCLES} of {LIFECYCLES}",
        ),
        acceptance.Condition(
            "wrong_writes", _wrong_writes(results), 0, "zero wrong writes"
        ),
        acceptance.Condition(
            "duplicate_writes", _duplicate_writes(results), 0, "zero duplicate writes"
        ),
        acceptance.Condition(
            "cross_company_writes", isolated, True, "zero cross-company writes"
        ),
        acceptance.Condition(
            "unresolved_unknowns", _unresolved(results), 0, "zero left unresolved"
        ),
        acceptance.Condition(
            "trial_balance_mismatches", _mismatches(results), 0, "zero mismatches"
        ),
        acceptance.Condition(
            "cleanup_mismatches",
            _cleanup_mismatches(results),
            0,
            "zero cleanup mismatches",
        ),
        acceptance.Condition(
            "clean_room_install", clean_ok, True, "installs and runs outside the repo"
        ),
        acceptance.Condition(
            "restart_recovery", results[1].passed, True, "state survives a restart"
        ),
        acceptance.Condition(
            "evidence_bundles_complete",
            _bundles_complete(results),
            True,
            "every run reports",
        ),
        acceptance.Condition(
            "operator_readable",
            all(r.detail.strip() for r in results),
            True,
            "each run says what happened without raw logs",
        ),
    )
    return ReadinessGate(
        runs=results,
        conditions=conditions,
        notes=(isolation_detail, f"clean room: {clean_detail}"),
    )


def _unresolved(results: Sequence[RunResult]) -> int:
    return sum(1 for r in results if not r.passed)


def _wrong_writes(results: Sequence[RunResult]) -> int:
    return sum(r.wrong_writes for r in results)


def _duplicate_writes(results: Sequence[RunResult]) -> int:
    return sum(r.duplicate_writes for r in results)


def _mismatches(results: Sequence[RunResult]) -> int:
    """Runs whose trial balance did not come back to its exact prior value.

    This used to read the substring "restored: False" out of `r.detail`. Run A
    never emitted that clause, so a run A that left the books wrong could not
    be counted by anything and the condition reported zero mismatches for a
    broken run. It now reads the measurement, and `render`/`detail` print that
    same measurement, so the line a person reads and the number the gate counts
    cannot drift apart.
    """
    return sum(1 for r in results if not r.trial_balance_restored)


def _cleanup_mismatches(results: Sequence[RunResult]) -> int:
    """Runs whose cleanup batch did not reach COMPLETED.

    A DIFFERENT question from `_mismatches`, which is why it is a different
    function. Both conditions used to call `_mismatches`, so "zero trial-balance
    mismatches" and "zero cleanup mismatches" were one measurement wearing two
    names — and the two come apart in both directions:

        finished, books wrong   every voucher reported reversed and the trial
                                balance did not return. The CRITICAL_FAILURE
                                shape a concurrent write leaves.
        books right, unfinished a batch that stopped before it touched anything
                                has moved nothing, so the books still match.
    """
    return sum(1 for r in results if r.cleanup_state != BatchState.COMPLETED.value)


def _wrong_movements(*batches: reversal.Batch) -> int:
    """Vouchers this run EVER reported as a wrong movement.

    Across every batch object the run produced, not just the last one. A
    WRONG_MOVEMENT is what makes a batch CRITICAL_FAILURE, and a critical batch
    cannot be resumed at all, so looking only at the final object is looking
    only at the runs where it did not happen.
    """
    return len(
        {
            o.operation_id
            for b in batches
            for o in b.outcomes
            if o.state is VoucherState.WRONG_MOVEMENT
        }
    )


def _condition(
    conditions: Sequence[acceptance.Condition], name: str
) -> acceptance.Condition:
    """The named condition, or a refusal.

    Never a default. A missing condition means the acceptance harness stopped
    reporting something this gate counts, and answering `0` to that is how the
    gate starts passing on measurements nobody is taking any more.
    """
    for c in conditions:
        if c.name == name:
            return c
    raise KeyError(
        f"the acceptance run reported no condition named {name!r}; the readiness "
        f"gate counts it and will not substitute a value for it. Present: "
        f"{[c.name for c in conditions]}"
    )


def _condition_passed(conditions: Sequence[acceptance.Condition], name: str) -> bool:
    return _condition(conditions, name).passed


def _condition_count(conditions: Sequence[acceptance.Condition], name: str) -> int:
    actual = _condition(conditions, name).actual
    if not isinstance(actual, int) or isinstance(actual, bool):
        raise TypeError(
            f"condition {name!r} reports {actual!r}, which is not a count; the "
            "readiness gate would be adding up something that is not a number"
        )
    return actual


def _bundles_complete(results: Sequence[RunResult]) -> bool:
    return all(r.lifecycles == N for r in results)


def render(gate: ReadinessGate) -> str:
    lines = ["PHASE 5B — operational readiness and repeatability", ""]
    for r in gate.runs:
        mark = "ok  " if r.passed else "FAIL"
        lines.append(f"  {mark} run {r.name}: {r.detail}")
    lines.append("")
    for c in gate.conditions:
        mark = "ok  " if c.passed else "FAIL"
        lines.append(f"  {mark} {c.name}: actual {c.actual!r}, expected {c.expected!r}")
    lines.append("")
    lines.extend(f"  note: {n}" for n in gate.notes)
    lines.append("")
    lines.append(f"  {gate.lifecycles} of {LIFECYCLES} voucher lifecycles")
    lines.append(f"  VERDICT: {gate.verdict}")
    return "\n".join(lines)


def bundle(gate: ReadinessGate) -> dict[str, object]:
    return {
        "phase": "5B",
        "runs": [
            {
                "name": r.name,
                "passed": r.passed,
                "lifecycles": r.lifecycles,
                "detail": r.detail,
                "wrong_writes": r.wrong_writes,
                "duplicate_writes": r.duplicate_writes,
                "trial_balance_restored": r.trial_balance_restored,
                "cleanup_state": r.cleanup_state,
            }
            for r in gate.runs
        ],
        "lifecycles": gate.lifecycles,
        "expected_lifecycles": LIFECYCLES,
        "conditions": [c.as_row() for c in gate.conditions],
        "notes": list(gate.notes),
        "verdict": gate.verdict,
    }


__all__ = [
    "LIFECYCLES",
    "RUNS",
    "AcceptanceRun",
    "Mapping",
    "ReadinessGate",
    "RunResult",
    "Voucher",
    "bundle",
    "clean_room_install",
    "company_isolation",
    "post_one",
    "render",
    "run_a",
    "run_b",
    "run_c",
    "run_gate",
]
