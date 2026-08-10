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

Owner decision D-03, 2026-08-10, extends that to the RESUME: while any voucher
is UNKNOWN_OUTCOME the whole batch is refused, not just that voucher. Safety
beats partial cleanup — six known vouchers are never deleted while one voucher's
fate is unknown. See `UNRESOLVED` and `resume` below.

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

THE FULL HISTORY, AND WHAT ITS `actor` FIELD IS WORTH
-----------------------------------------------------
Owner decision Q8 = A, 2026-08-10. Every transition of a voucher state or a
batch state is one durable event carrying seven fields:

    previous state · new state · reason · actor · timestamp
    company/document scope · evidence

`previous state` and `new state` are the state NAMES from `VoucherState` and
`BatchState`. That is what makes the history reconstructable: the events for one
scope form a chain from a known starting state to the state the object is in
now, and a transition that was never recorded shows up as a break in that chain
rather than as nothing at all. `audit` below is the function that looks.

The actor has exactly two values and a hard limit:

    accountant_dad   the system did it
    operator         somebody answered it through the UI

    authenticated user identity = NOT_IMPLEMENTED
    actor provenance            = coarse-grained system/operator

**`operator` IS NOT AN AUTHENTICATED USER IDENTITY.** It records that a person
was in the loop, not which person. There is no login and no session behind it,
because the decision that asked for these labels also said to add no
authentication dependency, and `dependencies = []` still stands. Building one
is not an agent's call:

    OWNER_DECISION_REQUIRED: approve an authenticated identity subsystem

That is H-05.

Rows written before this existed keep SQL NULL in the two new columns and read
back as `NOT_RECORDED`. They are NEVER back-filled with `accountant_dad`. A gap
that says it is a gap can be reasoned about; a gap filled with a plausible guess
is a false record, and a false audit record is worse than no audit record.
"""

from __future__ import annotations

import datetime
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum

from accountant import pipeline
from accountant.memory.identity import normalise_company
from accountant.schema import NOT_RECORDED, ActionLog, Actor
from accountant.tallyio.client import TallyClient, operation_id_in

#: The action name every durable row of a batch carries, so the log can be
#: filtered to "what did the bulk reversal do" without parsing prose.
BATCH_ACTION = "bulk_reverse"

#: The same, for the transitions of the BATCH rather than of one voucher.
#: A separate name rather than a flag on `BATCH_ACTION`, because every existing
#: reader of this log filters on the action word and expects one row shape:
#: `action == BATCH_ACTION` means "one voucher, identified by operation id".
BATCH_STATE_ACTION = "bulk_reverse_batch"

#: Both, for a reader that wants the whole history of a batch.
REVERSAL_ACTIONS = (BATCH_ACTION, BATCH_STATE_ACTION)


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

#: The states that mean nobody can name this voucher's fate. A batch holding one
#: of them cannot be resumed AT ALL — owner decision D-03, 2026-08-10:
#:
#:     Refuse the whole batch when any voucher has UNKNOWN_OUTCOME. Safety beats
#:     partial cleanup. Never delete six known vouchers while one voucher's fate
#:     is unknown.
#:
#: `REQUEST_SENT` sits beside `UNKNOWN_OUTCOME` because `reconcile` already
#: treats the two identically: a row whose last word is REQUEST_SENT is a
#: process that died mid-voucher, and that is exactly as unknown as a dropped
#: connection.
#:
#: This is NOT the complement of `RETRYABLE`, and the two answer different
#: questions. `RETRYABLE` asks "may this VOUCHER be attempted again"; this asks
#: "may the BATCH proceed at all". Answering only the first — skip the unknown
#: voucher, clean up the rest — is exactly the defect: it left nine of ten
#: vouchers deleted from a company where one voucher's fate was unknown.
UNRESOLVED = (
    VoucherState.UNKNOWN_OUTCOME,
    VoucherState.REQUEST_SENT,
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

    @property
    def unresolved(self) -> tuple[VoucherOutcome, ...]:
        """Every voucher whose fate no read has named yet.

        Empty is the precondition for a resume, and it is the ONE question
        `resume` asks before it writes anything. Returned as the outcomes rather
        than a count so the refusal can name each one by operation id: "something
        is unresolved" sends a person to read ten rows to find out which.
        """
        return self.in_state(*UNRESOLVED)


#: The state every candidate is in when `preview` builds it, and therefore the
#: state a voucher's reconstructed chain must start from.
INITIAL_VOUCHER_STATE = VoucherState.NOT_ATTEMPTED

#: The state every batch is in when `preview` returns it, and therefore the
#: state a batch's reconstructed chain must start from.
INITIAL_BATCH_STATE = BatchState.PREVIEW

#: The seven, named once. The order is the owner's.
SEVEN_FIELDS: tuple[str, ...] = (
    "previous state",
    "new state",
    "reason",
    "actor",
    "timestamp",
    "scope",
    "evidence",
)


@dataclass(frozen=True)
class ReversalEvent:
    """One transition, with everything needed to believe it later.

    Seven fields, owner decision Q8 = A. Constructed on the way out (by
    `_record` and `_record_batch`, which refuse an incomplete one) and again on
    the way in (by `from_action_log`, which reports incompleteness rather than
    refusing, because a row already written cannot be un-written by objecting to
    it).

    `document` is the operation id for a voucher transition and the batch id for
    a batch transition. Together with `company_key` it is the "company/document
    scope" of the seven, and it is also the key the chain is rebuilt on.
    """

    previous_state: str
    new_state: str
    reason: str
    actor: str
    ts: datetime.datetime | None
    company_key: str
    document: str
    evidence: str
    batch_id: str = ""
    action: str = BATCH_ACTION
    #: Carried through to the row but not part of the seven: `run_id` says which
    #: process wrote it and `backend` which Tally it is evidence about.
    run_id: str = ""
    backend: str = ""

    @property
    def scope(self) -> tuple[str, str]:
        """The chain key: which batch, and which thing inside it."""
        return (self.batch_id, self.document)

    @property
    def identity(self) -> tuple[object, ...]:
        """The seven fields as one comparable thing.

        Two events agreeing on all seven are the same record; anything else is a
        different record. This is what "0 overwritten" is measured with, and it
        deliberately leaves out `run_id` and `backend` — an event whose seven
        fields survived is the event that was written, whatever else moved.
        """
        return (
            self.previous_state,
            self.new_state,
            self.reason,
            self.actor,
            self.ts,
            self.company_key,
            self.document,
            self.evidence,
        )

    @property
    def missing(self) -> tuple[str, ...]:
        """Which of the seven this event does not carry. Empty is the goal.

        `NOT_RECORDED` counts as MISSING here and that is the point of the
        marker: it is an honest description of a row, not a value that satisfies
        the requirement. A legacy row reports two missing fields; it does not
        report an actor of `accountant_dad`.
        """
        absent: list[str] = []
        if not _present(self.previous_state):
            absent.append("previous state")
        if not _present(self.new_state):
            absent.append("new state")
        if not _present(self.reason):
            absent.append("reason")
        if self.actor not in tuple(Actor):
            absent.append("actor")
        if self.ts is None:
            absent.append("timestamp")
        if not _present(self.company_key) or not _present(self.document):
            absent.append("scope")
        if not _present(self.evidence):
            absent.append("evidence")
        return tuple(absent)

    @property
    def complete(self) -> bool:
        return not self.missing

    def demand_complete(self) -> None:
        """Refuse to write an event that is not all seven fields.

        Called on the WRITE path only. An event missing a field is not a
        slightly-worse record, it is a record that cannot answer the question
        the log exists for, and writing it anyway puts a hole in the chain that
        looks like data.
        """
        if self.missing:
            raise ValueError(
                f"refusing to record a reversal event for "
                f"{self.company_key!r}/{self.document!r} "
                f"({self.previous_state} -> {self.new_state}) without "
                + ", ".join(self.missing)
                + "; every reversal event carries all seven of "
                + ", ".join(SEVEN_FIELDS)
            )

    def to_action_log(self) -> ActionLog:
        """The durable row. `ts` is never None on this path — `demand_complete`
        has already refused that — so the fallback below is unreachable by
        construction and is written as an assertion rather than an `or`."""
        if self.ts is None:  # pragma: no cover - demand_complete refuses it
            raise ValueError("an event with no timestamp is not recordable")
        return ActionLog(
            ts=self.ts,
            action=self.action,
            company_key=self.company_key,
            outcome=self.new_state,
            reason=self.reason,
            run_id=self.run_id,
            backend=self.backend,
            operation_id=self.document if self.action == BATCH_ACTION else "",
            detail=self.evidence,
            actor=self.actor,
            previous_state=self.previous_state,
            batch_id=self.batch_id,
        )

    @classmethod
    def from_action_log(cls, row: ActionLog) -> ReversalEvent:
        """Read one row back as an event, reporting exactly what it carries.

        A row written before the two columns existed comes back with `actor` and
        `previous_state` as `NOT_RECORDED`, so `missing` names them. No default
        is substituted anywhere on this path.
        """
        return cls(
            previous_state=row.previous_state,
            new_state=row.outcome,
            reason=row.reason,
            actor=row.actor,
            ts=row.ts,
            company_key=row.company_key,
            document=(row.operation_id if row.action == BATCH_ACTION else row.batch_id),
            evidence=row.detail,
            batch_id=row.batch_id,
            action=row.action,
            run_id=row.run_id,
            backend=row.backend,
        )


def _present(value: str) -> bool:
    """A field counts as carried only if something was actually written in it.
    `NOT_RECORDED` is the marker for "nobody wrote this down" and never passes.
    """
    return bool(value.strip()) and value != NOT_RECORDED


def _new_batch_id() -> str:
    return f"bulk_{uuid.uuid4().hex[:12]}"


def _voucher_evidence(batch_id: str, outcome: VoucherOutcome) -> str:
    return f"batch {batch_id}; moved {dict(outcome.moved)}"


def _batch_evidence(batch: Batch) -> str:
    """What the batch looked like at the moment of the transition.

    Built from the outcomes rather than from `batch.detail`, so it is non-empty
    for every batch including one whose detail nobody set, and so there is no
    `or` fallback that no test can take.
    """
    counts: dict[str, int] = {}
    for outcome in batch.outcomes:
        counts[outcome.state.value] = counts.get(outcome.state.value, 0) + 1
    return f"batch {batch.batch_id}; {len(batch.outcomes)} voucher(s); {counts}"


def _record(
    log: pipeline.ActionLogSink | None,
    *,
    company_key: str,
    run_id: str,
    batch_id: str,
    previous: VoucherState,
    outcome: VoucherOutcome,
    actor: Actor,
    backend: str,
) -> None:
    """One durable row per voucher transition. Written before the next attempt.

    A summary written at the end is precisely the row that does not exist when
    the process dies, so there is no summary row: the log IS the state, one
    line at a time.
    """
    if log is None:
        return
    event = ReversalEvent(
        previous_state=previous.value,
        new_state=outcome.state.value,
        reason=outcome.detail or f"batch {batch_id}: {outcome.state.value}",
        actor=actor,
        ts=datetime.datetime.now(datetime.UTC),
        # Normalised HERE, not trusted from the caller. `MemoryStore.actions`
        # normalises before reading, so a caller passing a display name
        # would write rows nothing could ever read back — which is exactly
        # what happened on the first Phase 5B run: ten rows written, zero
        # found after the restart. Idempotent, so a caller that already
        # normalised (the web app, via memory.identity.key) is unaffected.
        company_key=normalise_company(company_key),
        document=outcome.operation_id,
        evidence=_voucher_evidence(batch_id, outcome),
        batch_id=batch_id,
        action=BATCH_ACTION,
        run_id=run_id,
        backend=backend,
    )
    event.demand_complete()
    log.record_action(event.to_action_log())


def _record_batch(
    log: pipeline.ActionLogSink | None,
    *,
    company_key: str,
    run_id: str,
    previous: BatchState,
    batch: Batch,
    actor: Actor,
    backend: str,
) -> None:
    """One durable row per BATCH transition, on the same terms.

    Nothing is written when the state did not change, because a non-transition
    is not an event and a log full of them hides the ones that are.
    """
    if log is None or previous is batch.state:
        return
    event = ReversalEvent(
        previous_state=previous.value,
        new_state=batch.state.value,
        reason=(
            f"batch {batch.batch_id} for {batch.company!r} moved from "
            f"{previous.value} to {batch.state.value}: {batch.detail}"
        ),
        actor=actor,
        ts=datetime.datetime.now(datetime.UTC),
        company_key=normalise_company(company_key),
        document=batch.batch_id,
        evidence=_batch_evidence(batch),
        batch_id=batch.batch_id,
        action=BATCH_STATE_ACTION,
        run_id=run_id,
        backend=backend,
    )
    event.demand_complete()
    log.record_action(event.to_action_log())


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


def confirm(
    batch: Batch,
    *,
    log: pipeline.ActionLogSink | None = None,
    company_key: str = "",
    run_id: str = "",
    backend: str = "",
) -> Batch:
    """The explicit confirmation. A preview is a question, not an order.

    The one transition in this module whose actor is `operator`: a preview
    becomes an order because a person said so through the UI. It is recorded
    here rather than at the start of `execute`, because a confirmation that is
    never executed still happened and the log has to be able to say so.

    `backend` defaults to empty and says so honestly — confirming touches no
    Tally, so there is no backend this row is evidence about. Every logging
    argument is keyword-only with a default, so the many callers that confirm
    without a log are unchanged.
    """
    if batch.state is not BatchState.PREVIEW:
        raise ValueError(
            f"batch {batch.batch_id} is {batch.state.value}, not a preview; "
            "only a preview can be confirmed"
        )
    confirmed = replace(batch, state=BatchState.CONFIRMED)
    _record_batch(
        log,
        company_key=company_key,
        run_id=run_id,
        previous=batch.state,
        batch=confirmed,
        actor=Actor.OPERATOR,
        backend=backend,
    )
    return confirmed


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
    return _drive(
        batch,
        client,
        log=log,
        company_key=company_key,
        run_id=run_id,
        actor=Actor.ACCOUNTANT_DAD,
    )


def _drive(
    batch: Batch,
    client: TallyClient,
    *,
    log: pipeline.ActionLogSink | None,
    company_key: str,
    run_id: str,
    actor: Actor,
) -> Batch:
    """The loop. Shared by `execute` and `resume` so they cannot drift.

    `REVERSING` is entered for real here rather than being a name in the enum
    that nothing ever sets. The owner specified seven batch states; a state the
    code never occupies cannot appear in the history, and a history that jumps
    CONFIRMED -> COMPLETED is missing the interval in which the damage, if any,
    was done.

    `actor` belongs to the transition INTO `REVERSING` and to nothing else. On
    `execute` it is the system starting the work it was confirmed to do; on
    `resume` it is the operator, because `approved=True` is a person's answer
    after reading the refusal. Every per-voucher transition below is the system,
    unconditionally: no person chose them one at a time.
    """
    backend = type(client).__name__
    driving = replace(batch, state=BatchState.REVERSING)
    _record_batch(
        log,
        company_key=company_key,
        run_id=run_id,
        previous=batch.state,
        batch=driving,
        actor=actor,
        backend=backend,
    )
    outcomes = list(driving.outcomes)
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
            previous=outcome.state,
            outcome=in_flight,
            actor=Actor.ACCOUNTANT_DAD,
            backend=backend,
        )

        settled = _classify(client, batch.company, outcome.operation_id)
        outcomes[i] = settled
        _record(
            log,
            company_key=company_key,
            run_id=run_id,
            batch_id=batch.batch_id,
            previous=VoucherState.REQUEST_SENT,
            outcome=settled,
            actor=Actor.ACCOUNTANT_DAD,
            backend=backend,
        )

        if settled.state is not VoucherState.REVERSED_VERIFIED:
            stopped = True

    rested = _settle(driving, outcomes, client.trial_balance(batch.company))
    _record_batch(
        log,
        company_key=company_key,
        run_id=run_id,
        previous=driving.state,
        batch=rested,
        actor=Actor.ACCOUNTANT_DAD,
        backend=backend,
    )
    return rested


# ---- reconcile and resume ----------------------------------------------------


def reconcile(
    batch: Batch,
    client: TallyClient,
    *,
    log: pipeline.ActionLogSink | None = None,
    company_key: str = "",
    run_id: str = "",
) -> Batch:
    """Read-only against TALLY. Turn every unknown into a fact.

    This is what makes a later retry not blind. An `UNKNOWN_OUTCOME` means the
    request may or may not have reached Tally; a single read settles which:

        the voucher is gone         it DID land — REVERSED_VERIFIED
        the voucher is still there  it did not — NOT_ATTEMPTED, retryable

    `REQUEST_SENT` is treated identically, because a row left in that state is
    a process that died mid-voucher and is exactly as unknown.

    NOTHING IS REVERSED HERE AND NOTHING IS WRITTEN INTO THE CUSTOMER'S BOOKS.
    What IS written, since Phase 8 PR-5, is our own audit trail: settling an
    unknown into a fact CHANGES A VOUCHER'S STATE, and a state change with no
    event is precisely the hole the full history exists to close. A voucher that
    goes UNKNOWN_OUTCOME -> NOT_ATTEMPTED with nothing recorded reads afterwards
    as though it were never attempted at all. The events are appended to
    `action_log`; the books are untouched, and the tests that count deletes and
    compare trial balances still measure exactly that.

    If the read itself fails, the voucher stays unknown — a reconciliation that
    cannot read has not reconciled anything, and it records no transition
    because none occurred.
    """
    backend = type(client).__name__
    settled: list[VoucherOutcome] = []
    previous_states: dict[str, VoucherState] = {
        o.operation_id: o.state for o in batch.outcomes
    }
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

    # `reconciled` is DERIVED from what is left unresolved, never asserted.
    #
    # Defect D3, answered by owner decision D-03 on 2026-08-10. The flag used to
    # be `True` on every return path, including the one where every read raised,
    # which contradicted two things this module says about itself: this
    # function's docstring ("a reconciliation that cannot read has not
    # reconciled anything") and `resume`'s gate message ("every unknown outcome
    # is settled by a read before anything else is written"). Measured: after a
    # reconcile in which every read failed, an approved resume deleted six more
    # vouchers, leaving nine of ten gone from a company where one voucher's fate
    # was unknown.
    #
    # The rejected alternative was to keep going and skip the unknown voucher so
    # the rest of the cleanup could finish. The owner refused it outright:
    # safety beats partial cleanup. So a pass that leaves anything unresolved
    # reports itself as what it is — a reconciliation that did not reconcile —
    # and `resume` refuses the whole batch on the strength of that.
    for outcome in settled:
        was = previous_states[outcome.operation_id]
        if outcome.state is was:
            continue  # read, and nothing changed; not a transition
        _record(
            log,
            company_key=company_key,
            run_id=run_id,
            batch_id=batch.batch_id,
            previous=was,
            outcome=outcome,
            actor=Actor.ACCOUNTANT_DAD,
            backend=backend,
        )

    reconciled_batch = _settle(batch, settled, client.trial_balance(batch.company))
    _record_batch(
        log,
        company_key=company_key,
        run_id=run_id,
        previous=batch.state,
        batch=reconciled_batch,
        actor=Actor.ACCOUNTANT_DAD,
        backend=backend,
    )
    return replace(reconciled_batch, reconciled=not reconciled_batch.unresolved)


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

    Three gates, and they are different questions:

        unresolved   does any voucher still have a fate nobody can name?
        reconciled   was a reconciliation run at all?
        approved     has a person said, after seeing those facts, go on?

    A `CRITICAL_FAILURE` cannot be resumed at all. WRONG_MOVEMENT means Tally's
    answer and Tally's books disagree, and a program that carries on writing
    into that is making a bad situation larger.

    THE FIRST GATE IS FAIL-CLOSED FOR THE WHOLE BATCH — owner decision D-03,
    2026-08-10. One UNKNOWN_OUTCOME stops all of it, including the vouchers
    whose fate IS known and whose cleanup is merely outstanding. Skipping the
    unknown one and finishing the rest is defensible right up to the moment you
    price it: it deletes six more vouchers out of a company whose books are
    already uncertain, and it costs a reconciliation nobody can do. Refusing
    costs one more read.
    """
    if batch.state is BatchState.CRITICAL_FAILURE:
        raise ValueError(
            f"batch {batch.batch_id} is CRITICAL_FAILURE and cannot be resumed: "
            f"{batch.detail}. A person has to reconcile the books by hand."
        )
    if unresolved := batch.unresolved:
        raise ValueError(
            f"batch {batch.batch_id} cannot be resumed while "
            f"{len(unresolved)} of {len(batch.outcomes)} voucher(s) have an "
            "outcome nobody can name: "
            + "; ".join(f"{o.operation_id} is {o.state.value}" for o in unresolved)
            + ". Nothing further is written, including the cleanup that is "
            "merely outstanding. Run reconcile() again — a separate, read-only "
            "operation — against a Tally that answers reads, until every "
            "voucher has a state a read established; then resume."
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
    # Derived here for the same reason as in `reconcile`: a resume that ends
    # with a fresh unknown has to be as unresumable as the batch that produced
    # the first one, or the second interruption is the one that gets waved
    # through.
    driven = _drive(
        batch,
        client,
        log=log,
        company_key=company_key,
        run_id=run_id,
        # The one place `operator` is the right label for starting work: the
        # batch leaves its resting state because a person looked at the refusal
        # and said go on. Not an authenticated identity — see the module
        # docstring and H-05.
        actor=Actor.OPERATOR,
    )
    return replace(driven, reconciled=not driven.unresolved)


# ---- the full history: read it back, and check it is whole --------------------


def history(
    rows: Sequence[ActionLog], *, batch_id: str = ""
) -> tuple[ReversalEvent, ...]:
    """Every reversal event in these log rows, oldest first.

    `rows` comes from `MemoryStore.actions(company)`, which is already ordered
    by rowid — by what happened first, not by a clock two events can share.
    Filtering happens on the action word, never by parsing prose out of
    `detail`.
    """
    return tuple(
        ReversalEvent.from_action_log(row)
        for row in rows
        if row.action in REVERSAL_ACTIONS and (not batch_id or row.batch_id == batch_id)
    )


@dataclass(frozen=True)
class Gap:
    """A place the chain does not join up. Each one is a transition nobody
    recorded, or an event claiming a predecessor that never happened."""

    #: (batch id, document) — the scope whose chain broke.
    scope: tuple[str, str]
    #: The state the chain had reached at that point.
    reached: str
    #: The state the next event claims to have started from, or — for the last
    #: entry in a chain — the state the object is actually in now.
    expected: str
    why: str

    def __str__(self) -> str:
        batch, document = self.scope
        return (
            f"{batch}/{document}: {self.why} — the recorded chain reaches "
            f"{self.reached} and {self.expected} is unaccounted for"
        )


@dataclass(frozen=True)
class HistoryAudit:
    """What the durable history does and does not prove. Counts, not adjectives.

    Every number here is read back out of the log, not carried forward from the
    run that wrote it. `overwritten` is `None` when nobody supplied the events
    that were written, because "no overwrites detected" and "nobody looked" are
    different answers and only one of them is evidence.
    """

    events: int
    complete: int
    missing: Mapping[str, int]
    gaps: tuple[Gap, ...]
    overwritten: int | None = None

    @property
    def whole(self) -> bool:
        """Every event carries all seven, every chain joins up, nothing was
        overwritten — and the overwrite question was actually asked."""
        return self.events == self.complete and not self.gaps and self.overwritten == 0

    def summary(self) -> str:
        missing = ", ".join(f"{n} missing {f}" for f, n in self.missing.items())
        return (
            f"{self.complete}/{self.events} events carry all seven fields; "
            f"{len(self.gaps)} unrecorded transition(s); "
            f"{'NOT_MEASURED' if self.overwritten is None else self.overwritten} "
            f"overwritten" + (f"; {missing}" if missing else "")
        )


def _expected_chains(
    batches: Sequence[Batch],
) -> dict[tuple[str, str], tuple[str, str]]:
    """Scope -> (the state it started in, the state it is in now).

    The starting states are facts about `preview`, not assumptions: it builds
    every candidate `NOT_ATTEMPTED` and returns the batch `PREVIEW`.
    """
    expected: dict[tuple[str, str], tuple[str, str]] = {}
    for batch in batches:
        expected[(batch.batch_id, batch.batch_id)] = (
            INITIAL_BATCH_STATE.value,
            batch.state.value,
        )
        for outcome in batch.outcomes:
            expected[(batch.batch_id, outcome.operation_id)] = (
                INITIAL_VOUCHER_STATE.value,
                outcome.state.value,
            )
    return expected


def audit(
    events: Sequence[ReversalEvent],
    *,
    batches: Sequence[Batch],
    written: Sequence[ReversalEvent] | None = None,
) -> HistoryAudit:
    """Is this history whole, and does it match what actually happened?

    THE HARD PART IS NOT THE FIELDS. Checking that every recorded event carries
    seven fields only proves the events somebody remembered to write are
    well-formed. The question that matters is whether an event is MISSING, and
    no amount of inspecting the rows that exist can answer it.

    So the chain is replayed instead. For each scope — one batch, or one voucher
    inside one batch — the events must run from the state `preview` created it
    in, through each recorded transition, to the state the object is in now,
    with every link joining `new state` to the next `previous state`. A
    transition that was never recorded leaves the replay short of where the
    object actually is, and that is a `Gap`. So is an event whose `previous
    state` does not match where the chain had got to, which is what a
    reordered, duplicated or fabricated event looks like.

    `written` is the events the run believes it wrote. Supplied, it measures
    overwriting: every one of them must still be present, in order, with the
    same seven fields. Omitted, `overwritten` is `None` — NOT_MEASURED — and
    `whole` is False, because an audit that did not ask cannot report zero.
    """
    missing: dict[str, int] = {}
    for event in events:
        for field_name in event.missing:
            missing[field_name] = missing.get(field_name, 0) + 1

    recorded: dict[tuple[str, str], list[ReversalEvent]] = {}
    for event in events:
        recorded.setdefault(event.scope, []).append(event)

    expected = _expected_chains(batches)
    gaps: list[Gap] = []
    for scope, (start, end) in expected.items():
        reached = start
        for event in recorded.get(scope, []):
            if event.previous_state != reached:
                gaps.append(
                    Gap(
                        scope=scope,
                        reached=reached,
                        expected=event.previous_state,
                        why="an event starts from a state the chain never was in",
                    )
                )
            reached = event.new_state
        if reached != end:
            gaps.append(
                Gap(
                    scope=scope,
                    reached=reached,
                    expected=end,
                    why="a transition happened that no event records",
                )
            )
    for scope in recorded:
        if scope not in expected:
            gaps.append(
                Gap(
                    scope=scope,
                    reached="",
                    expected="",
                    why="events recorded against a scope no batch knows about",
                )
            )

    overwritten: int | None = None
    if written is not None:
        overwritten = _overwritten(written, events)

    return HistoryAudit(
        events=len(events),
        complete=sum(1 for e in events if e.complete),
        missing=missing,
        gaps=tuple(gaps),
        overwritten=overwritten,
    )


def _overwritten(
    written: Sequence[ReversalEvent], read_back: Sequence[ReversalEvent]
) -> int:
    """How many of the events we wrote are not in the log, unchanged, in order.

    Positional, not set-based, and that is deliberate. A log that collapsed two
    byte-identical events into one would pass a set comparison and has lost a
    thing that happened — which is exactly the mistake `action_log` has no
    primary key in order to avoid.
    """
    lost = max(len(written) - len(read_back), 0)
    for mine, theirs in zip(written, read_back, strict=False):
        if mine.identity != theirs.identity:
            lost += 1
    return lost
