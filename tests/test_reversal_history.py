"""Phase 8 PR-5: the full reversal history, and what its actor field is worth.

WHAT THIS FILE IS FOR
---------------------
Owner decision Q8 = A, 2026-08-10. Every reversal event carries seven fields:

    previous state · new state · reason · actor · timestamp
    company/document scope · evidence

and the acceptance table is:

    20/20 events preserve all seven
    0 overwritten · 0 missing actors · 0 missing timestamps
    0 missing scopes · 0 missing reasons

THE MEASURED STARTING POINT, CHECKED HERE RATHER THAN RELAYED
--------------------------------------------------------------
`action_log` had ELEVEN columns before this work:

    company_key · ts · action · outcome · reason · run_id
    backend · operation_id · voucher_id · vendor_id · detail

Four of the seven fields were already there — reason, timestamp, scope and new
state — plus evidence in `detail`. **Actor and previous state were absent.**
`test_the_action_log_was_missing_exactly_two_of_the_seven` asserts the shape the
audit reported, so the claim is a measurement in this repository and not a
number carried in from somewhere else.

THE HARD PART IS THE TRANSITION NOBODY RECORDED
-----------------------------------------------
Inspecting the events that exist can only ever prove that the events somebody
remembered to write are well-formed. It cannot see the one that was never
written. So the history is REPLAYED: for each scope the chain must run from the
state `preview` created it in, link by link, to the state the object is in now.
A transition with no event leaves the replay short, and `reversal.audit` reports
it as a gap. `test_a_transition_that_no_event_records_is_detected` is the test
that matters most in this file.

WHAT `operator` IS NOT
----------------------
It is not an authenticated user identity, and no test here may be read as
evidence that it is.

    authenticated user identity = NOT_IMPLEMENTED
    actor provenance            = coarse-grained system/operator

Approving an identity subsystem is H-05 —
`OWNER_DECISION_REQUIRED: approve an authenticated identity subsystem` — and
nothing in this branch builds one. `dependencies = []` is asserted below.

EVIDENCE CLASS
--------------
FAKETALLY. `FakeTally`, plus the connection-dropping double defined below.
Nothing here is evidence about a licensed TallyPrime's delete semantics; it is
evidence about what this system records when a batch moves between states.
"""

from __future__ import annotations

import ast
import datetime
import itertools
import pathlib
import sqlite3
import tomllib

import pytest

from accountant import reversal
from accountant.memory import store as store_module
from accountant.memory.store import MemoryStore
from accountant.reversal import (
    BATCH_ACTION,
    BATCH_STATE_ACTION,
    Batch,
    BatchState,
    ReversalEvent,
    VoucherState,
)
from accountant.schema import NOT_RECORDED, ActionLog, Actor, Voucher
from accountant.tallyio.fake import FakeTally
from tests.test_reversal_recovery import ACCOUNTS, COMPANY, KEY, books, post_n

ROOT = pathlib.Path(reversal.__file__).resolve().parent.parent


class DropsTheConnection(FakeTally):
    """One voucher's delete raises mid-request, so nobody can say whether it
    landed. The same double as `tests/test_reversal_recovery._DropsTheConnection`
    and deliberately a second copy: that one is private to its own file, and a
    test file reaching into another test file's internals is the kind of
    coupling that makes a shared double impossible to change.

    `target` and `deletes` are set on the INSTANCE. A class attribute outlives
    the test that set it, and a double left armed is how a later test comes to
    pass for a reason nobody chose.
    """

    def __init__(self) -> None:
        super().__init__()
        self.target: str = ""
        self.reads_fail: bool = False
        #: Every operation id that reached the delete method, in order, whether
        #: it went on to succeed or to raise.
        self.deletes: list[str] = []

    def read_by_operation_id(self, company: str, operation_id: str) -> Voucher | None:
        if self.reads_fail:
            raise ConnectionError("Tally is not answering reads")
        return super().read_by_operation_id(company, operation_id)

    def reverse_by_operation_id(self, company: str, operation_id: str) -> bool:
        self.deletes.append(operation_id)
        if operation_id == self.target:
            raise ConnectionError("the connection dropped mid-request")
        return super().reverse_by_operation_id(company, operation_id)


def a_broken_tally() -> DropsTheConnection:
    tally = DropsTheConnection()
    tally.add_company(COMPANY, accounts=ACCOUNTS, backed_up=True)
    return tally


#: The eleven columns `action_log` had before this work, in order. Written out
#: rather than derived, so a column silently disappearing is a failure here
#: instead of a shrug.
ELEVEN_BEFORE: tuple[str, ...] = (
    "company_key",
    "ts",
    "action",
    "outcome",
    "reason",
    "run_id",
    "backend",
    "operation_id",
    "voucher_id",
    "vendor_id",
    "detail",
)

THREE_ADDED: tuple[str, ...] = ("actor", "previous_state", "batch_id")


class Recorder:
    """A log sink that keeps its own copy of every row AND forwards it.

    Needed for the overwrite measurement: "nothing was overwritten" is a claim
    about what came back compared with what went in, and without a copy of what
    went in there is nothing to compare against. `MemoryStore` satisfies
    `pipeline.ActionLogSink` and so does this.
    """

    def __init__(self, store: MemoryStore) -> None:
        self.store = store
        self.written: list[ActionLog] = []

    def record_action(self, entry: ActionLog) -> None:
        self.written.append(entry)
        self.store.record_action(entry)

    def events(self) -> tuple[ReversalEvent, ...]:
        return reversal.history(tuple(self.written))


# ---------------------------------------------------------------------------
# the starting point, measured here rather than relayed
# ---------------------------------------------------------------------------


def test_the_action_log_was_missing_exactly_two_of_the_seven() -> None:
    """The audit said `action_log` had eleven columns and neither an actor nor a
    previous state. Both halves are checked: the eleven are all still there, in
    order, and the additions are exactly the ones that were missing.

    Two relayed numbers in this project have turned out to be false, so this is
    read off the live schema.
    """
    store = MemoryStore()
    columns = store.columns_of("action_log")

    assert columns[: len(ELEVEN_BEFORE)] == ELEVEN_BEFORE, (
        "the eleven columns that were there before must still be there, in "
        "order; this migration is additive"
    )
    assert columns[len(ELEVEN_BEFORE) :] == THREE_ADDED
    assert "actor" not in ELEVEN_BEFORE
    assert "previous_state" not in ELEVEN_BEFORE


def test_each_of_the_seven_fields_has_somewhere_to_live() -> None:
    """The mapping, stated once. Five of the seven ride on columns that already
    existed; two needed the schema change."""
    store = MemoryStore()
    columns = set(store.columns_of("action_log"))

    already_there = {
        "reason": "reason",
        "timestamp": "ts",
        "new state": "outcome",
        "evidence": "detail",
    }
    added_now = {"actor": "actor", "previous state": "previous_state"}

    for column in (*already_there.values(), *added_now.values()):
        assert column in columns
    # The scope is two columns together, and that is why it is not in either
    # dict above: a company without a document does not identify anything.
    assert {"company_key", "operation_id"} <= columns


def test_no_authentication_dependency_was_added() -> None:
    """`dependencies = []` and it stays that way.

    Coarse actor labels were chosen precisely so no identity subsystem was
    needed. If this ever fails, the actor field has stopped meaning what the
    owner approved.
    """
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert pyproject["project"]["dependencies"] == []


def test_there_are_exactly_two_actor_labels_and_neither_is_an_identity() -> None:
    """Owner decision Q8 = A, verbatim: `accountant_dad` and `operator`.

    A third label would be a claim about who did something that this system has
    no way to establish. `NOT_RECORDED` is not a third actor — it is the absence
    of one, and `ActionLog` refuses anything else.
    """
    assert [a.value for a in Actor] == ["accountant_dad", "operator"]

    for banned in ("user", "admin", "tanveer", "system_user", ""):
        with pytest.raises(ValueError, match="the only actors are"):
            ActionLog(
                ts=datetime.datetime.now(datetime.UTC),
                action="x",
                company_key=KEY,
                outcome="y",
                reason="a reason, because every row needs one",
                run_id="r",
                backend="b",
                actor=banned,
            )


def test_the_limit_of_the_actor_field_is_recorded_in_the_code_itself() -> None:
    """Not only in a document that ships separately from the code.

    Somebody reading `reversal.py` to find out what `operator` proves must be
    told, there, that it proves a person was in the loop and nothing more.
    """
    docstrings = (reversal.__doc__ or "") + (Actor.__doc__ or "")

    assert "NOT_IMPLEMENTED" in docstrings
    assert "coarse-grained system/operator" in docstrings
    assert "OWNER_DECISION_REQUIRED: approve an authenticated identity" in docstrings
    assert "H-05" in docstrings


# ---------------------------------------------------------------------------
# the scenario the acceptance table is measured on
# ---------------------------------------------------------------------------


def twenty_events() -> tuple[DropsTheConnection, Recorder, list[Batch], list[str]]:
    """One company, two batches, twenty events. Every one from a real run.

    Batch b1 is four vouchers. Voucher 3's connection drops mid-delete, so the
    batch stops UNKNOWN_OUTCOME with voucher 4 never attempted; a reconciliation
    reads voucher 3 and finds it still in Tally, which settles it as
    NOT_ATTEMPTED and drops the batch to PARTIAL_FAILURE; an approved resume
    then finishes vouchers 3 and 4 and the batch completes.

    Batch b2 is what an operator does next: preview again, find nothing of ours
    left, and confirm it. A batch with no candidates still has its own history,
    and this is the part that would be silently absent if only vouchers were
    recorded.

        confirm b1     1     preview -> confirmed
        execute b1     8     confirmed -> reversing, three vouchers x 2,
                             reversing -> unknown_outcome
        reconcile b1   2     voucher 3, and unknown_outcome -> partial_failure
        resume b1      6     partial_failure -> reversing, two vouchers x 2,
                             reversing -> completed
        confirm b2     1     preview -> confirmed
        execute b2     2     confirmed -> reversing, reversing -> completed
                      --
                      20
    """
    tally = a_broken_tally()
    log = Recorder(MemoryStore(":memory:"))
    ops = post_n(tally, 4)
    tally.target = ops[2]

    first = reversal.preview(tally, COMPANY, batch_id="b1")
    stopped = reversal.execute(
        reversal.confirm(first, log=log, company_key=KEY, run_id="run-1"),
        tally,
        log=log,
        company_key=KEY,
        run_id="run-1",
    )
    assert stopped.state is BatchState.UNKNOWN_OUTCOME

    tally.target = ""
    settled = reversal.reconcile(
        stopped, tally, log=log, company_key=KEY, run_id="run-1"
    )
    assert settled.state is BatchState.PARTIAL_FAILURE

    finished = reversal.resume(
        settled, tally, approved=True, log=log, company_key=KEY, run_id="run-2"
    )
    assert finished.state is BatchState.COMPLETED

    nothing_left = reversal.preview(tally, COMPANY, batch_id="b2")
    assert nothing_left.outcomes == ()
    done = reversal.execute(
        reversal.confirm(nothing_left, log=log, company_key=KEY, run_id="run-3"),
        tally,
        log=log,
        company_key=KEY,
        run_id="run-3",
    )
    assert done.state is BatchState.COMPLETED

    return tally, log, [finished, done], ops


def test_twenty_reversal_events_each_preserve_all_seven_fields() -> None:
    """The owner's acceptance table, measured, in one test.

        20/20 events preserve all seven
        0 overwritten · 0 missing actors · 0 missing timestamps
        0 missing scopes · 0 missing reasons

    Read back out of SQLite, not counted off the objects the run held in memory.
    A history that only exists in the process that wrote it is not a history.
    """
    _tally, log, batches, _ops = twenty_events()

    events = reversal.history(log.store.actions(COMPANY))
    result = reversal.audit(events, batches=batches, written=log.events())

    assert result.events == 20
    assert result.complete == 20
    assert result.missing == {}, result.summary()
    assert result.gaps == (), [str(g) for g in result.gaps]
    assert result.overwritten == 0
    assert result.whole is True

    # The five "0" rows of the table, each named, so a failure says which.
    for field_name in ("actor", "timestamp", "scope", "reason", "previous state"):
        assert result.missing.get(field_name, 0) == 0

    # And every event individually, so "20/20" is not an aggregate hiding one
    # blank field behind nineteen good ones.
    for event in events:
        assert event.missing == (), (event.scope, event.missing)
        assert event.actor in tuple(Actor)
        assert event.ts is not None
        assert event.company_key == KEY
        assert event.document
        assert event.reason.strip()
        assert event.evidence.strip()


def test_both_actor_labels_appear_and_each_is_on_the_right_transition() -> None:
    """`operator` is not decoration. It marks the two places a person acted:
    confirming the candidate list, and approving the resume after the batch
    refused to continue on its own.

    Everything else is `accountant_dad`, including every per-voucher
    transition — nobody chose those one at a time.
    """
    _tally, log, _batches, _ops = twenty_events()
    events = reversal.history(log.store.actions(COMPANY))

    by_actor: dict[str, list[str]] = {}
    for event in events:
        by_actor.setdefault(event.actor, []).append(
            f"{event.previous_state}->{event.new_state}"
        )

    assert set(by_actor) == {Actor.ACCOUNTANT_DAD, Actor.OPERATOR}
    assert sorted(by_actor[Actor.OPERATOR]) == [
        "partial_failure->reversing",  # the approved resume
        "preview->confirmed",  # batch b1
        "preview->confirmed",  # batch b2
    ]
    assert all(
        e.actor is not None and e.actor == Actor.ACCOUNTANT_DAD
        for e in events
        if e.action == BATCH_ACTION
    ), "no per-voucher transition is attributed to a person"


def test_the_history_reconstructs_one_vouchers_whole_life() -> None:
    """The point of carrying the PREVIOUS state: the chain, not a list of
    endings.

    Voucher 3 is the interesting one. It was attempted, went unknown, was
    settled by a read, was attempted again and finally verified — and every link
    is present, in order, with no state appearing from nowhere.
    """
    _tally, log, _batches, ops = twenty_events()
    events = reversal.history(log.store.actions(COMPANY))

    chain = [e for e in events if e.document == ops[2]]

    assert [(e.previous_state, e.new_state) for e in chain] == [
        ("not_attempted", "request_sent"),
        ("request_sent", "unknown_outcome"),
        ("unknown_outcome", "not_attempted"),
        ("not_attempted", "request_sent"),
        ("request_sent", "reversed_verified"),
    ]
    # Each link joins to the next. Stated as its own assertion because that is
    # the property `audit` relies on and the list above only illustrates.
    for earlier, later in itertools.pairwise(chain):
        assert earlier.new_state == later.previous_state


def test_the_batch_chain_runs_from_preview_to_completed() -> None:
    """The batch has a life too, and `reversing` is part of it.

    Before this work `BatchState.REVERSING` was a name in the enum that nothing
    ever set, so the history would have jumped from `confirmed` to whatever the
    batch rested at — leaving out the interval in which the vouchers were
    actually being deleted.
    """
    _tally, log, _batches, _ops = twenty_events()
    events = reversal.history(log.store.actions(COMPANY), batch_id="b1")

    batch_chain = [e for e in events if e.action == BATCH_STATE_ACTION]

    assert [e.new_state for e in batch_chain] == [
        "confirmed",
        "reversing",
        "unknown_outcome",
        "partial_failure",
        "reversing",
        "completed",
    ]
    assert batch_chain[0].previous_state == BatchState.PREVIEW.value
    assert all(e.document == "b1" for e in batch_chain)


def test_the_two_batches_keep_separate_chains() -> None:
    """Scope is (batch, document). Two batches in one company do not share a
    chain, or the second one's `preview -> confirmed` would look like an event
    arriving after the first had completed."""
    _tally, log, _batches, _ops = twenty_events()
    rows = log.store.actions(COMPANY)

    assert len(reversal.history(rows, batch_id="b1")) == 17
    assert len(reversal.history(rows, batch_id="b2")) == 3
    assert {e.batch_id for e in reversal.history(rows)} == {"b1", "b2"}


# ---------------------------------------------------------------------------
# the mutant that matters: a transition nobody recorded
# ---------------------------------------------------------------------------


def test_a_transition_that_no_event_records_is_detected() -> None:
    """The one failure mode that inspecting the recorded events cannot find.

    It is easy to check that the events you remembered to write are well-formed.
    This drops the LAST event of one voucher's chain — exactly what forgetting a
    `_record` call produces — and the audit has to notice that the replay now
    stops at `request_sent` while the voucher is actually `reversed_verified`.

    Every remaining event is still perfect: 19 of 19 carry all seven fields. If
    completeness were the only check, this would pass.
    """
    _tally, log, batches, ops = twenty_events()
    events = list(reversal.history(log.store.actions(COMPANY)))

    dropped = next(
        e
        for e in reversed(events)
        if e.document == ops[3] and e.new_state == "reversed_verified"
    )
    events.remove(dropped)

    result = reversal.audit(tuple(events), batches=batches)

    assert result.events == 19
    assert result.complete == 19, "every surviving event is still well-formed"
    assert result.missing == {}
    assert len(result.gaps) == 1
    (gap,) = result.gaps
    assert gap.scope == ("b1", ops[3])
    assert gap.reached == "request_sent"
    assert gap.expected == "reversed_verified"
    assert "no event records" in gap.why
    assert result.whole is False


def test_a_missing_batch_transition_is_detected_the_same_way() -> None:
    """Vouchers are the obvious thing to record and the batch is the thing that
    gets forgotten, so it is checked separately rather than assumed to follow.
    """
    _tally, log, batches, _ops = twenty_events()
    events = [
        e
        for e in reversal.history(log.store.actions(COMPANY))
        if not (e.document == "b2" and e.new_state == "completed")
    ]

    result = reversal.audit(tuple(events), batches=batches)

    assert len(result.gaps) == 1
    assert result.gaps[0].scope == ("b2", "b2")
    assert result.gaps[0].reached == "reversing"
    assert result.gaps[0].expected == "completed"


def test_an_event_starting_from_a_state_the_chain_was_never_in_is_detected() -> None:
    """The other half of the chain check.

    A reordered, duplicated or invented event claims a predecessor that did not
    happen. Without this, a history could be padded with plausible-looking rows
    and still replay to the right ending.
    """
    _tally, log, batches, ops = twenty_events()
    events = list(reversal.history(log.store.actions(COMPANY)))
    i = next(i for i, e in enumerate(events) if e.document == ops[0])
    events.insert(
        i,
        ReversalEvent(
            previous_state="reversed_verified",
            new_state="not_attempted",
            reason="an event nobody's run produced",
            actor=Actor.ACCOUNTANT_DAD,
            ts=datetime.datetime.now(datetime.UTC),
            company_key=KEY,
            document=ops[0],
            evidence="fabricated for this test",
            batch_id="b1",
        ),
    )

    result = reversal.audit(tuple(events), batches=batches)

    assert result.complete == 21, "the forged event is itself well-formed"
    assert any("never was in" in g.why for g in result.gaps)
    assert result.whole is False


def test_events_recorded_against_a_scope_no_batch_knows_about_are_detected() -> None:
    """A chain that belongs to nothing is as much a defect as a missing link."""
    _tally, log, batches, _ops = twenty_events()
    events = (
        *reversal.history(log.store.actions(COMPANY)),
        ReversalEvent(
            previous_state="not_attempted",
            new_state="reversed_verified",
            reason="a voucher no batch in this audit contains",
            actor=Actor.ACCOUNTANT_DAD,
            ts=datetime.datetime.now(datetime.UTC),
            company_key=KEY,
            document="op-from-nowhere",
            evidence="fabricated for this test",
            batch_id="b9",
        ),
    )

    result = reversal.audit(events, batches=batches)

    assert any("no batch knows about" in g.why for g in result.gaps)


# ---------------------------------------------------------------------------
# append-only, measured rather than assumed
# ---------------------------------------------------------------------------


def test_zero_events_were_overwritten_measured_not_assumed() -> None:
    """`0 overwritten` compares what came back with what went in, positionally.

    A set comparison would pass on a log that collapsed two identical events
    into one, and losing a thing that happened is precisely what an append-only
    trail exists to prevent.
    """
    _tally, log, batches, _ops = twenty_events()

    read_back = reversal.history(log.store.actions(COMPANY))
    written = log.events()

    assert len(written) == 20
    assert len(read_back) == 20
    for mine, theirs in zip(written, read_back, strict=True):
        assert mine.identity == theirs.identity
    assert reversal.audit(read_back, batches=batches, written=written).overwritten == 0


def test_two_byte_identical_events_are_two_rows_and_not_one() -> None:
    """Two identical decisions are not a duplicate to be collapsed; they are two
    things that happened. `action_log` has no primary key for this reason, and
    this is the behavioural half of that structural choice."""
    store = MemoryStore(":memory:")
    row = ActionLog(
        ts=datetime.datetime(2026, 8, 10, 9, 0, tzinfo=datetime.UTC),
        action=BATCH_ACTION,
        company_key=KEY,
        outcome=VoucherState.REQUEST_SENT.value,
        reason="the same thing, twice",
        run_id="run-1",
        backend="FakeTally",
        operation_id="op-1",
        detail="batch b1; moved {}",
        actor=Actor.ACCOUNTANT_DAD,
        previous_state=VoucherState.NOT_ATTEMPTED.value,
        batch_id="b1",
    )

    store.record_action(row)
    store.record_action(row)

    assert len(store.actions(COMPANY)) == 2
    assert store.primary_key_of("action_log") == ()


def test_the_store_has_no_update_or_delete_path_for_the_action_log() -> None:
    """Structural. An audit row a later write can edit is not an audit row.

    Read off the module source rather than promised in a comment: every SQL
    statement in `accountant/memory/store.py` that names `action_log` must be an
    INSERT or a SELECT, and the INSERT must not be an `INSERT OR REPLACE` — the
    form the four lookup tables in the same file legitimately use.
    """
    source = pathlib.Path(store_module.__file__).read_text(encoding="utf-8")
    statements = [
        node.value.upper()
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and "ACTION_LOG" in node.value.upper()
    ]

    assert statements, "the scan found nothing, so it is proving nothing"
    for statement in statements:
        assert "UPDATE ACTION_LOG" not in statement
        assert "DELETE FROM ACTION_LOG" not in statement
        assert "REPLACE INTO ACTION_LOG" not in statement
    # And the control: the same scan DOES find the delete the four lookup
    # tables have, so a scan that simply matches nothing cannot pass this test.
    lookups = [
        node.value.upper()
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and "DELETE FROM VENDOR_ACCOUNT" in node.value.upper()
    ]
    assert lookups, "the scan cannot see a delete it should see"


def test_forget_leaves_the_reversal_history_where_it_is() -> None:
    """`forget()` runs on every rebuild. The index is a statement about our
    memory and may be rebuilt; what we already did to somebody's books is a
    different fact."""
    _tally, log, _batches, _ops = twenty_events()
    before = reversal.history(log.store.actions(COMPANY))

    log.store.forget(KEY)

    assert len(before) == 20
    assert reversal.history(log.store.actions(COMPANY)) == before


def test_the_audit_refuses_to_report_zero_overwritten_when_nobody_asked() -> None:
    """NOT_MEASURED and zero are different answers.

    An audit that reports 0 because it never compared anything is the shape of
    every number this project has had to strike out later.
    """
    _tally, log, batches, _ops = twenty_events()

    unasked = reversal.audit(
        reversal.history(log.store.actions(COMPANY)), batches=batches
    )

    assert unasked.overwritten is None
    assert unasked.gaps == ()
    assert unasked.complete == 20
    assert unasked.whole is False, (
        "twenty perfect events and no overwrite check is still not a whole "
        "history; the question was not asked"
    )
    assert "NOT_MEASURED" in unasked.summary()


# ---------------------------------------------------------------------------
# the migration: a row from before these columns existed
# ---------------------------------------------------------------------------


def an_old_database(path: pathlib.Path) -> None:
    """A file written by a build that predates `actor` and `previous_state`.

    Built with the ELEVEN-column CREATE TABLE verbatim rather than by deleting
    columns afterwards, because SQLite's column drop is not what an old file
    went through and the point is to reproduce the old file.
    """
    db = sqlite3.connect(str(path))
    db.execute(
        """
        CREATE TABLE action_log (
            company_key  TEXT NOT NULL,
            ts           TEXT NOT NULL,
            action       TEXT NOT NULL,
            outcome      TEXT NOT NULL,
            reason       TEXT NOT NULL,
            run_id       TEXT NOT NULL,
            backend      TEXT NOT NULL,
            operation_id TEXT NOT NULL,
            voucher_id   TEXT NOT NULL,
            vendor_id    TEXT NOT NULL,
            detail       TEXT NOT NULL
        )
        """
    )
    db.execute(
        "INSERT INTO action_log VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            KEY,
            "2026-08-09T10:00:00+00:00",
            BATCH_ACTION,
            VoucherState.REVERSED_VERIFIED.value,
            "reversed as part of the phase 5B batch",
            "run-old",
            "FakeTally",
            "op-legacy",
            "",
            "",
            "batch bulk_old; moved {'Purchases': -100000}",
        ),
    )
    db.commit()
    db.close()


def test_a_row_written_before_the_columns_existed_reads_as_unrecorded(
    tmp_path: pathlib.Path,
) -> None:
    """The migration's whole honesty, in one assertion.

    The row is real. What is not known about it is who did it and what state the
    voucher was in beforehand, because nothing recorded either. It reads back
    saying exactly that.
    """
    path = tmp_path / "old.sqlite3"
    an_old_database(path)

    store = MemoryStore(path)
    (row,) = store.actions(COMPANY)

    assert row.actor == NOT_RECORDED
    assert row.previous_state == NOT_RECORDED
    assert row.reason == "reversed as part of the phase 5B batch"
    assert row.outcome == VoucherState.REVERSED_VERIFIED.value
    assert row.operation_id == "op-legacy"
    assert store.columns_of("action_log")[-3:] == THREE_ADDED


def test_a_legacy_row_is_never_mistaken_for_a_system_action(
    tmp_path: pathlib.Path,
) -> None:
    """The back-fill that must never happen.

    `accountant_dad` on a historical row would say the system did something
    nobody recorded the actor of. It is the cheapest possible fix and it makes
    every row in the file a lie of the same size, including the ones a person
    took. An explicit "we did not record this" is evidence; a plausible guess is
    not.
    """
    path = tmp_path / "old.sqlite3"
    an_old_database(path)

    store = MemoryStore(path)
    (event,) = reversal.history(store.actions(COMPANY))

    assert event.actor != Actor.ACCOUNTANT_DAD
    assert event.actor != Actor.OPERATOR
    assert event.actor not in tuple(Actor)
    assert event.actor == NOT_RECORDED
    assert set(event.missing) == {"previous state", "actor"}
    assert event.complete is False

    # And the column itself is still NULL — nothing rewrote the row on open.
    raw = (
        sqlite3.connect(str(path))
        .execute("SELECT actor, previous_state FROM action_log")
        .fetchall()
    )
    assert raw == [(None, None)]


def test_the_migration_does_not_rewrite_any_row(tmp_path: pathlib.Path) -> None:
    """Additive only, and idempotent. Opening the same file twice adds the
    columns once and changes nothing else."""
    path = tmp_path / "old.sqlite3"
    an_old_database(path)

    first = MemoryStore(path)
    once = first.actions(COMPANY)
    first.close()
    second = MemoryStore(path)
    twice = second.actions(COMPANY)
    second.close()

    assert once == twice
    assert len(twice) == 1


def test_a_legacy_row_and_a_new_row_sit_in_the_same_trail(
    tmp_path: pathlib.Path,
) -> None:
    """The migrated file keeps working. A new event appended after the migration
    carries all seven; the old one beside it carries five and says so."""
    path = tmp_path / "old.sqlite3"
    an_old_database(path)
    store = MemoryStore(path)
    tally = books()
    ops = post_n(tally, 1)

    reversal.execute(
        reversal.confirm(
            reversal.preview(tally, COMPANY, batch_id="b1"),
            log=store,
            company_key=KEY,
            run_id="run-new",
        ),
        tally,
        log=store,
        company_key=KEY,
        run_id="run-new",
    )
    events = reversal.history(store.actions(COMPANY))
    store.close()

    old, *new = events
    assert old.document == "op-legacy"
    assert old.missing == ("previous state", "actor")
    # preview->confirmed, confirmed->reversing, the voucher's two, and the
    # batch settling to completed.
    assert len(new) == 5
    assert [e.complete for e in new] == [True] * len(new)
    assert {e.document for e in new} == {"b1", ops[0]}


# ---------------------------------------------------------------------------
# the write path refuses an incomplete event
# ---------------------------------------------------------------------------


def a_complete_event(**overrides: object) -> ReversalEvent:
    fields: dict[str, object] = {
        "previous_state": VoucherState.NOT_ATTEMPTED.value,
        "new_state": VoucherState.REQUEST_SENT.value,
        "reason": "the batch reached this voucher",
        "actor": Actor.ACCOUNTANT_DAD,
        "ts": datetime.datetime.now(datetime.UTC),
        "company_key": KEY,
        "document": "op-1",
        "evidence": "batch b1; moved {}",
        "batch_id": "b1",
    }
    fields.update(overrides)
    return ReversalEvent(**fields)  # type: ignore[arg-type]


def test_a_complete_event_is_the_control() -> None:
    """Every refusal below is worth nothing without this: the same builder,
    untouched, produces an event that IS accepted."""
    event = a_complete_event()

    assert event.missing == ()
    assert event.complete is True
    event.demand_complete()


@pytest.mark.parametrize(
    ("field_name", "value", "named"),
    [
        ("actor", NOT_RECORDED, "actor"),
        ("actor", "", "actor"),
        ("ts", None, "timestamp"),
        ("company_key", "", "scope"),
        ("document", "", "scope"),
        ("reason", "   ", "reason"),
        ("previous_state", NOT_RECORDED, "previous state"),
        ("new_state", "", "new state"),
        ("evidence", "", "evidence"),
    ],
)
def test_an_event_missing_any_one_field_is_refused_by_name(
    field_name: str, value: object, named: str
) -> None:
    """One parametrised case per field, and the refusal names the field.

    "Something is missing" sends a person to compare seven values by hand. The
    field name is the whole difference between a message and a fix.
    """
    event = a_complete_event(**{field_name: value})

    assert named in event.missing
    assert event.complete is False
    with pytest.raises(ValueError, match=named):
        event.demand_complete()


def test_the_write_path_refuses_rather_than_recording_a_hole() -> None:
    """Driven through `execute`, not through the private recorder, because the
    claim is about what the product does and not about a helper.

    A batch is hand-built carrying a voucher with no operation id — a thing
    `preview` refuses to produce and only a corrupted resume could hand in. The
    scope of its event would be a company and nothing else, which identifies no
    document, so the write is refused and NO voucher row reaches the log.
    """
    tally = books()
    store = MemoryStore(":memory:")
    nameless = Batch(
        batch_id="b1",
        company=COMPANY,
        state=BatchState.CONFIRMED,
        baseline={},
        outcomes=(
            reversal.VoucherOutcome(operation_id="", state=VoucherState.NOT_ATTEMPTED),
        ),
        detail="hand-built for this test",
    )

    with pytest.raises(ValueError, match="scope"):
        reversal.execute(nameless, tally, log=store, company_key=KEY, run_id="run-1")

    voucher_rows = [r for r in store.actions(COMPANY) if r.action == BATCH_ACTION]
    assert voucher_rows == [], "the unidentifiable event was not written"


def test_not_recorded_counts_as_missing_and_never_as_a_value() -> None:
    """The marker is a description of a row, not a way to satisfy the
    requirement. If `NOT_RECORDED` passed the completeness check, every legacy
    row would report seven of seven.

    Checked through the public `missing`, one field at a time, so the property
    is asserted where it is used rather than on the helper underneath it.
    """
    assert a_complete_event(actor=NOT_RECORDED).missing == ("actor",)
    assert a_complete_event(previous_state=NOT_RECORDED).missing == ("previous state",)
    assert a_complete_event(reason=NOT_RECORDED).missing == ("reason",)
    assert a_complete_event(evidence=NOT_RECORDED).missing == ("evidence",)
    assert a_complete_event(document=NOT_RECORDED).missing == ("scope",)
    assert a_complete_event(actor=Actor.OPERATOR).missing == ()


# ---------------------------------------------------------------------------
# D-29 stays exactly as it is
# ---------------------------------------------------------------------------


def test_a_refused_resume_still_writes_no_event_of_any_kind() -> None:
    """D-29 / owner decision D-03: one UNKNOWN_OUTCOME refuses the WHOLE batch.

    The six tests in `tests/test_reversal_recovery.py` pin that behaviour and
    one of them asserts a refused resume writes no `bulk_reverse` row. Adding a
    second action name could have opened a hole beside that assertion, so the
    same claim is made here over BOTH names: a refusal did nothing, so it
    records nothing.
    """
    tally = a_broken_tally()
    store = MemoryStore(":memory:")
    ops = post_n(tally, 5)
    tally.target = ops[2]

    stopped = reversal.execute(
        reversal.confirm(reversal.preview(tally, COMPANY, batch_id="b1")),
        tally,
        log=store,
        company_key=KEY,
        run_id="run-1",
    )
    tally.reads_fail = True
    unsettled = reversal.reconcile(stopped, tally)
    tally.reads_fail = False
    tally.target = ""
    assert unsettled.outcomes[2].state is VoucherState.UNKNOWN_OUTCOME
    before = len(store.actions(COMPANY))
    tally.deletes.clear()

    with pytest.raises(ValueError, match=ops[2]):
        reversal.resume(
            unsettled,
            tally,
            approved=True,
            log=store,
            company_key=KEY,
            run_id="run-refused",
        )

    after = store.actions(COMPANY)
    assert len(after) == before
    assert [r for r in after if r.run_id == "run-refused"] == []
    assert tally.deletes == [], "and not one delete reached the connector"


def test_a_reconciliation_that_could_not_read_records_no_transition() -> None:
    """A read that failed settled nothing, so nothing moved, so there is no
    event. The absence here is the correct record — and `audit` will still
    report the batch's real gap, which is that voucher 3's fate is unknown."""
    tally = a_broken_tally()
    store = MemoryStore(":memory:")
    ops = post_n(tally, 4)
    tally.target = ops[2]

    stopped = reversal.execute(
        reversal.confirm(reversal.preview(tally, COMPANY, batch_id="b1")),
        tally,
        log=store,
        company_key=KEY,
        run_id="run-1",
    )
    before = len(store.actions(COMPANY))

    tally.reads_fail = True
    unsettled = reversal.reconcile(
        stopped, tally, log=store, company_key=KEY, run_id="run-1"
    )
    tally.reads_fail = False

    assert unsettled.outcomes[2].state is VoucherState.UNKNOWN_OUTCOME
    assert len(store.actions(COMPANY)) == before


def test_reconcile_records_the_state_it_settled() -> None:
    """The other side: a reconciliation that DID read changed a voucher's state,
    and a state change with no event is the hole this work exists to close."""
    tally = a_broken_tally()
    store = MemoryStore(":memory:")
    ops = post_n(tally, 4)
    tally.target = ops[2]

    stopped = reversal.execute(
        reversal.confirm(reversal.preview(tally, COMPANY, batch_id="b1")),
        tally,
        log=store,
        company_key=KEY,
        run_id="run-1",
    )
    tally.target = ""

    reversal.reconcile(stopped, tally, log=store, company_key=KEY, run_id="run-1")

    events = reversal.history(store.actions(COMPANY))
    settled = [e for e in events if e.previous_state == "unknown_outcome"]
    assert [(e.document, e.new_state) for e in settled] == [
        (ops[2], "not_attempted"),
        ("b1", "partial_failure"),
    ]
    assert all(e.actor == Actor.ACCOUNTANT_DAD for e in settled)
    assert all(e.complete for e in settled)


def test_reconcile_still_writes_nothing_into_the_customers_books() -> None:
    """`reconcile` now appends to OUR log. It must still send no delete and move
    no paise, or the read-only claim it is built on has quietly stopped being
    true."""
    tally = a_broken_tally()
    store = MemoryStore(":memory:")
    ops = post_n(tally, 4)
    tally.target = ops[2]

    stopped = reversal.execute(
        reversal.confirm(reversal.preview(tally, COMPANY, batch_id="b1")),
        tally,
        log=store,
        company_key=KEY,
        run_id="run-1",
    )
    tally.target = ""
    tally.deletes.clear()
    balance = tally.trial_balance(COMPANY)
    survivors = {
        reversal.operation_id_of(v.narration) for v in tally.list_our_vouchers(COMPANY)
    }

    reversal.reconcile(stopped, tally, log=store, company_key=KEY, run_id="run-1")

    assert tally.deletes == []
    assert tally.trial_balance(COMPANY) == balance
    assert {
        reversal.operation_id_of(v.narration) for v in tally.list_our_vouchers(COMPANY)
    } == survivors
