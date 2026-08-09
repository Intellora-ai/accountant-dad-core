"""Bulk reversal: fail-closed, durable per voucher, and resumable. G5.2.

WHAT THIS IS FOR
----------------
Phase 5's exit needs N vouchers posted and then reversed as a batch, with the
trial balance back to its exact prior value in paise. Before this module the
product had no batch path at all: `POST /reverse` took one operation id out of
a form, and the only batch-shaped code in the repository was a test looping the
CLIENT directly — no state, no durability, and past the one doorway that checks
the books actually moved.

CLEANUP, NOT ROLLBACK — and the distinction is load-bearing
------------------------------------------------------------
    posting 10 vouchers   = setup
    reversing 10 vouchers = cleanup

If voucher 4's reversal fails, vouchers 1-3 are NOT re-reversed. They are
already correctly cleaned up; putting them back would write entries into
somebody's books that nobody asked for. So the resting state is partial and is
recorded as exactly that:

    1-3   REVERSED_VERIFIED
    4     the exact failure state
    5-10  NOT_ATTEMPTED

A resume finishes the OUTSTANDING cleanup and never re-touches 1-3.

WHY IT STOPS AT THE FIRST FAILURE
---------------------------------
Owner decision, 2026-08-09: fail-closed, resumable execution. A sequence of
external writes is not one atomic transaction, and the moment one of them has
an outcome nobody can name, continuing means writing more into a book whose
state is already uncertain. Stopping costs one interrupted batch. Continuing
costs a reconciliation nobody can do.

THE FOUR FAILURE CATEGORIES ARE NOT INTERCHANGEABLE
---------------------------------------------------
    PRECHECK_REFUSED    the request never went to Tally
    EXPLICIT_REJECTION  Tally clearly said the operation did not occur
    UNKNOWN_OUTCOME     the request may have reached Tally and we cannot prove
                        whether it occurred
    WRONG_MOVEMENT      Tally answered positively and the expected ledger
                        movement was absent or incorrect

`UNKNOWN_OUTCOME` is never treated as a rejection, and transport success is
never treated as accounting success. Those two mistakes are the same mistake
wearing different clothes: believing an answer instead of reading the books.

WHAT THIS MODULE DOES NOT DO
----------------------------
It never calls `client.reverse_by_operation_id`. Every undo goes through
`pipeline.reverse_operation`, which is the only code that compares the trial
balance before and after. A test asserts this structurally, because a direct
call would be faster, would look right, and would reintroduce the defect where
"reversed" meant "a boolean said so".
"""

from __future__ import annotations

import datetime
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum

from accountant import pipeline
from accountant.memory.identity import normalise_company
from accountant.schema import ActionLog
from accountant.tallyio.client import TallyClient, operation_id_in

#: The action name every durable row of a batch carries, so the log can be
#: filtered to "what did the bulk reversal do" without parsing prose.
BATCH_ACTION = "bulk_reverse"


def operation_id_of(narration: str) -> str | None:
    """The marker's operation id, or None. Re-exported so the batch's one
    assumption about narration format is nameable and testable here."""
    return operation_id_in(narration)


class VoucherState(StrEnum):
    """Where one voucher in the batch got to. Eight states, owner-specified.

    `REQUEST_SENT` is the one that only ever exists mid-flight. It is written
    durably BEFORE the reversal is attempted, so a process killed between the
    request and the answer leaves that row as its last word — and a resume
    reads it as exactly what it is: an outcome nobody can name. Without it, a
    crash mid-voucher is indistinguishable from a voucher never tried.
    """

    NOT_ATTEMPTED = "not_attempted"
    PRECHECK_REFUSED = "precheck_refused"
    EXPLICIT_REJECTION = "explicit_rejection"
    REQUEST_SENT = "request_sent"
    REVERSED_VERIFIED = "reversed_verified"
    UNKNOWN_OUTCOME = "unknown_outcome"
    WRONG_MOVEMENT = "wrong_movement"
    READBACK_FAILED = "readback_failed"


class BatchState(StrEnum):
    """Where the batch as a whole got to. Seven states, owner-specified."""

    PREVIEW = "preview"
    CONFIRMED = "confirmed"
    REVERSING = "reversing"
    PARTIAL_FAILURE = "partial_failure"
    UNKNOWN_OUTCOME = "unknown_outcome"
    CRITICAL_FAILURE = "critical_failure"
    COMPLETED = "completed"


#: Which batch state each unresolved voucher state forces, worst first. Written
#: as ordered data rather than an if/elif chain so the precedence is one thing
#: to read and one thing to change: a wrong movement outranks an unknown
#: outcome, which outranks anything merely refused.
_ESCALATION: tuple[tuple[VoucherState, BatchState], ...] = (
    (VoucherState.WRONG_MOVEMENT, BatchState.CRITICAL_FAILURE),
    (VoucherState.UNKNOWN_OUTCOME, BatchState.UNKNOWN_OUTCOME),
    (VoucherState.REQUEST_SENT, BatchState.UNKNOWN_OUTCOME),
    (VoucherState.READBACK_FAILED, BatchState.PARTIAL_FAILURE),
    (VoucherState.PRECHECK_REFUSED, BatchState.PARTIAL_FAILURE),
    (VoucherState.EXPLICIT_REJECTION, BatchState.PARTIAL_FAILURE),
    (VoucherState.NOT_ATTEMPTED, BatchState.PARTIAL_FAILURE),
)

#: The states an explicit resume may retry, and the owner's reason for each:
#:
#:   NOT_ATTEMPTED       never tried; the batch stopped before reaching it
#:   PRECHECK_REFUSED    "allow explicit resume after the local cause is
#:                       corrected" — no request was ever sent, so nothing is
#:                       uncertain about Tally's state
#:   EXPLICIT_REJECTION  "do not blindly retry; require explicit resume" —
#:                       Tally said clearly that it did not happen, so retrying
#:                       after a person has looked is not blind
#:
#: Everything else is deliberately absent. UNKNOWN_OUTCOME must be reconciled
#: into one of these first; WRONG_MOVEMENT is CRITICAL and cannot be resumed at
#: all; READBACK_FAILED needs a person; REVERSED_VERIFIED is done.
RETRYABLE = (
    VoucherState.NOT_ATTEMPTED,
    VoucherState.PRECHECK_REFUSED,
    VoucherState.EXPLICIT_REJECTION,
)


@dataclass(frozen=True)
class VoucherOutcome:
    """One voucher's place in the batch, and the evidence for it."""

    operation_id: str
    state: VoucherState
    detail: str = ""
    #: Ledger -> paise, AFTER minus BEFORE. Empty unless something moved.
    moved: Mapping[str, int] = field(default_factory=dict[str, int])
    #: Whether this outcome's movement was MEASURED. False for anything settled
    #: by reconciliation: a read can prove the voucher is gone, but the trial
    #: balance moved while nobody was watching and no snapshot brackets it.
    #: The batch must then decline to claim conservation rather than claim it
    #: wrongly in either direction.
    measured: bool = True


@dataclass(frozen=True)
class Batch:
    """A bulk reversal, at whatever point it has reached.

    Frozen: every transition returns a new one, so a partial result cannot be
    edited into looking finished.
    """

    batch_id: str
    company: str
    state: BatchState
    #: The trial balance before anything was attempted, in exact paise.
    baseline: Mapping[str, int]
    outcomes: tuple[VoucherOutcome, ...] = ()
    final: Mapping[str, int] | None = None
    detail: str = ""
    reconciled: bool = False

    @property
    def expected_final(self) -> dict[str, int]:
        """The baseline plus every movement this batch verified, in paise."""
        expected = dict(self.baseline)
        for outcome in self.outcomes:
            for ledger, delta in outcome.moved.items():
                expected[ledger] = expected.get(ledger, 0) + delta
        return {ledger: paise for ledger, paise in expected.items() if paise != 0}

    @property
    def accounted(self) -> bool | None:
        """Whether every paise that moved is one this batch can account for.

        NOT "did the books come back to where they started". That would be the
        wrong question and it was the first thing this class got wrong:
        `baseline` is the trial balance immediately BEFORE the reversal, which
        already contains the vouchers about to be removed. A successful batch is
        therefore SUPPOSED to end somewhere else, and comparing the two would
        report failure on every clean run.

        The batch's own conservation law is narrower and stronger:

            final == baseline + the sum of the movements it verified

        A clean run satisfies it. So does a batch that stopped at voucher 4
        without moving anything — which is what makes it useful, because it
        proves the FAILED voucher moved nothing. It fails only when the books
        moved by an amount nobody in this batch accounted for.

        Returning to a pre-posting balance is a different claim, belongs to
        whoever posted the vouchers, and is asserted by the N = 10 harness.
        """
        if self.final is None:
            return None
        if any(not o.measured for o in self.outcomes):
            # A reconciled voucher moved the books between two snapshots that
            # nobody took. UNKNOWN is the honest answer; asserting either way
            # would be inventing a measurement.
            return None
        return dict(self.final) == self.expected_final

    def in_state(self, *states: VoucherState) -> tuple[VoucherOutcome, ...]:
        return tuple(o for o in self.outcomes if o.state in states)


def _new_batch_id() -> str:
    return f"bulk_{uuid.uuid4().hex[:12]}"


def _record(
    log: pipeline.ActionLogSink | None,
    *,
    company_key: str,
    run_id: str,
    batch_id: str,
    outcome: VoucherOutcome,
    backend: str,
) -> None:
    """One durable row per voucher transition. Written before the next attempt.

    A summary written at the end is precisely the row that does not exist when
    the process dies, so there is no summary row: the log IS the state, one
    line at a time.
    """
    if log is None:
        return
    log.record_action(
        ActionLog(
            ts=datetime.datetime.now(datetime.UTC),
            action=BATCH_ACTION,
            # Normalised HERE, not trusted from the caller. `MemoryStore.actions`
            # normalises before reading, so a caller passing a display name
            # would write rows nothing could ever read back — which is exactly
            # what happened on the first Phase 5B run: ten rows written, zero
            # found after the restart. Idempotent, so a caller that already
            # normalised (the web app, via memory.identity.key) is unaffected.
            company_key=normalise_company(company_key),
            outcome=outcome.state.value,
            reason=outcome.detail or f"batch {batch_id}: {outcome.state.value}",
            run_id=run_id,
            backend=backend,
            operation_id=outcome.operation_id,
            detail=f"batch {batch_id}; moved {dict(outcome.moved)}",
        )
    )


# ---- preview: say exactly what would be touched, and refuse early -----------


def preview(client: TallyClient, company: str, *, batch_id: str = "") -> Batch:
    """The candidate set, and every refusal that can be made without writing.

    Three refusals happen here rather than mid-batch, because a batch that
    stops halfway for a reason knowable up front has already done avoidable
    damage:

        no recorded backup          nothing is touched at all
        a candidate with no marker  reversal targets the operation id and
                                    nothing else; guessing by amount or
                                    narration is what correction C4 forbids
        (a candidate that is not ours cannot appear — `list_our_vouchers` is
        the marker filter, and the register control test proves it excludes
        hand-typed vouchers)

    The candidate list is FROZEN into the batch here. `execute` never re-reads
    it: a voucher posted by something else between preview and confirmation
    would otherwise be swept up by a batch the person never saw.
    """
    if not client.backed_up(company):
        from accountant.tallyio.client import CompanyNotBackedUp

        raise CompanyNotBackedUp(
            f"{company!r} has no recorded backup; refusing to reverse in bulk"
        )

    candidates = client.list_our_vouchers(company)
    outcomes: list[VoucherOutcome] = []
    for voucher in candidates:
        op = operation_id_of(voucher.narration)
        if op is None:
            raise ValueError(
                f"a voucher in {company!r} is listed as ours but carries no "
                f"operation id (narration {voucher.narration!r}); reversal "
                "targets the operation id and never an amount or a narration"
            )
        outcomes.append(
            VoucherOutcome(operation_id=op, state=VoucherState.NOT_ATTEMPTED)
        )

    return Batch(
        batch_id=batch_id or _new_batch_id(),
        company=company,
        state=BatchState.PREVIEW,
        baseline=client.trial_balance(company),
        outcomes=tuple(outcomes),
        detail=(
            f"{len(outcomes)} voucher(s) of ours in {company!r}: "
            + ", ".join(o.operation_id for o in outcomes)
            if outcomes
            else f"nothing of ours in {company!r}"
        ),
    )


def confirm(batch: Batch) -> Batch:
    """The explicit confirmation. A preview is a question, not an order."""
    if batch.state is not BatchState.PREVIEW:
        raise ValueError(
            f"batch {batch.batch_id} is {batch.state.value}, not a preview; "
            "only a preview can be confirmed"
        )
    return replace(batch, state=BatchState.CONFIRMED)


# ---- execute ----------------------------------------------------------------


def _classify(client: TallyClient, company: str, operation_id: str) -> VoucherOutcome:
    """Reverse one voucher and name what happened. Never raises.

    Every branch maps to exactly one of the eight states, and the mapping is
    the whole safety argument of this module:

        reversed and gone          REVERSED_VERIFIED
        reversed, still findable   READBACK_FAILED  — the books moved right and
                                   the voucher is still there; something is
                                   wrong that a trial balance cannot see
        not found before the send  PRECHECK_REFUSED — no request was made
        Tally refused              EXPLICIT_REJECTION
        books and answer disagree  WRONG_MOVEMENT
        anything else at all       UNKNOWN_OUTCOME
    """
    try:
        result = pipeline.reverse_operation(client, company, operation_id)
    except pipeline.ReversalMismatch as exc:
        return VoucherOutcome(operation_id, VoucherState.WRONG_MOVEMENT, str(exc))
    except BaseException as exc:  # see UNKNOWN_OUTCOME below
        # BaseException on purpose, matching `pipeline.post`. A KeyboardInterrupt
        # or a SystemExit arriving mid-reversal leaves exactly the uncertainty
        # this state exists to record, and catching only Exception would let the
        # two most likely interruptions of a manual run go unclassified.
        return VoucherOutcome(
            operation_id,
            VoucherState.UNKNOWN_OUTCOME,
            f"{type(exc).__name__}: {exc}",
        )

    if not result.reversed_:
        if "carries" in result.detail:
            # `reverse_operation` read first and found nothing, so no delete was
            # ever sent. Not a rejection: there was no request to reject.
            return VoucherOutcome(
                operation_id, VoucherState.PRECHECK_REFUSED, result.detail
            )
        return VoucherOutcome(
            operation_id, VoucherState.EXPLICIT_REJECTION, result.detail
        )

    try:
        still_there = client.read_by_operation_id(company, operation_id)
    except BaseException as exc:
        return VoucherOutcome(
            operation_id,
            VoucherState.UNKNOWN_OUTCOME,
            f"reversed, then the confirming read failed: {type(exc).__name__}: {exc}",
            result.moved,
        )
    if still_there is not None:
        return VoucherOutcome(
            operation_id,
            VoucherState.READBACK_FAILED,
            (
                f"the trial balance moved by {result.moved} as it should, and "
                f"operation {operation_id!r} is STILL findable in {company!r}. "
                "A person has to look before anything else is reversed."
            ),
            result.moved,
        )

    return VoucherOutcome(
        operation_id, VoucherState.REVERSED_VERIFIED, result.detail, result.moved
    )


def _settle(
    batch: Batch, outcomes: Sequence[VoucherOutcome], final: Mapping[str, int]
) -> Batch:
    """The batch state implied by the voucher states. Worst wins.

    A batch containing one unresolved voucher can never be COMPLETED — that is
    the single rule here that outranks every other reporting choice.
    """
    present = {o.state for o in outcomes}
    for voucher_state, batch_state in _ESCALATION:
        if voucher_state in present:
            worst = batch_state
            break
    else:
        worst = BatchState.COMPLETED

    settled = replace(batch, state=worst, outcomes=tuple(outcomes), final=final)
    if worst is BatchState.COMPLETED and settled.accounted is False:
        # Every voucher reported reversed and verified, and the books still
        # moved by something nobody here can account for. Each per-voucher check
        # passed, so this is a whole-batch arithmetic failure — the shape a
        # concurrent write leaves — and it is exactly as serious as a single
        # wrong movement.
        #
        # Only escalated from COMPLETED. On an UNKNOWN_OUTCOME batch an
        # unaccounted movement is the expected shape of the unknown itself, and
        # reconciliation is what settles it; calling that CRITICAL would hide
        # the one state that has a safe recovery path.
        return replace(
            settled,
            state=BatchState.CRITICAL_FAILURE,
            detail=(
                f"every voucher reported reversed and the trial balance is "
                f"{dict(final)} where the movements this batch verified account "
                f"for {settled.expected_final}"
            ),
        )
    return replace(settled, detail=_summary(worst, outcomes, batch))


def _summary(
    state: BatchState, outcomes: Sequence[VoucherOutcome], batch: Batch
) -> str:
    if not outcomes:
        # Stated outright rather than `batch.detail or <this>`. The fallback was
        # unreachable — `preview` already writes this exact sentence for an
        # empty candidate list and nothing else produces an empty detail — so
        # the `or` was a branch no test could take and no mutant could kill.
        # An unkillable line is not a safe line, it is an unmeasured one.
        return f"nothing of ours in {batch.company!r}"
    done = sum(1 for o in outcomes if o.state is VoucherState.REVERSED_VERIFIED)
    untried = sum(1 for o in outcomes if o.state is VoucherState.NOT_ATTEMPTED)
    return (
        f"{state.value}: {done} of {len(outcomes)} reversed and verified, "
        f"{untried} not attempted"
    )


def execute(
    batch: Batch,
    client: TallyClient,
    *,
    log: pipeline.ActionLogSink | None = None,
    company_key: str = "",
    run_id: str = "",
) -> Batch:
    """Run a confirmed batch, stopping at the first voucher that is not clean."""
    if batch.state is not BatchState.CONFIRMED:
        raise ValueError(
            f"batch {batch.batch_id} has not been confirmed (state "
            f"{batch.state.value}); nothing is reversed without an explicit "
            "confirmation of the exact candidate list"
        )
    return _drive(batch, client, log=log, company_key=company_key, run_id=run_id)


def _drive(
    batch: Batch,
    client: TallyClient,
    *,
    log: pipeline.ActionLogSink | None,
    company_key: str,
    run_id: str,
) -> Batch:
    """The loop. Shared by `execute` and `resume` so they cannot drift."""
    backend = type(client).__name__
    outcomes = list(batch.outcomes)
    stopped = False

    for i, outcome in enumerate(outcomes):
        if outcome.state not in RETRYABLE:
            continue  # done, or a question for a person; never re-touched
        if stopped:
            continue

        in_flight = replace(outcome, state=VoucherState.REQUEST_SENT, detail="")
        outcomes[i] = in_flight
        _record(
            log,
            company_key=company_key,
            run_id=run_id,
            batch_id=batch.batch_id,
            outcome=in_flight,
            backend=backend,
        )

        settled = _classify(client, batch.company, outcome.operation_id)
        outcomes[i] = settled
        _record(
            log,
            company_key=company_key,
            run_id=run_id,
            batch_id=batch.batch_id,
            outcome=settled,
            backend=backend,
        )

        if settled.state is not VoucherState.REVERSED_VERIFIED:
            stopped = True

    return _settle(batch, outcomes, client.trial_balance(batch.company))


# ---- reconcile and resume ----------------------------------------------------


def reconcile(batch: Batch, client: TallyClient) -> Batch:
    """Read-only. Turn every unknown into a fact, and write nothing.

    This is what makes a later retry not blind. An `UNKNOWN_OUTCOME` means the
    request may or may not have reached Tally; a single read settles which:

        the voucher is gone         it DID land — REVERSED_VERIFIED
        the voucher is still there  it did not — NOT_ATTEMPTED, retryable

    `REQUEST_SENT` is treated identically, because a row left in that state is
    a process that died mid-voucher and is exactly as unknown.

    Nothing is reversed here and nothing is written. If the read itself fails,
    the voucher stays unknown — a reconciliation that cannot read has not
    reconciled anything.
    """
    settled: list[VoucherOutcome] = []
    for outcome in batch.outcomes:
        if outcome.state not in (
            VoucherState.UNKNOWN_OUTCOME,
            VoucherState.REQUEST_SENT,
        ):
            settled.append(outcome)
            continue
        try:
            found = client.read_by_operation_id(batch.company, outcome.operation_id)
        except BaseException as exc:
            settled.append(
                replace(
                    outcome,
                    state=VoucherState.UNKNOWN_OUTCOME,
                    detail=f"reconciliation could not read Tally: "
                    f"{type(exc).__name__}: {exc}",
                )
            )
            continue
        if found is None:
            settled.append(
                replace(
                    outcome,
                    state=VoucherState.REVERSED_VERIFIED,
                    measured=False,
                    detail=(
                        f"reconciled: operation {outcome.operation_id!r} is no "
                        f"longer in Tally, so the reversal did land and only "
                        "the answer was lost"
                    ),
                )
            )
        else:
            settled.append(
                replace(
                    outcome,
                    state=VoucherState.NOT_ATTEMPTED,
                    detail=(
                        f"reconciled: operation {outcome.operation_id!r} is "
                        "still in Tally, so the reversal did not land and "
                        "retrying it is no longer blind"
                    ),
                )
            )

    return replace(
        _settle(batch, settled, client.trial_balance(batch.company)),
        reconciled=True,
    )


def resume(
    batch: Batch,
    client: TallyClient,
    *,
    approved: bool,
    log: pipeline.ActionLogSink | None = None,
    company_key: str = "",
    run_id: str = "",
) -> Batch:
    """Finish the OUTSTANDING cleanup. Never re-touch what is already done.

    Two gates, and they are different questions:

        reconciled   has every unknown been turned into a fact by a read?
        approved     has a person said, after seeing those facts, go on?

    A `CRITICAL_FAILURE` cannot be resumed at all. WRONG_MOVEMENT means Tally's
    answer and Tally's books disagree, and a program that carries on writing
    into that is making a bad situation larger.
    """
    if batch.state is BatchState.CRITICAL_FAILURE:
        raise ValueError(
            f"batch {batch.batch_id} is CRITICAL_FAILURE and cannot be resumed: "
            f"{batch.detail}. A person has to reconcile the books by hand."
        )
    if not batch.reconciled:
        raise ValueError(
            f"batch {batch.batch_id} has not been reconciled; call reconcile() "
            "first so every unknown outcome is settled by a read before "
            "anything else is written"
        )
    if not approved:
        raise ValueError(
            f"batch {batch.batch_id} needs explicit approval to resume; "
            "reconciling is a read and resuming is a write"
        )
    if not batch.in_state(*RETRYABLE):
        return batch
    return replace(
        _drive(batch, client, log=log, company_key=company_key, run_id=run_id),
        reconciled=True,
    )
