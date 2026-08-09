"""G5.2 — bulk reversal: fail-closed, durable, resumable.

Before this file there was no bulk reversal anywhere in the product.
`POST /reverse` (`web/app.py`) took ONE operation id out of a form. The frozen
plan documents `python -m accountant.tallyio --reverse-all --company "Demo Co"`
and criterion #14.7 says the app "shows every voucher we have written and offers
bulk reverse". Neither existed. The only batch-shaped thing in the repository
was `tests/test_tally_contract.py`, which loops the CLIENT directly — no state,
no durability, no partial-failure policy, and it bypasses the verified
`pipeline.reverse_operation` doorway entirely.

THE DECISION THIS FILE ENCODES — owner, 2026-08-09
--------------------------------------------------
Bulk reversal stops at the first unresolved voucher and resumes explicitly.

    posting 10 vouchers   = setup
    reversing 10 vouchers = cleanup

If voucher 4's reversal fails, vouchers 1-3 are NOT re-reversed. They are
already correctly cleaned up, and undoing successful cleanup would put entries
back into somebody's books that nobody asked for. The intended resting state is
partial and is recorded as such:

    1-3   REVERSED_VERIFIED
    4     the exact failure state
    5-10  NOT_ATTEMPTED

A later resume finishes the OUTSTANDING cleanup. It never re-touches 1-3.
A sequence of external operations is not one atomic transaction and this code
does not pretend it is.

WHAT THIS FILE DOES NOT PROVE
-----------------------------
Anything about a real TallyPrime. Evidence class: FAKETALLY. Every failure is
injected into an in-memory double. `tests/test_real_tally.py` runs the same
doorway against `RealTally` over the XML simulator; a licensed Tally has seen
none of it.
"""

from __future__ import annotations

import datetime

import pytest

from accountant import pipeline, reversal
from accountant.memory.store import MemoryStore
from accountant.reversal import BatchState, VoucherState
from accountant.schema import Voucher
from accountant.tallyio.client import CompanyNotBackedUp, new_operation_id, stamp
from accountant.tallyio.fake import FakeTally

COMPANY = "Demo Co"
ACCOUNTS = ("Purchases", "Cash")
KEY = "demo_co"


def a_voucher(n: int) -> Voucher:
    return Voucher(
        id=f"draft-{n}",
        date=datetime.date(2026, 8, 31),
        party="Sharma Traders",
        narration=f"cement load {n}",
        debit_account="Purchases",
        credit_account="Cash",
        amount_paise=100_000 + n,
    )


def books(theirs: int = 0) -> FakeTally:
    t = FakeTally()
    t.add_company(COMPANY, accounts=ACCOUNTS, backed_up=True)
    for i in range(theirs):
        t.seed_voucher(
            COMPANY,
            Voucher(
                id=f"human-{i}",
                date=datetime.date(2026, 8, 1),
                party="Verma Properties",
                narration="rent paid by hand",
                debit_account="Purchases",
                credit_account="Cash",
                amount_paise=555_00,
            ),
        )
    return t


def post_n(tally: FakeTally, n: int) -> list[str]:
    """N vouchers written the way the product writes them. Returns the ids."""
    ops: list[str] = []
    for i in range(n):
        op = new_operation_id()
        tally.write_voucher(COMPANY, a_voucher(i), op)
        ops.append(op)
    return ops


def run_batch(tally: FakeTally, *, log: MemoryStore | None = None) -> reversal.Batch:
    batch = reversal.confirm(reversal.preview(tally, COMPANY, batch_id="bulk_test"))
    return reversal.execute(batch, tally, log=log, company_key=KEY, run_id="run-1")


# ---- preview: what would be touched, and the four refusals ------------------


def test_preview_lists_only_our_vouchers_and_never_the_users_own():
    tally = books(theirs=2)
    ops = post_n(tally, 3)

    batch = reversal.preview(tally, COMPANY, batch_id="b1")

    assert batch.state is BatchState.PREVIEW
    assert [o.operation_id for o in batch.outcomes] == ops
    assert all(o.state is VoucherState.NOT_ATTEMPTED for o in batch.outcomes)
    assert len(tally.read_vouchers(COMPANY)) == 5, "the register still holds all five"


def test_preview_refuses_a_company_with_no_recorded_backup():
    """#6.7. The refusal comes from the connector and is not caught here."""
    tally = FakeTally()
    tally.add_company(COMPANY, accounts=ACCOUNTS, backed_up=False)

    with pytest.raises(CompanyNotBackedUp):
        reversal.preview(tally, COMPANY, batch_id="b1")


def test_preview_refuses_when_a_candidate_carries_no_operation_id():
    """A voucher in `list_our_vouchers` whose narration lost its marker.

    Reversal targets the operation id and nothing else. A candidate without one
    cannot be reversed safely, and guessing by amount or narration is exactly
    what correction C4 forbids — two vouchers with the same amount and
    narration are normal.
    """

    class LosesTheMarker(FakeTally):
        def list_our_vouchers(self, company: str) -> tuple[Voucher, ...]:
            real = super().list_our_vouchers(company)
            from dataclasses import replace as _replace

            return (_replace(real[0], narration="marker rubbed off"), *real[1:])

    tally = LosesTheMarker()
    tally.add_company(COMPANY, accounts=ACCOUNTS, backed_up=True)
    post_n(tally, 2)

    with pytest.raises(ValueError, match="no operation id"):
        reversal.preview(tally, COMPANY, batch_id="b1")


def test_an_unconfirmed_batch_will_not_execute():
    """The explicit-confirmation gate. A preview is a question, not an order."""
    tally = books()
    post_n(tally, 2)
    batch = reversal.preview(tally, COMPANY, batch_id="b1")
    before = tally.trial_balance(COMPANY)

    with pytest.raises(ValueError, match="not been confirmed"):
        reversal.execute(batch, tally, company_key=KEY)

    assert tally.trial_balance(COMPANY) == before, "nothing moved"
    assert len(tally.list_our_vouchers(COMPANY)) == 2


# ---- the clean run ----------------------------------------------------------


def test_a_clean_batch_reverses_everything_and_restores_the_exact_paise():
    """Two different conservation claims, and both hold.

    `before_posting` is the caller's: the books end where they were before the
    three vouchers existed. `accounted` is the batch's own: every paise that
    moved is one of the movements it verified. The batch cannot make the first
    claim, because its own baseline is taken AFTER the vouchers were posted.
    """
    tally = books(theirs=1)
    before_posting = tally.trial_balance(COMPANY)
    post_n(tally, 3)
    assert tally.trial_balance(COMPANY) != before_posting

    result = run_batch(tally)

    assert result.state is BatchState.COMPLETED
    assert all(o.state is VoucherState.REVERSED_VERIFIED for o in result.outcomes)
    assert result.final == before_posting
    assert result.accounted is True
    assert tally.list_our_vouchers(COMPANY) == ()
    assert len(tally.read_vouchers(COMPANY)) == 1, "the hand-typed voucher is untouched"


def test_a_batch_with_nothing_of_ours_completes_and_says_so():
    """Not a failure. Zero vouchers, zero failures, the books did not move."""
    tally = books(theirs=2)
    baseline = tally.trial_balance(COMPANY)

    result = run_batch(tally)

    assert result.state is BatchState.COMPLETED
    assert result.outcomes == ()
    assert result.final == baseline
    assert "nothing of ours" in result.detail
    assert len(tally.read_vouchers(COMPANY)) == 2


def test_every_reversal_goes_through_the_verified_doorway():
    """A structural check. The claim is "no other path", and only the AST says so.

    `pipeline.reverse_operation` is what compares the trial balance before and
    after. A batch that called `client.reverse_by_operation_id` directly would
    be fast, would look right, and would reintroduce the exact defect §39.1
    closed — "reversed" meaning "a boolean said so".
    """
    import ast
    import pathlib

    tree = ast.parse(pathlib.Path(reversal.__file__).read_text(encoding="utf-8"))
    direct = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "reverse_by_operation_id"
    ]
    assert direct == [], (
        f"accountant/reversal.py calls the connector's reverse directly at "
        f"lines {direct}; every undo goes through pipeline.reverse_operation"
    )


# ---- the four failure categories, each at voucher 4 of 10 -------------------


def _ten(tally: FakeTally) -> list[str]:
    return post_n(tally, 10)


class _Injector(FakeTally):
    """The shape every failure double below shares.

    `target` is the operation id that will misbehave — voucher 4 of 10 in every
    test here. Declared on a common base so the helper can set it without
    reaching into a subclass the type checker cannot narrow.
    """

    target: str = ""


class _Vanishes(_Injector):
    """Voucher 4 is gone by the time the batch reaches it. No request is sent."""

    def read_by_operation_id(self, company: str, operation_id: str) -> Voucher | None:
        if operation_id == self.target:
            return None
        return super().read_by_operation_id(company, operation_id)


class _Refuses(_Injector):
    """Tally says clearly that it did not reverse voucher 4."""

    def reverse_by_operation_id(self, company: str, operation_id: str) -> bool:
        if operation_id == self.target:
            return False
        return super().reverse_by_operation_id(company, operation_id)


class _DropsTheConnection(_Injector):
    """The request may or may not have reached Tally. Nobody can say."""

    def reverse_by_operation_id(self, company: str, operation_id: str) -> bool:
        if operation_id == self.target:
            raise ConnectionError("the connection dropped mid-request")
        return super().reverse_by_operation_id(company, operation_id)


class _MovesTheWrongAmount(_Injector):
    """Tally reports success and the books move by something else."""

    def reverse_by_operation_id(self, company: str, operation_id: str) -> bool:
        if operation_id != self.target:
            return super().reverse_by_operation_id(company, operation_id)
        found = [
            v
            for v in self.read_vouchers(company)
            if v.narration.endswith(f"{operation_id}]")
        ]
        if not found:
            return False
        co = self._companies[company]
        co.vouchers = [v for v in co.vouchers if v not in found]
        co.vouchers.append(
            Voucher(
                id="stub",
                date=datetime.date(2026, 8, 31),
                party="Sharma Traders",
                narration="half of it put back",
                debit_account="Purchases",
                credit_account="Cash",
                amount_paise=found[0].amount_paise // 2,
            )
        )
        return True


class _StaysFindable(_Injector):
    """The books move correctly, and the voucher is still there afterwards."""

    _reversed = False

    def read_by_operation_id(self, company: str, operation_id: str) -> Voucher | None:
        found = super().read_by_operation_id(company, operation_id)
        if found is None and operation_id == self.target and self._reversed:
            return a_voucher(3)
        return found

    def reverse_by_operation_id(self, company: str, operation_id: str) -> bool:
        ok = super().reverse_by_operation_id(company, operation_id)
        if operation_id == self.target:
            self._reversed = True
        return ok


def _failing_batch(
    kind: type[_Injector],
) -> tuple[FakeTally, reversal.Batch, list[str]]:
    tally = kind()
    tally.add_company(COMPANY, accounts=ACCOUNTS, backed_up=True)
    ops = _ten(tally)
    kind.target = ops[3]  # voucher 4 of 10
    return tally, run_batch(tally), ops


@pytest.mark.parametrize(
    ("kind", "voucher_state", "batch_state"),
    [
        (_Vanishes, VoucherState.PRECHECK_REFUSED, BatchState.PARTIAL_FAILURE),
        (_Refuses, VoucherState.EXPLICIT_REJECTION, BatchState.PARTIAL_FAILURE),
        (_DropsTheConnection, VoucherState.UNKNOWN_OUTCOME, BatchState.UNKNOWN_OUTCOME),
        (
            _MovesTheWrongAmount,
            VoucherState.WRONG_MOVEMENT,
            BatchState.CRITICAL_FAILURE,
        ),
        (_StaysFindable, VoucherState.READBACK_FAILED, BatchState.PARTIAL_FAILURE),
    ],
)
def test_a_failure_at_voucher_four_stops_the_batch_and_names_the_category(
    kind: type[_Injector], voucher_state: VoucherState, batch_state: BatchState
):
    """The whole partial-failure policy, one row of the owner's table per case."""
    _, result, ops = _failing_batch(kind)

    assert result.state is batch_state
    assert [o.state for o in result.outcomes[:3]] == [
        VoucherState.REVERSED_VERIFIED
    ] * 3
    assert result.outcomes[3].state is voucher_state
    assert result.outcomes[3].operation_id == ops[3]
    assert [o.state for o in result.outcomes[4:]] == [VoucherState.NOT_ATTEMPTED] * 6


@pytest.mark.parametrize(
    "kind",
    [_Vanishes, _Refuses, _DropsTheConnection, _MovesTheWrongAmount, _StaysFindable],
)
def test_a_batch_with_one_unresolved_voucher_never_reports_completed(
    kind: type[_Injector],
):
    """The single rule that outranks every other reporting choice here."""
    _, result, _ = _failing_batch(kind)
    assert result.state is not BatchState.COMPLETED


@pytest.mark.parametrize("kind", [_Vanishes, _Refuses, _DropsTheConnection])
def test_a_voucher_that_failed_cleanly_moved_no_money(kind: type[_Injector]):
    """`accounted` is what proves the failure was clean.

    Vouchers 1-3 moved and their movements are recorded; voucher 4 failed and
    the books show no trace of it; 5-10 were never tried. So the trial balance
    is exactly the baseline plus the three verified movements, and nothing else
    happened in between.
    """
    _, result, _ = _failing_batch(kind)
    assert result.accounted is True


def test_a_wrong_movement_shows_up_as_money_nobody_accounted_for():
    """The other side of the same measurement. Half the voucher came back and
    no verified movement explains it."""
    _, result, _ = _failing_batch(_MovesTheWrongAmount)
    assert result.accounted is False


@pytest.mark.parametrize(
    "kind", [_Vanishes, _Refuses, _DropsTheConnection, _MovesTheWrongAmount]
)
def test_vouchers_one_to_three_stay_reversed_and_are_never_put_back(
    kind: type[_Injector],
):
    """Cleanup, not rollback. The partial state is the intended resting state."""
    tally, result, ops = _failing_batch(kind)

    remaining = {
        o.operation_id for o in result.outcomes if o.state is VoucherState.NOT_ATTEMPTED
    }
    still_there = {
        v.narration.rsplit("[ACCOUNTANT_DAD:", 1)[-1].rstrip("]")
        for v in tally.list_our_vouchers(COMPANY)
    }
    assert remaining <= still_there, "untried vouchers are still in the books"
    assert not (still_there & set(ops[:3])), "the first three are gone and stay gone"


def test_the_connection_drop_leaves_the_remaining_six_untouched_in_tally():
    """UNKNOWN_OUTCOME must not process the rest automatically."""
    tally, result, ops = _failing_batch(_DropsTheConnection)

    assert result.state is BatchState.UNKNOWN_OUTCOME
    survivors = {
        v.narration.rsplit("[ACCOUNTANT_DAD:", 1)[-1].rstrip("]")
        for v in tally.list_our_vouchers(COMPANY)
    }
    assert set(ops[4:]) <= survivors


def test_a_wrong_movement_records_what_moved_and_what_should_have():
    _, result, _ = _failing_batch(_MovesTheWrongAmount)
    detail = result.outcomes[3].detail
    assert "should have moved" in detail
    assert result.state is BatchState.CRITICAL_FAILURE


# ---- durability -------------------------------------------------------------


def test_each_voucher_outcome_is_durable_before_the_next_one_is_attempted():
    """A crash mid-batch must leave a readable state, not a guess.

    The rows are what a resume reads. Written per voucher rather than once at
    the end, because a summary written at the end is exactly the row that does
    not exist when the process dies.
    """
    tally = books()
    store = MemoryStore(":memory:")
    ops = post_n(tally, 3)

    result = run_batch(tally, log=store)
    assert result.state is BatchState.COMPLETED

    rows = [r for r in store.actions(KEY) if r.action == reversal.BATCH_ACTION]
    # one REQUEST_SENT and one terminal row per voucher, in order
    assert [r.operation_id for r in rows] == [op for op in ops for _ in range(2)]
    assert [r.outcome for r in rows[::2]] == [VoucherState.REQUEST_SENT] * 3
    assert [r.outcome for r in rows[1::2]] == [VoucherState.REVERSED_VERIFIED] * 3
    assert all(r.reason.strip() for r in rows)


def test_the_durable_row_for_an_unknown_outcome_says_it_is_unknown():
    tally = _DropsTheConnection()
    tally.add_company(COMPANY, accounts=ACCOUNTS, backed_up=True)
    store = MemoryStore(":memory:")
    ops = _ten(tally)
    _DropsTheConnection.target = ops[3]

    reversal.execute(
        reversal.confirm(reversal.preview(tally, COMPANY, batch_id="b1")),
        tally,
        log=store,
        company_key=KEY,
        run_id="run-1",
    )

    rows = [r for r in store.actions(KEY) if r.operation_id == ops[3]]
    assert [r.outcome for r in rows] == [
        VoucherState.REQUEST_SENT,
        VoucherState.UNKNOWN_OUTCOME,
    ]
    assert "dropped" in rows[-1].reason


# ---- reconciliation and resume ----------------------------------------------


def test_resume_is_refused_before_reconciliation():
    _, result, _ = _failing_batch(_DropsTheConnection)
    tally = books()

    with pytest.raises(ValueError, match="reconcil"):
        reversal.resume(result, tally, approved=True, company_key=KEY)


def test_resume_is_refused_without_explicit_approval():
    """Reconciling is a read. Resuming writes, and needs a person to say so."""
    tally, result, _ = _failing_batch(_DropsTheConnection)
    reconciled = reversal.reconcile(result, tally)

    with pytest.raises(ValueError, match="approval"):
        reversal.resume(reconciled, tally, approved=False, company_key=KEY)


def test_reconciliation_is_read_only_and_moves_nothing():
    tally, result, _ = _failing_batch(_DropsTheConnection)
    before = tally.trial_balance(COMPANY)

    reversal.reconcile(result, tally)

    assert tally.trial_balance(COMPANY) == before


def test_reconciliation_finds_the_voucher_still_there_and_makes_it_retryable():
    """The connection dropped before Tally acted. The voucher is still present,
    so retrying it is no longer blind — the state was established by a read."""
    tally, result, ops = _failing_batch(_DropsTheConnection)

    reconciled = reversal.reconcile(result, tally)

    fourth = reconciled.outcomes[3]
    assert fourth.operation_id == ops[3]
    assert fourth.state is VoucherState.NOT_ATTEMPTED
    assert "still in Tally" in fourth.detail


def test_reconciliation_finds_the_voucher_gone_and_records_it_as_reversed():
    """The other half: the request DID land, and the answer was lost."""

    class DropsTheAnswerNotTheRequest(_Injector):
        def reverse_by_operation_id(self, company: str, operation_id: str) -> bool:
            ok = super().reverse_by_operation_id(company, operation_id)
            if operation_id == self.target:
                raise ConnectionError("the answer never came back")
            return ok

    tally = DropsTheAnswerNotTheRequest()
    tally.add_company(COMPANY, accounts=ACCOUNTS, backed_up=True)
    ops = _ten(tally)
    DropsTheAnswerNotTheRequest.target = ops[3]
    result = run_batch(tally)
    assert result.outcomes[3].state is VoucherState.UNKNOWN_OUTCOME

    reconciled = reversal.reconcile(result, tally)

    assert reconciled.outcomes[3].state is VoucherState.REVERSED_VERIFIED
    assert "no longer in Tally" in reconciled.outcomes[3].detail
    # And the batch does NOT become COMPLETED on the strength of that. Six
    # vouchers are still in the books, untried. A reconciliation that resolves
    # the last unknown favourably is the exact moment a state machine is
    # tempted to declare victory over work it has not done.
    assert reconciled.state is BatchState.PARTIAL_FAILURE
    assert len(reconciled.in_state(VoucherState.NOT_ATTEMPTED)) == 6


def test_a_resumed_batch_finishes_the_outstanding_cleanup_and_nothing_else():
    """The whole resume contract in one run.

    Vouchers 1-3 were already reversed. The resume must not touch them, must
    finish 4-10, and must land on COMPLETED with the books back to baseline.
    """
    tally = _DropsTheConnection()
    tally.add_company(COMPANY, accounts=ACCOUNTS, backed_up=True)
    baseline = tally.trial_balance(COMPANY)
    ops = _ten(tally)
    _DropsTheConnection.target = ops[3]

    stopped = run_batch(tally)
    assert stopped.state is BatchState.UNKNOWN_OUTCOME

    # The cause is corrected: the connection is no longer dropping.
    _DropsTheConnection.target = ""
    reconciled = reversal.reconcile(stopped, tally)
    finished = reversal.resume(reconciled, tally, approved=True, company_key=KEY)

    assert finished.state is BatchState.COMPLETED
    assert all(o.state is VoucherState.REVERSED_VERIFIED for o in finished.outcomes)
    assert finished.final == baseline
    assert tally.list_our_vouchers(COMPANY) == ()


def test_a_resume_never_re_reverses_an_already_verified_voucher():
    """Undoing successful cleanup would put entries back that nobody asked for.

    Measured by counting calls: the first three operation ids must not reach
    the connector a second time.
    """
    seen: list[str] = []

    class Counting(_DropsTheConnection):
        def reverse_by_operation_id(self, company: str, operation_id: str) -> bool:
            seen.append(operation_id)
            return super().reverse_by_operation_id(company, operation_id)

    tally = Counting()
    tally.add_company(COMPANY, accounts=ACCOUNTS, backed_up=True)
    ops = _ten(tally)
    Counting.target = ops[3]

    stopped = run_batch(tally)
    Counting.target = ""
    reversal.resume(
        reversal.reconcile(stopped, tally), tally, approved=True, company_key=KEY
    )

    for op in ops[:3]:
        assert seen.count(op) == 1, f"{op} was reversed more than once"


def test_resuming_a_completed_batch_does_nothing_at_all():
    tally = books()
    post_n(tally, 3)
    done = run_batch(tally)
    assert done.state is BatchState.COMPLETED
    before = tally.trial_balance(COMPANY)

    again = reversal.resume(
        reversal.reconcile(done, tally), tally, approved=True, company_key=KEY
    )

    assert again.state is BatchState.COMPLETED
    assert tally.trial_balance(COMPANY) == before


def test_a_critical_failure_cannot_be_resumed():
    """WRONG_MOVEMENT means the books and the answer disagree. A person looks
    at it; a program does not carry on writing."""
    tally, result, _ = _failing_batch(_MovesTheWrongAmount)

    with pytest.raises(ValueError, match="CRITICAL_FAILURE"):
        reversal.resume(
            reversal.reconcile(result, tally), tally, approved=True, company_key=KEY
        )


# ---- the marker helper the batch relies on ----------------------------------


def test_the_connector_itself_refuses_to_reverse_without_a_recorded_backup():
    """Below `preview`, at the connector, where the write path has always
    enforced it and the delete path never did until 2026-08-09.

    `preview` refuses first, which is why this needs its own test: a guard that
    is only ever reached through another guard is not a guard, it is a comment.
    A caller holding a client — the CLI, a script, a future surface — must be
    refused too.
    """
    tally = FakeTally()
    tally.add_company(COMPANY, accounts=ACCOUNTS, backed_up=True)
    ops = post_n(tally, 2)
    before = tally.trial_balance(COMPANY)

    tally.set_backup(COMPANY, False)

    with pytest.raises(CompanyNotBackedUp, match="refusing to reverse"):
        tally.reverse_by_operation_id(COMPANY, ops[0])

    assert tally.trial_balance(COMPANY) == before
    assert len(tally.list_our_vouchers(COMPANY)) == 2


def test_the_stamped_narration_is_what_preview_reads_the_id_from():
    """Guards the assumption the whole batch rests on."""
    assert reversal.operation_id_of(stamp("cement", "ad_x")) == "ad_x"
    assert reversal.operation_id_of("no marker here") is None


def test_a_batch_reverses_the_exact_vouchers_preview_promised():
    """No re-listing between preview and execute.

    If `execute` re-read `list_our_vouchers`, a voucher posted by another
    process in the gap would be swept up by a batch the person never saw and
    never confirmed.
    """
    tally = books()
    ops = post_n(tally, 2)
    batch = reversal.confirm(reversal.preview(tally, COMPANY, batch_id="b1"))

    late = new_operation_id()
    tally.write_voucher(COMPANY, a_voucher(99), late)

    result = reversal.execute(batch, tally, company_key=KEY)

    assert [o.operation_id for o in result.outcomes] == ops
    survivors = [pipeline.reverse_operation(tally, COMPANY, late).operation_id]
    assert survivors == [late], "the latecomer was still there to be reversed"
