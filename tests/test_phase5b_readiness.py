"""PHASE 5B — the operational readiness gate, and the three defects it found.

WHAT THE GATE FOUND ON ITS FIRST RUN
------------------------------------
All three were real, and all three had passed every existing test:

  1. the batch wrote its durable rows under the DISPLAY name while
     `MemoryStore.actions` reads by the NORMALISED key. Ten rows written, zero
     found. Every unit test had used `:memory:` and read back through the same
     unnormalised string, so the mismatch was invisible until a run reopened
     the file the way a second process would.

  2. a reconciled voucher was counted as accounted-for movement. Reconciliation
     proves the voucher is gone; it cannot prove by how much the books moved,
     because nobody snapshotted either side. The batch was asserting a
     conservation law it had not measured, and a legitimate recovery came out
     as CRITICAL_FAILURE.

  3. an explicitly rejected voucher was not retryable by resume. The owner's
     policy says "do not blindly retry; require explicit resume" — which means
     an explicit resume MUST be able to retry it once the local cause is
     corrected. It could not, so a recoverable partial failure was permanent.

None of the three is exotic. Each is what happens when a property is only ever
exercised the one way the tests happen to exercise it.

AND WHAT A SECOND AUDIT FOUND IN THE GATE ITSELF, 2026-08-10
------------------------------------------------------------
Three more, numbered G1-G3 and kept separate from the three above because they
are a different kind of thing: those are defects the gate FOUND, these are
defects in the gate's own conditions. Four of its twelve conditions reported a
pass without measuring anything. See the "the gate must MEASURE, not assert"
section below.

The lesson is the same one, one level up: a gate is a measuring instrument, and
an instrument nobody has tried to make read wrong has not been calibrated.

WHAT THIS FILE DOES NOT PROVE
-----------------------------
Anything about a licensed TallyPrime. Every failure here is injected into
`FakeTally`; a failure is never manufactured in real statutory books.
Evidence class: FAKETALLY.
"""

from __future__ import annotations

import pathlib
from collections.abc import Callable
from dataclasses import replace

import pytest

from accountant import reversal
from accountant.memory.store import MemoryStore
from accountant.reversal import BatchState, VoucherState
from accountant.schema import Voucher
from accountant.tallyio.client import DuplicateOperation, WriteResult
from accountant.tallyio.fake import FakeTally
from ci import acceptance, readiness

# ---- the three runs, each on its own ----------------------------------------


def test_run_a_is_the_whole_normal_lifecycle():
    result = readiness.run_a()

    assert result.passed, result.detail
    assert result.lifecycles == acceptance.N
    assert "PASSED" in result.detail


def test_run_b_survives_an_interruption_a_restart_and_a_reconciliation(
    tmp_path: pathlib.Path,
):
    result = readiness.run_b(str(tmp_path / "store.sqlite"))

    assert result.passed, result.detail
    assert result.lifecycles == acceptance.N
    assert "durable rows survived the restart" in result.detail
    assert "resumed to completed" in result.detail
    assert "trial balance restored: True" in result.detail


def test_run_c_stops_at_voucher_four_and_resumes_only_the_outstanding_work():
    result = readiness.run_c()

    assert result.passed, result.detail
    assert result.lifecycles == acceptance.N
    assert "expected partial shape: True" in result.detail
    assert "resumed to completed" in result.detail


def test_the_three_runs_exercise_three_different_paths(tmp_path: pathlib.Path):
    """Three identical clean runs would prove the happy path three times.

    So the check is not that three runs happened, it is that they went through
    different machinery: A never reconciles, B ends in an unknown outcome and a
    restart, C ends in a partial failure and an explicit resume.
    """
    a = readiness.run_a()
    b = readiness.run_b(str(tmp_path / "store.sqlite"))
    c = readiness.run_c()

    assert len({a.name, b.name, c.name}) == 3
    assert "restart" in b.detail and "restart" not in a.detail
    assert "partial" in c.detail and "partial" not in a.detail
    assert BatchState.UNKNOWN_OUTCOME.value in b.detail
    assert BatchState.PARTIAL_FAILURE.value in c.detail


# ---- defect 1: the durable rows have to be findable -------------------------


def test_batch_rows_are_written_under_the_key_the_store_reads_by(
    tmp_path: pathlib.Path,
):
    """The mismatch that made ten durable rows unreadable.

    The caller passes a display name; `MemoryStore.actions` normalises before
    reading. The batch normalises before writing, so the two cannot drift —
    and passing an ALREADY normalised key (which the web app does) still lands
    in the same place, because normalisation is idempotent.
    """
    path = str(tmp_path / "store.sqlite")
    tally = readiness.fresh_company("Demo Co")
    for i in range(2):
        readiness.post_one(tally, "Demo Co", i, acceptance.DEFAULT_DATE)

    store = MemoryStore(path)
    reversal.execute(
        reversal.confirm(reversal.preview(tally, "Demo Co")),
        tally,
        log=store,
        company_key="Demo Co",  # the DISPLAY name, as a CLI would pass it
        run_id="r",
    )

    reopened = MemoryStore(path)
    rows = [r for r in reopened.actions("Demo Co") if r.action == reversal.BATCH_ACTION]
    assert len(rows) == 4, "two rows per voucher, and all of them readable"
    assert all(r.company_key == "demo_co" for r in rows)

    # And the same rows are found when the caller normalised first.
    assert len(reopened.actions("demo_co")) == len(reopened.actions("Demo Co"))


# ---- defect 2: a reconciled movement is not a measured one ------------------


def test_a_reconciled_batch_declines_to_claim_conservation():
    """It cannot honestly claim it either way, so it says UNKNOWN.

    Before the fix this returned False and escalated a clean recovery to
    CRITICAL_FAILURE — the books DID move, correctly, between two snapshots
    nobody took.
    """
    tally = readiness.DelaysThenDrops()
    tally.add_company("Demo Co", accounts=readiness.ACCOUNTS, backed_up=True)
    readiness.DelaysThenDrops.drop_after = 1
    for i in range(3):
        readiness.post_one(tally, "Demo Co", i, acceptance.DEFAULT_DATE)

    stopped = reversal.execute(
        reversal.confirm(reversal.preview(tally, "Demo Co")),
        tally,
        company_key="demo_co",
    )
    assert stopped.state is BatchState.UNKNOWN_OUTCOME

    reconciled = reversal.reconcile(stopped, tally)

    settled = reconciled.outcomes[1]
    assert settled.state is VoucherState.REVERSED_VERIFIED
    assert settled.measured is False, "a read proves absence, not an amount"
    assert reconciled.accounted is None, "UNKNOWN, not a claim in either direction"
    assert reconciled.state is not BatchState.CRITICAL_FAILURE


def test_a_fully_measured_batch_still_claims_conservation():
    """The control. If `accounted` went UNKNOWN everywhere, the check would be
    gone rather than honest."""
    tally = readiness.fresh_company("Demo Co")
    for i in range(3):
        readiness.post_one(tally, "Demo Co", i, acceptance.DEFAULT_DATE)

    done = reversal.execute(
        reversal.confirm(reversal.preview(tally, "Demo Co")),
        tally,
        company_key="demo_co",
    )

    assert done.state is BatchState.COMPLETED
    assert all(o.measured for o in done.outcomes)
    assert done.accounted is True


# ---- defect 3: an explicit resume may retry a refusal -----------------------


@pytest.mark.parametrize(
    "state",
    [
        VoucherState.NOT_ATTEMPTED,
        VoucherState.PRECHECK_REFUSED,
        VoucherState.EXPLICIT_REJECTION,
    ],
)
def test_these_three_states_are_retryable_by_an_explicit_resume(
    state: VoucherState,
):
    """Owner policy: "do not blindly retry; require explicit resume". A resume
    that cannot retry the refusal makes a recoverable failure permanent."""
    assert state in reversal.RETRYABLE


@pytest.mark.parametrize(
    "state",
    [
        VoucherState.UNKNOWN_OUTCOME,
        VoucherState.WRONG_MOVEMENT,
        VoucherState.READBACK_FAILED,
        VoucherState.REVERSED_VERIFIED,
        VoucherState.REQUEST_SENT,
    ],
)
def test_these_states_are_never_retried_automatically(state: VoucherState):
    """Unknown must be reconciled first, wrong movement is critical, a failed
    read-back needs a person, and a verified reversal is done."""
    assert state not in reversal.RETRYABLE


def test_an_explicit_rejection_is_recoverable_once_the_cause_is_corrected():
    tally = readiness.RefusesOne()
    tally.add_company("Demo Co", accounts=readiness.ACCOUNTS, backed_up=True)
    before = tally.trial_balance("Demo Co")
    ops = [
        readiness.post_one(tally, "Demo Co", i, acceptance.DEFAULT_DATE)
        for i in range(5)
    ]
    readiness.RefusesOne.target = ops[2]

    stopped = reversal.execute(
        reversal.confirm(reversal.preview(tally, "Demo Co")),
        tally,
        company_key="demo_co",
    )
    assert stopped.state is BatchState.PARTIAL_FAILURE
    assert stopped.outcomes[2].state is VoucherState.EXPLICIT_REJECTION

    readiness.RefusesOne.target = ""
    finished = reversal.resume(
        reversal.reconcile(stopped, tally), tally, approved=True, company_key="demo_co"
    )

    assert finished.state is BatchState.COMPLETED
    assert tally.trial_balance("Demo Co") == before


# ---- company isolation -------------------------------------------------------


def test_a_batch_against_one_company_leaves_the_other_untouched():
    ok, detail = readiness.company_isolation()
    assert ok, detail
    assert "overlap 0" in detail


# ---- the gate must MEASURE, not assert -------------------------------------
#
# A SECOND set of three defects, found by audit on 2026-08-10 and confirmed by
# running the gate. Numbered G1-G3, NOT 1-3: the three above are defects in the
# reversal engine that the gate FOUND, and these are defects in the gate's own
# conditions. Reusing the numbers would merge two different things into one
# label, which is the same mistake the phase map has a test against.
#
# All three let a condition report a pass without looking at anything:
#
#   G1. `wrong_writes` and `duplicate_writes` were `Condition(name, 0, 0, ...)`.
#       Both operands literal. actual == expected for every possible run,
#       including one that made ten of each.
#   G2. `trial_balance_mismatches` and `cleanup_mismatches` were two names over
#       ONE call to `_mismatches`. Two different claims, one measurement.
#   G3. `_mismatches` read whether a run restored the books out of the run's
#       detail line. Run A's detail never said, so a run A that left the books
#       wrong could not be counted by anything.
#
# The injected failures below are `FakeTally` subclasses. Evidence class
# FAKETALLY. A failure is never manufactured in real statutory books.


class MovesTheBooksAndDeniesIt(FakeTally):
    """Reports a failed reversal while the voucher is actually deleted.

    The one outcome `pipeline.reverse_operation` calls WRONG_MOVEMENT: Tally's
    answer and Tally's own books disagree. It is the shape a real wrong write
    leaves, and it is the shape `wrong_writes` claims to count.
    """

    def reverse_by_operation_id(self, company: str, operation_id: str) -> bool:
        super().reverse_by_operation_id(company, operation_id)
        return False


class AcceptsADuplicate(FakeTally):
    """A connector that refuses a repeated operation id LOUDLY and writes it
    anyway.

    Deliberately the nastiest version. A connector that accepted the duplicate
    silently would be caught by the exception check; this one raises exactly
    what the caller expects and still leaves a second voucher behind, so only a
    count of vouchers can see it. That is what `duplicate_writes` is for.
    """

    def write_voucher(
        self, company: str, voucher: Voucher, operation_id: str
    ) -> WriteResult:
        try:
            return super().write_voucher(company, voucher, operation_id)
        except DuplicateOperation:
            co = self._co(company)
            co.vouchers.append(replace(co.vouchers[-1], id=f"{voucher.id}-twin"))
            raise


#: The last of the ten controlled vouchers, read off the rule that makes them
#: rather than restated as a number here.
LAST_AMOUNT = acceptance.controlled_voucher(
    acceptance.N - 1, acceptance.DEFAULT_DATE
).amount_paise


class LiesAboutTheLastVoucher(FakeTally):
    """Deletes voucher 9 and reports the delete failed.

    Voucher 9 on purpose. Runs B and C both stop partway and finish the rest
    under an explicit resume, so the last voucher is always reversed DURING the
    resume. A wrong movement inside `execute` makes the batch CRITICAL_FAILURE
    before the resume, and `reversal.resume` refuses a critical batch outright
    — the run would abort with a ValueError and report no measurement at all,
    which is why the injection has to land where it does.
    """

    def reverse_by_operation_id(self, company: str, operation_id: str) -> bool:
        found = self.read_by_operation_id(company, operation_id)
        lying = found is not None and found.amount_paise == LAST_AMOUNT
        moved = super().reverse_by_operation_id(company, operation_id)
        return False if lying else moved


class RefusesTheLastVoucherForever(FakeTally):
    """Refuses voucher 9 cleanly, and keeps refusing after the resume.

    A clean refusal: nothing moves, so the batch is PARTIAL_FAILURE and the
    trial balance is genuinely short by one voucher. The mirror image of
    `LiesAboutTheLastVoucher`, and between them they produce both ways the two
    mismatch counts come apart.
    """

    def reverse_by_operation_id(self, company: str, operation_id: str) -> bool:
        found = self.read_by_operation_id(company, operation_id)
        if found is not None and found.amount_paise == LAST_AMOUNT:
            return False
        return super().reverse_by_operation_id(company, operation_id)


class RefusesOneAndLies(readiness.RefusesOne, LiesAboutTheLastVoucher):
    """Run C's injected refusal, plus one wrong movement during the resume."""


class RefusesOneAndThenTheLast(readiness.RefusesOne, RefusesTheLastVoucherForever):
    """Run C's injected refusal, plus a voucher that is never reversed."""


class DropsAndRefusesTheLast(readiness.DelaysThenDrops, RefusesTheLastVoucherForever):
    """Run B's dropped answer, plus a voucher that is never reversed."""


class RefusesOneAndDuplicates(readiness.RefusesOne, AcceptsADuplicate):
    """Run C's injected refusal, plus a duplicate retry that lands."""


class DropsAndLies(readiness.DelaysThenDrops, LiesAboutTheLastVoucher):
    """Run B's dropped answer, plus one wrong movement during the resume."""


class DropsAndDuplicates(readiness.DelaysThenDrops, AcceptsADuplicate):
    """Run B's dropped answer, plus a duplicate retry that lands."""


def _company_on(client: FakeTally, company: str = readiness.COMPANY) -> FakeTally:
    client.add_company(company, accounts=readiness.ACCOUNTS, backed_up=True)
    return client


def _gate_with_run_a(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    make: Callable[[], readiness.RunResult],
) -> readiness.ReadinessGate:
    """Run the whole gate with run A replaced by one specific run A."""
    monkeypatch.setattr(readiness, "run_a", make)
    return readiness.run_gate(str(tmp_path / "store.sqlite"))


def _condition(gate: readiness.ReadinessGate, name: str) -> acceptance.Condition:
    return next(c for c in gate.conditions if c.name == name)


# ---- G1: the two conditions that could not fail -----------------------------


def test_run_a_counts_the_wrong_writes_it_actually_saw():
    """One voucher whose books and answer disagree, and the run says so."""
    broken = readiness.run_a(client=_company_on(MovesTheBooksAndDeniesIt()))

    assert broken.wrong_writes == 1, broken.detail
    assert broken.cleanup_state == BatchState.CRITICAL_FAILURE.value
    assert not broken.passed


def test_a_clean_run_a_counts_zero_wrong_writes():
    """The control. If the counter were stuck ON, the condition would be just
    as useless as when it was stuck off."""
    assert readiness.run_a().wrong_writes == 0


def test_run_a_counts_a_duplicate_write_that_actually_landed():
    duped = readiness.run_a(client=_company_on(AcceptsADuplicate()))

    assert duped.duplicate_writes == 1, duped.detail
    assert not duped.passed


def test_a_clean_run_a_counts_zero_duplicate_writes():
    assert readiness.run_a().duplicate_writes == 0


def test_a_clean_run_b_and_run_c_count_zero_of_both(tmp_path: pathlib.Path):
    """The control for the four tests below."""
    b = readiness.run_b(str(tmp_path / "store.sqlite"))
    c = readiness.run_c()

    assert (b.wrong_writes, b.duplicate_writes) == (0, 0)
    assert (c.wrong_writes, c.duplicate_writes) == (0, 0)


def test_run_b_counts_a_duplicate_write_that_actually_landed(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
):
    """Run C had no duplicate retry at all until 2026-08-10, so its "zero
    duplicate writes" was an assumption. Both runs now take the reading, and
    both readings have to be able to come back nonzero."""
    monkeypatch.setattr(readiness, "DelaysThenDrops", DropsAndDuplicates)
    b = readiness.run_b(str(tmp_path / "store.sqlite"))

    assert b.duplicate_writes == 1, b.detail
    assert not b.passed


def test_run_c_counts_a_duplicate_write_that_actually_landed(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(readiness, "RefusesOne", RefusesOneAndDuplicates)
    c = readiness.run_c()

    assert c.duplicate_writes == 1, c.detail
    assert not c.passed


def test_run_b_counts_a_wrong_write_made_during_the_resume(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(readiness, "DelaysThenDrops", DropsAndLies)
    b = readiness.run_b(str(tmp_path / "store.sqlite"))

    assert b.wrong_writes == 1, b.detail
    assert b.cleanup_state == BatchState.CRITICAL_FAILURE.value
    # Every voucher really was deleted, so the books DID come back while the
    # batch did not finish. Defect 2 in the wild: one number cannot say both.
    assert b.trial_balance_restored is True
    assert not b.passed


def test_run_c_counts_a_wrong_write_made_during_the_resume(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(readiness, "RefusesOne", RefusesOneAndLies)
    c = readiness.run_c()

    assert c.wrong_writes == 1, c.detail
    assert c.cleanup_state == BatchState.CRITICAL_FAILURE.value
    assert c.trial_balance_restored is True
    assert not c.passed


def test_run_b_reports_a_trial_balance_that_did_not_come_back(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(readiness, "DelaysThenDrops", DropsAndRefusesTheLast)
    b = readiness.run_b(str(tmp_path / "store.sqlite"))

    assert b.trial_balance_restored is False, b.detail
    assert "trial balance restored: False" in b.detail
    assert not b.passed


def test_run_c_reports_a_trial_balance_that_did_not_come_back(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(readiness, "RefusesOne", RefusesOneAndThenTheLast)
    c = readiness.run_c()

    assert c.trial_balance_restored is False, c.detail
    assert "trial balance restored: False" in c.detail
    assert not c.passed


def test_the_gate_separates_the_two_mismatches_on_a_real_run(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
):
    """Defect 2 without a constructed RunResult anywhere.

    Run C deletes all ten vouchers and reports one of the deletes as failed.
    The books come back to the paise AND the cleanup batch ends
    CRITICAL_FAILURE. One measurement reported under two names would have to
    call that either two mismatches or none; it is exactly one of each.
    """
    monkeypatch.setattr(readiness, "RefusesOne", RefusesOneAndLies)
    gate = readiness.run_gate(str(tmp_path / "store.sqlite"))

    assert _condition(gate, "cleanup_mismatches").actual == 1
    assert _condition(gate, "trial_balance_mismatches").actual == 0
    assert _condition(gate, "wrong_writes").actual == 1


def test_the_gate_reports_the_wrong_writes_the_runs_measured(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
):
    """The condition that could not fail. With one real wrong write in run A
    the gate must report 1 and NOT pass."""
    original = readiness.run_a
    gate = _gate_with_run_a(
        tmp_path,
        monkeypatch,
        lambda: original(client=_company_on(MovesTheBooksAndDeniesIt())),
    )

    condition = _condition(gate, "wrong_writes")
    assert condition.actual == 1, "the gate did not count a wrong write that happened"
    assert not condition.passed
    assert "wrong_writes" in {c.name for c in gate.failures()}


def test_the_gate_reports_the_duplicate_writes_the_runs_measured(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
):
    original = readiness.run_a
    gate = _gate_with_run_a(
        tmp_path, monkeypatch, lambda: original(client=_company_on(AcceptsADuplicate()))
    )

    condition = _condition(gate, "duplicate_writes")
    assert condition.actual == 1, "the gate did not count a duplicate that landed"
    assert not condition.passed
    assert "duplicate_writes" in {c.name for c in gate.failures()}


# ---- G2: two claims need two measurements -----------------------------------


def _run(**over: object) -> readiness.RunResult:
    """A RunResult with every measurement stated. There is no default for any
    of them on purpose: an omitted measurement is defect 1 again."""
    fields: dict[str, object] = {
        "name": readiness.RUN_A,
        "passed": True,
        "lifecycles": acceptance.N,
        "detail": "constructed for this test",
        "wrong_writes": 0,
        "duplicate_writes": 0,
        "trial_balance_restored": True,
        "cleanup_state": BatchState.COMPLETED.value,
    }
    fields.update(over)
    return readiness.RunResult(**fields)  # type: ignore[arg-type]


def test_the_gate_separates_a_cleanup_mismatch_from_a_trial_balance_mismatch(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
):
    """A batch that stopped before it touched anything has moved nothing, so
    the books still match while the cleanup plainly did not finish.

    One measurement reported under two names has to call this either two
    mismatches or none. It is one of each, and this is the direction that says
    so.
    """
    gate = _gate_with_run_a(
        tmp_path,
        monkeypatch,
        lambda: _run(passed=False, cleanup_state=BatchState.PARTIAL_FAILURE.value),
    )

    assert _condition(gate, "cleanup_mismatches").actual == 1
    assert _condition(gate, "trial_balance_mismatches").actual == 0


def test_the_gate_separates_a_trial_balance_mismatch_from_a_cleanup_mismatch(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
):
    """And the other direction: every voucher reported reversed, the batch
    COMPLETED, and the trial balance did not come back."""
    gate = _gate_with_run_a(
        tmp_path, monkeypatch, lambda: _run(passed=False, trial_balance_restored=False)
    )

    assert _condition(gate, "trial_balance_mismatches").actual == 1
    assert _condition(gate, "cleanup_mismatches").actual == 0


# ---- G3: the condition was blind to run A -----------------------------------


def test_run_a_says_whether_the_trial_balance_was_restored():
    """Runs B and C have always said it. Run A said nothing, so a run A that
    left the books wrong could not be counted by anything."""
    clean = readiness.run_a()

    assert clean.trial_balance_restored is True
    assert "trial balance restored: True" in clean.detail


def test_a_run_a_that_leaves_the_books_wrong_says_so_in_words_and_in_the_field():
    broken = readiness.run_a(client=_company_on(MovesTheBooksAndDeniesIt()))

    assert broken.trial_balance_restored is False
    assert "trial balance restored: False" in broken.detail


def test_the_operator_line_and_the_measured_field_cannot_disagree(
    tmp_path: pathlib.Path,
):
    """The report a person reads and the number the gate counts are the same
    fact, or one of the two is lying."""
    runs = (
        readiness.run_a(),
        readiness.run_b(str(tmp_path / "store.sqlite")),
        readiness.run_c(),
    )

    for r in runs:
        assert f"trial balance restored: {r.trial_balance_restored}" in r.detail, (
            f"run {r.name} reports one thing and measures another: {r.detail}"
        )


def test_the_gate_sees_a_broken_trial_balance_in_run_a(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
):
    """The whole of defect 3, at the gate. Real run A, real broken books."""
    original = readiness.run_a
    gate = _gate_with_run_a(
        tmp_path,
        monkeypatch,
        lambda: original(client=_company_on(MovesTheBooksAndDeniesIt())),
    )

    condition = _condition(gate, "trial_balance_mismatches")
    assert condition.actual == 1, "run A broke the books and the gate did not notice"
    assert not condition.passed


# ---- the gate as a whole -----------------------------------------------------


def test_the_gate_reports_thirty_lifecycles_and_twelve_conditions(
    tmp_path: pathlib.Path,
):
    gate = readiness.run_gate(str(tmp_path / "store.sqlite"))

    assert gate.lifecycles == readiness.LIFECYCLES == 30
    assert len(gate.conditions) == 12
    assert len(gate.runs) == readiness.RUNS == 3


def test_the_gate_does_not_pass_while_the_clean_room_has_not_run(
    tmp_path: pathlib.Path,
):
    """NOT_RUN is not a pass. A gate that quietly skips its slowest check is a
    gate that reports on the checks it felt like doing."""
    gate = readiness.run_gate(str(tmp_path / "store.sqlite"))

    assert gate.verdict == "NOT_PASSED"
    assert {c.name for c in gate.failures()} == {"clean_room_install"}
    assert "NOT_RUN" in " ".join(gate.notes)


def test_every_other_condition_passes_without_the_clean_room(
    tmp_path: pathlib.Path,
):
    gate = readiness.run_gate(str(tmp_path / "store.sqlite"))

    for c in gate.conditions:
        if c.name != "clean_room_install":
            assert c.passed, f"{c.name}: actual {c.actual!r}, expected {c.expected!r}"


def test_a_failing_run_fails_the_whole_gate(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
):
    def broken(*_a: object, **_k: object) -> readiness.RunResult:
        return _run(passed=False, detail="broken")

    monkeypatch.setattr(readiness, "run_a", broken)
    gate = readiness.run_gate(str(tmp_path / "store.sqlite"))

    assert gate.verdict == "NOT_PASSED"
    assert not gate.passed


def test_the_bundle_and_the_report_both_carry_the_verdict(tmp_path: pathlib.Path):
    gate = readiness.run_gate(str(tmp_path / "store.sqlite"))

    bundle = readiness.bundle(gate)
    assert bundle["phase"] == "5B"
    assert bundle["expected_lifecycles"] == 30
    assert bundle["lifecycles"] == 30
    assert len(bundle["runs"]) == 3  # type: ignore[arg-type]
    assert len(bundle["conditions"]) == 12  # type: ignore[arg-type]

    text = readiness.render(gate)
    assert "PHASE 5B" in text
    assert "30 of 30 voucher lifecycles" in text
    assert f"VERDICT: {gate.verdict}" in text


# ---- the clean room, and the whole gate green -------------------------------


def test_the_wheel_installs_into_an_empty_virtualenv_and_runs_elsewhere():
    """Builds a wheel, installs it with no index and no deps, imports it from a
    directory that is not the repo, and runs the reversal command there.

    `--no-deps` is only safe because runtime dependencies are `[]`. If that ever
    changes this fails, which is the correct alarm.
    """
    ok, detail = readiness.clean_room_install()
    assert ok, detail
    assert ".whl" in detail
    assert "refused with exit" in detail


def test_the_whole_gate_passes_with_the_clean_room_supplied(tmp_path: pathlib.Path):
    """PHASE 5B, end to end. The only run that may be reported as the gate."""
    gate = readiness.run_gate(
        str(tmp_path / "store.sqlite"), clean_room=readiness.clean_room_install
    )

    assert gate.verdict == "PASSED", readiness.render(gate)
    assert gate.lifecycles == 30
    assert gate.failures() == ()


# ---- the phase map is not allowed to drift ----------------------------------


def test_phase_5b_is_not_phase_6():
    """The collision that was raised and ruled on. Phase 6 is the detector.

    Written as a test because a rule recorded only in prose is a rule that gets
    re-broken by whoever writes the next mandate.
    """
    source = pathlib.Path(readiness.__file__).read_text(encoding="utf-8")
    assert "Phase 6 remains the first detector" in source
    assert "Phase 5B  operational readiness" in source

    architecture = pathlib.Path("docs/ARCHITECTURE.md").read_text(encoding="utf-8")
    assert "### Phase 6 — the first detector" in architecture, (
        "the canonical Phase 6 definition has been renamed or replaced"
    )
