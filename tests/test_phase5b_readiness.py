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

WHAT THIS FILE DOES NOT PROVE
-----------------------------
Anything about a licensed TallyPrime. Every failure here is injected into
`FakeTally`; a failure is never manufactured in real statutory books.
Evidence class: FAKETALLY.
"""

from __future__ import annotations

import pathlib

import pytest

from accountant import reversal
from accountant.memory.store import MemoryStore
from accountant.reversal import BatchState, VoucherState
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
        return readiness.RunResult(
            name=readiness.RUN_A, passed=False, lifecycles=10, detail="broken"
        )

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
