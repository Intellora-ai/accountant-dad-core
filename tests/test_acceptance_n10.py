"""G5.3 — the N = 10 conservation proof, and every way it must refuse to pass.

OWNER DECISION, 2026-08-09: N = 10.

Before this file, N was 1 everywhere. `ci/educational_slice.py` posts one
voucher and reverses it; `tests/test_tally_contract.py` posts three and loops
the client. Nothing exercised a batch of ten through the product's own path,
and nothing asserted the fifteen pass conditions individually.

WHY THE FAILURE CASES ARE HALF THIS FILE
----------------------------------------
A harness that has only ever passed is a harness nobody has tested. Each
failure below breaks exactly one condition and asserts that the run reports
NOT_PASSED and names that condition — because the value of this thing is not
that it says PASSED, it is that it cannot say PASSED when it should not.

WHAT THIS FILE DOES NOT PROVE
-----------------------------
Anything about a licensed TallyPrime. Two evidence classes run here, FAKETALLY
and SIMULATOR, and both are labelled as such by the harness itself. The live
class, LICENSED_REALTALLY, is REQUIRED and NOT YET RUN.
"""

from __future__ import annotations

import datetime

import pytest

from accountant.reversal import BatchState, VoucherState
from accountant.schema import Voucher
from accountant.tallyio import real
from accountant.tallyio.client import TallyClient, new_operation_id
from accountant.tallyio.fake import FakeTally
from ci import acceptance
from ci.acceptance import AcceptanceRun, N
from tests.test_real_tally import TallySim

COMPANY = "Demo Co"
ACCOUNTS = ("Purchases", "Cash")


def fake() -> FakeTally:
    t = FakeTally()
    t.add_company(COMPANY, accounts=ACCOUNTS, backed_up=True)
    return t


def sim_client() -> TallyClient:
    sim = TallySim()
    sim.add_company(COMPANY, ACCOUNTS)
    return real.RealTally(
        transport=sim, backups=real.RecordedBackups(frozenset({COMPANY}))
    )


def run(client: TallyClient, evidence: str, run_id: str = "acc-1") -> AcceptanceRun:
    return acceptance.run_acceptance(
        client, COMPANY, run_id=run_id, evidence_class=evidence
    )


def condition(result: AcceptanceRun, name: str) -> acceptance.Condition:
    found = [c for c in result.conditions if c.name == name]
    assert found, (
        f"no condition named {name!r} in {[c.name for c in result.conditions]}"
    )
    return found[0]


# ---- N is the owner's number, and it is not negotiable ----------------------


def test_n_is_ten():
    """The one assertion that would notice somebody quietly lowering the bar."""
    assert acceptance.N == 10


def test_a_clean_run_passes_every_one_of_the_fifteen_conditions():
    result = run(fake(), acceptance.FAKETALLY)

    assert result.verdict == "PASSED"
    assert result.failures() == ()
    assert len(result.conditions) == 15
    assert result.failed_early == ""


def test_the_same_harness_passes_against_the_real_connector_over_the_simulator():
    """One harness, two backends. If it only worked against `FakeTally` it
    would be measuring our double and not our code."""
    result = run(sim_client(), acceptance.SIMULATOR)

    assert result.verdict == "PASSED", acceptance.render(result)
    assert result.backend == "RealTally"
    assert result.evidence_class == acceptance.SIMULATOR


def test_exactly_ten_vouchers_are_posted_and_ten_are_reversed():
    client = fake()
    result = run(client, acceptance.FAKETALLY)

    assert len(result.operation_ids) == N
    assert len(set(result.operation_ids)) == N
    assert len(result.voucher_ids) == N
    assert len(set(result.voucher_ids)) == N
    assert result.voucher_states == (VoucherState.REVERSED_VERIFIED.value,) * N
    assert result.batch_state == BatchState.COMPLETED.value
    assert client.list_our_vouchers(COMPANY) == ()


def test_the_trial_balance_comes_back_to_the_exact_paise():
    client = fake()
    before = client.trial_balance(COMPANY)

    result = run(client, acceptance.FAKETALLY)

    assert result.baseline == before
    assert result.final == before
    assert condition(result, "trial_balance_restored").passed


def test_a_hand_typed_voucher_is_left_exactly_where_it_was():
    client = fake()
    client.seed_voucher(
        COMPANY,
        Voucher(
            id="human-1",
            date=datetime.date(2026, 8, 1),
            party="Verma Properties",
            narration="rent paid by hand",
            debit_account="Purchases",
            credit_account="Cash",
            amount_paise=555_00,
        ),
    )
    before = client.trial_balance(COMPANY)

    result = run(client, acceptance.FAKETALLY)

    assert result.verdict == "PASSED"
    assert result.final == before
    assert len(client.read_vouchers(COMPANY)) == 1


# ---- the evidence bundle ----------------------------------------------------


def test_the_bundle_carries_every_field_the_measurement_contract_names():
    bundle = run(fake(), acceptance.FAKETALLY).bundle()

    for key in (
        "run_id",
        "company",
        "backend",
        "evidence_class",
        "backed_up",
        "voucher_date",
        "n",
        "baseline_trial_balance",
        "final_trial_balance",
        "operation_ids",
        "voucher_ids",
        "batch_state",
        "voucher_states",
        "conditions",
        "verdict",
    ):
        assert key in bundle, f"the evidence bundle has no {key!r}"
    assert bundle["n"] == 10
    assert len(bundle["conditions"]) == 15  # type: ignore[arg-type]


def test_the_bundle_serialises_so_a_run_can_be_kept():
    text = run(fake(), acceptance.FAKETALLY).to_json()
    assert '"verdict": "PASSED"' in text
    assert '"n": 10' in text


def test_an_unlabelled_run_is_refused_before_anything_is_written():
    """The evidence class is a property of the run, not a caption added later."""
    client = fake()

    with pytest.raises(ValueError, match="not one of"):
        acceptance.run_acceptance(
            client, COMPANY, run_id="x", evidence_class="looks-fine"
        )

    assert client.list_our_vouchers(COMPANY) == ()


def test_every_condition_states_its_actual_expected_and_rule():
    for c in run(fake(), acceptance.FAKETALLY).conditions:
        row = c.as_row()
        assert row["metric"]
        assert row["pass_rule"]
        assert "actual" in row and "expected" in row


# ---- the failure twins: one broken condition each ---------------------------


def test_a_refused_reversal_fails_the_run_and_names_the_conditions():
    client = fake()
    ops_seen: list[str] = []
    original = FakeTally.reverse_by_operation_id

    def refuse_the_fourth(self: FakeTally, company: str, operation_id: str) -> bool:
        ops_seen.append(operation_id)
        if len(ops_seen) == 4:
            return False
        return original(self, company, operation_id)

    FakeTally.reverse_by_operation_id = refuse_the_fourth  # type: ignore[method-assign]
    try:
        result = run(client, acceptance.FAKETALLY)
    finally:
        FakeTally.reverse_by_operation_id = original  # type: ignore[method-assign]

    assert result.verdict == "NOT_PASSED"
    failed = {c.name for c in result.failures()}
    assert "reversals_succeeded" in failed
    assert "cleanup_completed" in failed
    assert "trial_balance_restored" in failed


def test_an_accepted_duplicate_fails_the_run():
    """C5. A connector that lets a retry through creates a second statutory
    entry, and this is the condition that would catch it."""

    class AcceptsDuplicates(FakeTally):
        def write_voucher(self, company: str, voucher: Voucher, operation_id: str):
            if self.read_by_operation_id(company, operation_id) is not None:
                # write it again under a fresh id, which is what a connector
                # without the guard effectively does
                return super().write_voucher(company, voucher, new_operation_id())
            return super().write_voucher(company, voucher, operation_id)

    client = AcceptsDuplicates()
    client.add_company(COMPANY, accounts=ACCOUNTS, backed_up=True)

    result = run(client, acceptance.FAKETALLY)

    assert result.verdict == "NOT_PASSED"
    assert not condition(result, "duplicate_created_nothing").passed
    assert condition(result, "duplicate_created_nothing").actual == 1


def test_a_read_back_that_returns_a_different_voucher_fails_the_run():
    """W1. HTTP 200 is not proof, and neither is a marker on the right box."""

    class SwapsTheAmount(FakeTally):
        def read_by_operation_id(self, company: str, operation_id: str):
            found = super().read_by_operation_id(company, operation_id)
            if found is None:
                return None
            from dataclasses import replace as _replace

            return _replace(found, amount_paise=found.amount_paise + 1)

    client = SwapsTheAmount()
    client.add_company(COMPANY, accounts=ACCOUNTS, backed_up=True)

    result = run(client, acceptance.FAKETALLY)

    assert result.verdict == "NOT_PASSED"
    assert condition(result, "postings_read_back").actual == 0


def test_a_company_that_is_not_open_fails_the_run_and_writes_nothing():
    """Company identity is uncertain, so nothing may be read or written.

    The control is in the same test: the SAME client passes for the company
    that IS open, so the failure is about identity and not about the double.
    """
    client = FakeTally()
    client.add_company("Somebody Else Ltd", accounts=ACCOUNTS, backed_up=True)

    control = acceptance.run_acceptance(
        client, "Somebody Else Ltd", run_id="x", evidence_class=acceptance.FAKETALLY
    )
    assert control.verdict == "PASSED", acceptance.render(control)

    missing = acceptance.run_acceptance(
        client, "Not Open At All", run_id="y", evidence_class=acceptance.FAKETALLY
    )

    assert missing.verdict == "NOT_PASSED"
    assert not condition(missing, "correct_company").passed
    assert missing.failed_early, "it stops at the baseline read, before any write"
    assert missing.operation_ids == (), "and nothing was even attempted"
    assert len(client.read_vouchers("Somebody Else Ltd")) == 0


def test_a_company_with_no_recorded_backup_cannot_even_start():
    client = FakeTally()
    client.add_company(COMPANY, accounts=ACCOUNTS, backed_up=False)

    result = run(client, acceptance.FAKETALLY)

    assert result.verdict == "NOT_PASSED"
    assert result.backed_up is False
    assert result.failed_early, "the first write is refused and the run says so"
    assert client.list_our_vouchers(COMPANY) == ()


def test_a_run_that_stopped_early_can_never_report_passed():
    """`failed_early` outranks the conditions. A run that did not finish has
    not passed, whatever the conditions it did manage to fill in say."""
    client = FakeTally()
    client.add_company(COMPANY, accounts=ACCOUNTS, backed_up=False)

    result = run(client, acceptance.FAKETALLY)

    assert result.failed_early
    assert result.passed is False


def test_a_missing_voucher_identity_makes_the_bundle_incomplete():
    """An evidence bundle with holes is refused, separately from the run's
    own verdict. A missing field is indistinguishable from an unchecked one."""

    class NoTallyId(FakeTally):
        def read_by_operation_id(self, company: str, operation_id: str):
            found = super().read_by_operation_id(company, operation_id)
            if found is None:
                return None
            from dataclasses import replace as _replace

            return _replace(found, tally_id="")

    client = NoTallyId()
    client.add_company(COMPANY, accounts=ACCOUNTS, backed_up=True)

    result = run(client, acceptance.FAKETALLY)

    assert not condition(result, "evidence_complete").passed
    assert result.verdict == "NOT_PASSED"


# ---- the two invariants that a whole-run test cannot reach ------------------


def test_a_run_that_stopped_early_is_not_passed_even_with_every_condition_green():
    """`failed_early` outranks the conditions, and nothing else can prove it.

    In every real failure the conditions fail too, so the guard is invisible
    from the outside — which is exactly how it would be deleted by somebody
    tidying up, and exactly the shape of run it exists to refuse: one that
    stopped in the middle and happened to leave a tidy set of numbers behind.
    """
    green = tuple(acceptance.Condition(f"c{i}", 1, 1, "always true") for i in range(15))
    stopped = AcceptanceRun(
        run_id="x",
        company=COMPANY,
        backend="FakeTally",
        evidence_class=acceptance.FAKETALLY,
        backed_up=True,
        voucher_date=acceptance.DEFAULT_DATE,
        baseline={},
        final={},
        operation_ids=(),
        voucher_ids=(),
        conditions=green,
        batch_state="completed",
        voucher_states=(),
        failed_early="the connection dropped after voucher 6",
    )

    assert all(c.passed for c in stopped.conditions)
    assert stopped.passed is False
    assert stopped.verdict == "NOT_PASSED"


@pytest.mark.parametrize(
    ("ops", "tally_ids", "states", "complete"),
    [
        (["o"] * N, ["t"] * N, ["s"] * N, True),
        (["o"] * (N - 1), ["t"] * N, ["s"] * N, False),
        (["o"] * N, ["t"] * (N - 1), ["s"] * N, False),
        (["o"] * N, ["t"] * N, ["s"] * (N - 1), False),
        ([*["o"] * (N - 1), ""], ["t"] * N, ["s"] * N, False),
        (["o"] * N, [*["t"] * (N - 1), ""], ["s"] * N, False),
    ],
)
def test_every_missing_piece_makes_the_bundle_incomplete(
    ops: list[str], tally_ids: list[str], states: list[str], complete: bool
):
    """Each conjunct on its own. A whole-run test breaks several at once, so
    any single one of them could be deleted and nothing would notice."""
    assert acceptance.bundle_complete(ops, tally_ids, states) is complete


# ---- the report a person reads ----------------------------------------------


def test_the_rendered_report_names_the_evidence_class_and_the_verdict():
    text = acceptance.render(run(fake(), acceptance.FAKETALLY))

    assert "N = 10 acceptance run" in text
    assert acceptance.FAKETALLY in text
    assert "VERDICT: PASSED" in text


def test_the_rendered_report_of_a_failure_names_every_failed_condition():
    client = FakeTally()
    client.add_company(COMPANY, accounts=ACCOUNTS, backed_up=False)

    text = acceptance.render(run(client, acceptance.FAKETALLY))

    assert "VERDICT: NOT_PASSED" in text
    assert "STOPPED EARLY" in text
    assert "FAIL" in text
