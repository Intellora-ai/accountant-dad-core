"""Where a proposal is, proved by how it got there. Seven states, thirteen events.

WHY THIS MODULE EXISTS
----------------------
A proposal is the thing that walks from "the reader thinks the bill says this"
to "this is now in somebody's books". Before this module that walk had no name.
Each stage was a local variable inside whichever function happened to be
running, so nothing could ask a proposal where it was, nothing could prove it
had not skipped a stage, and no row anywhere said it moved.

That is how a write gets claimed on something nobody checked. Not because
anybody decided to skip the check - because the code path had no place to record
that the check happened, so nothing could notice it was missing.

    observed   the reader has spoken, nothing is checked      may post: no
    checked    conservation AND validation both passed        no
    decided    a confidence band has been assigned            no
    asking     a question is outstanding with a person        no
    blocked    TERMINAL, and it carries a written reason      NEVER
    posting    a write is in flight, operation id claimed     in progress
    posted     written and read back                          TERMINAL

`reversal.py` already holds the only other real state machine here - eight
voucher states and seven batch states, with one durable row per transition. This
is the same shape one layer earlier, before anything reaches Tally at all.

THE AUDIT IS THE STATE. IT IS NOT A REPORT ABOUT THE STATE.
------------------------------------------------------------
`Proposal.state` is a property computed from the audit rows, and so are the
band, the gates, the answer and the operation id. There is no state field to
assign, which makes "the state changed and nothing recorded it" not a bug to be
caught but a sentence Python will not execute:

    proposal.state = POSTED       AttributeError - frozen, and not a field
    replace(p, state=POSTED)      TypeError - `state` is not an argument
    Proposal(audit=<forged>)      replayed against the table on construction

The replay is the part that matters. A forged row must join the chain AND be a
legal transition under the rules below, or the object does not load. Editing
where a real event lands is caught; inventing an event from a state it cannot
happen in is caught; starting a history anywhere but `observed` is caught.

WHY THE CLOCK IS AN ARGUMENT AND NOT A CALL
--------------------------------------------
`apply` takes `at`. Nothing in this module reads a clock, and a test scans this
file's syntax tree to prove it. Two runs of the same walk must produce identical
rows, because comparing two runs is how a transition that fired when it should
not have shows up at all. A naive datetime is refused rather than assumed to be
UTC: an hour nobody can place sorts two rows into the wrong order, and the order
is the entire evidence.

WHY THERE IS NO THRESHOLD ANYWHERE IN THIS FILE
------------------------------------------------
The three confidence events arrive already banded. This module never compares a
float to 0.95 or to anything else - it is handed `ConfidenceHigh` and believes
it. Thresholds are the owner's numbers and they live where the owner set them;
a second copy here would be a second place they could drift, and moving a
threshold moves the measurement rather than the product.

WHAT THIS MODULE DOES NOT PROVE
--------------------------------
It does not prove the conservation laws ran, that the validation was worth
anything, or that the band describes the bill. It proves the SEQUENCE is legal:
that nothing reaches `posting` without having been `checked`, that `blocked` is
a wall with a written reason on it, and that every move left a row behind.

It also cannot stop `object.__setattr__`. Anything willing to write that line
can put a proposal in any state it likes and no runtime guard in Python
prevents it. What stops it is a reviewer reading it in the diff - the same
argument `wall.py` makes about `LedgerEntry`.
"""

from __future__ import annotations

import datetime
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from accountant.schema import Actor


class State(StrEnum):
    """The seven, in the order a clean proposal meets them."""

    OBSERVED = "observed"
    CHECKED = "checked"
    DECIDED = "decided"
    ASKING = "asking"
    BLOCKED = "blocked"
    POSTING = "posting"
    POSTED = "posted"


class Event(StrEnum):
    """The thirteen. Nothing moves a proposal except one of these.

    Named for what HAPPENED, not for what should follow. `ConservationFailed`
    says the arithmetic did not hold; that it leads to `blocked` is the table's
    decision below and can be read there, in one place.
    """

    CONSERVATION_PASSED = "ConservationPassed"
    CONSERVATION_FAILED = "ConservationFailed"
    VALIDATION_PASSED = "ValidationPassed"
    VALIDATION_FAILED = "ValidationFailed"
    CONFIDENCE_HIGH = "ConfidenceHigh"
    CONFIDENCE_MEDIUM = "ConfidenceMedium"
    CONFIDENCE_LOW = "ConfidenceLow"
    HARD_RULE_VIOLATED = "HardRuleViolated"
    ANSWERED = "Answered"
    ANSWERS_EXHAUSTED = "AnswersExhausted"
    WRITE_CLAIMED = "WriteClaimed"
    WRITE_CONFIRMED = "WriteConfirmed"
    WRITE_FAILED = "WriteFailed"


class Band(StrEnum):
    """How sure the reader was, as a band and never as a number here.

    `HIGH` is the only band that may claim a write on its own. `MEDIUM` needs a
    person's answer beside it. `LOW` blocks: a question built on a guess invites
    somebody to rubber-stamp it, and their yes then carries an authority their
    reading never had.
    """

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Gate(StrEnum):
    """The two checks that together mean `checked`.

    Neither depends on the other, so neither order is privileged and both are
    recorded separately. A machine that moved on the first would let a bill that
    balances perfectly but names no party through the gate.
    """

    CONSERVATION = "conservation"
    VALIDATION = "validation"


#: Where every proposal starts, and therefore the state a reconstructed history
#: must begin from. Named once so the replay and the callers read the same fact.
INITIAL_STATE: Final = State.OBSERVED

#: Both gates, for the one comparison that means "checked".
BOTH_GATES: Final = frozenset(Gate)

#: The states nothing leaves. `blocked` because a refusal that can be talked out
#: of is not a refusal; `posted` because the customer's books have already
#: changed and no further state of ours alters that.
TERMINAL: Final[tuple[State, ...]] = (State.BLOCKED, State.POSTED)

#: The owner's third column, kept as data so it can be read back rather than
#: inferred from the table. This is documentation with a test on it, not a
#: control: the control is that no rule leads into `posting` except from
#: `DECIDED`, which `CLAIMABLE_FROM` names and a test asserts.
WRITE_PERMISSION: Final[Mapping[State, str]] = {
    State.OBSERVED: "no",
    State.CHECKED: "no",
    State.DECIDED: "no",
    State.ASKING: "no",
    State.BLOCKED: "never",
    State.POSTING: "in progress",
    State.POSTED: "terminal",
}

#: The only state a write may be claimed from. No state "may post" - a write
#: starts because `WriteClaimed` arrived while the proposal was here, and there
#: is no other door.
CLAIMABLE_FROM: Final = State.DECIDED


class StateError(RuntimeError):
    """Something asked this machine for a move it does not have.

    A `RuntimeError` rather than a `ValueError`, following `wall.py`: the data
    may be perfectly fine. What is wrong is WHEN, or by WHOM, and no correction
    to the numbers fixes it.
    """


class RejectedTransition(StateError):
    """This event cannot happen to this proposal, here, now."""


class UnrecordedMutation(StateError):
    """A history that is not a thing that could have happened.

    Raised on construction, never on `apply`. Either the rows do not join up, or
    one of them is not a legal transition under the table.
    """


class BrokenGuarantee(StateError):
    """A post-condition or invariant this module promises did not hold.

    Nothing in normal operation raises this. It exists so that if one ever does
    fail the answer is a loud stop rather than a proposal that quietly reports a
    state it did not earn.
    """


def _demand_an_injected_timestamp(at: object) -> datetime.datetime:
    """The caller's clock, or a refusal. This module owns no clock at all."""
    if not isinstance(at, datetime.datetime):
        raise TypeError(
            f"a transition needs the timestamp the caller supplies, not "
            f"{type(at).__name__}. This module never reads a clock, because two "
            "runs of the same walk have to produce identical rows."
        )
    if at.utcoffset() is None:
        raise ValueError(
            f"{at!r} carries no time zone. An hour nobody can place sorts two "
            "rows into the wrong order, and the order is the whole evidence. "
            "Guessing UTC here would be inventing the one fact that matters."
        )
    return at


@dataclass(frozen=True)
class AuditRow:
    """One transition, and everything needed to believe it later.

    The five the owner named - event, timestamp, before, after, actor - plus the
    sentence a person reads and the operation id a write claimed. Frozen, like
    every result type in this package: a record that can be edited afterwards is
    not evidence.
    """

    event: Event
    at: datetime.datetime
    before: State
    after: State
    actor: Actor
    reason: str = ""
    operation_id: str = ""


@dataclass(frozen=True)
class Payload:
    """What the caller supplied with an event, validated once.

    Built by `apply` from its arguments and rebuilt by the replay from a
    recorded row, so a forged row's timestamp and actor face exactly the checks
    a live call faces.
    """

    event: Event
    at: datetime.datetime
    actor: Actor
    reason: str = ""
    operation_id: str = ""

    def __post_init__(self) -> None:
        # `type(...) is not T` rather than `isinstance`, following `wall.py`.
        # Both are StrEnums, so `isinstance("ConservationPassed", Event)` is
        # False but every one of these values compares equal to its own string -
        # and pyright flags the `isinstance` form as redundant against the
        # annotation while leaving this one alone. The check is not redundant at
        # run time: annotations are not enforced, and a tool call or a CSV row
        # arrives untyped.
        if type(self.event) is not Event:
            raise TypeError(
                f"{self.event!r} is not one of the thirteen events. A proposal "
                "moves for a named reason or it does not move."
            )
        _demand_an_injected_timestamp(self.at)
        if type(self.actor) is not Actor:
            raise TypeError(
                f"{self.actor!r} is not an Actor. There are exactly two - "
                f"{Actor.ACCOUNTANT_DAD.value} and {Actor.OPERATOR.value} - and "
                "a free string here would put an unvalidated name in the row "
                "where the accountability lives."
            )


# ---- everything about a proposal, derived from its rows ----------------------
#
# These are the only readers of an audit tuple. `Proposal`'s properties call
# them and so does the replay, which is what keeps a half-built history and a
# finished one answering the same questions the same way.

_GATE_OF: Final[Mapping[Event, Gate]] = {
    Event.CONSERVATION_PASSED: Gate.CONSERVATION,
    Event.VALIDATION_PASSED: Gate.VALIDATION,
}

_BAND_OF: Final[Mapping[Event, Band]] = {
    Event.CONFIDENCE_HIGH: Band.HIGH,
    Event.CONFIDENCE_MEDIUM: Band.MEDIUM,
    Event.CONFIDENCE_LOW: Band.LOW,
}


def _state_of(rows: Sequence[AuditRow]) -> State:
    return rows[-1].after if rows else INITIAL_STATE


def _gates_of(rows: Sequence[AuditRow]) -> frozenset[Gate]:
    return frozenset(_GATE_OF[r.event] for r in rows if r.event in _GATE_OF)


def _band_of(rows: Sequence[AuditRow]) -> Band | None:
    """The band, or None because nobody has assigned one yet.

    None is never read as LOW. "We have not scored this" and "we scored it badly"
    are different facts, and collapsing them is the coercion this whole package
    exists to refuse.
    """
    for row in reversed(rows):
        if row.event in _BAND_OF:
            return _BAND_OF[row.event]
    return None


def _answered_in(rows: Sequence[AuditRow]) -> bool:
    return any(row.event is Event.ANSWERED for row in rows)


def _operation_id_of(rows: Sequence[AuditRow]) -> str:
    for row in reversed(rows):
        if row.event is Event.WRITE_CLAIMED:
            return row.operation_id
    return ""


@dataclass(frozen=True)
class Proposal:
    """A bill on its way somewhere, and the proof of how far it got.

    Three fields and nothing else. Everything a caller wants to know - the
    state, the band, which gates passed, whether a person answered, which
    operation id is claimed - is a function of `audit`, so none of it can
    disagree with the record.
    """

    proposal_id: str
    amount_paise: int
    audit: tuple[AuditRow, ...] = ()

    def __post_init__(self) -> None:
        if not self.proposal_id.strip():
            raise ValueError(
                "a proposal must have an id. Every audit row is keyed by it, and "
                "a blank one makes two proposals' histories into one history."
            )
        # `type(...) is not int` rather than `isinstance`, exactly as
        # `wall.py::LedgerEntry.decided` does it and for the same two reasons.
        # It rejects `bool` - which IS an `int` in Python, so a flag passed
        # where an amount belonged would otherwise become a proposal for one
        # paisa - and pyright does not flag it as redundant against the
        # annotation. `conservation.py::_paise` refuses bools too; this matches
        # the house rule rather than inventing a second one.
        if type(self.amount_paise) is not int:
            raise TypeError(
                f"amount_paise must be a whole number of paise, not "
                f"{type(self.amount_paise).__name__}. Money is never a float "
                "and never a flag, and None means nobody read it - which is not "
                "the same as zero and is never quietly turned into it."
            )
        if self.amount_paise < 0:
            raise ValueError(
                f"a proposal cannot be for {self.amount_paise} paise. A negative "
                "amount is a correction, and this system does corrections by "
                "reversal, never by sign."
            )
        _replay(self.audit)

    @property
    def state(self) -> State:
        """Where this proposal is, read off its last row.

        A property and not a field, deliberately. See the module docstring: this
        is what makes an unrecorded state change unwritable rather than merely
        forbidden.
        """
        return _state_of(self.audit)

    @property
    def gates(self) -> frozenset[Gate]:
        return _gates_of(self.audit)

    @property
    def band(self) -> Band | None:
        return _band_of(self.audit)

    @property
    def answered(self) -> bool:
        return _answered_in(self.audit)

    @property
    def operation_id(self) -> str:
        """The operation id a write claimed, or empty because none has.

        It survives a failure on purpose: a write that failed is exactly the
        case where somebody has to go and look for that id in Tally.
        """
        return _operation_id_of(self.audit)

    @property
    def said(self) -> str:
        """The sentence explaining why the proposal is where it is now."""
        return self.audit[-1].reason if self.audit else ""

    @property
    def visited(self) -> tuple[State, ...]:
        """Every state this proposal has been in, starting where it started."""
        return (INITIAL_STATE, *(row.after for row in self.audit))

    @property
    def terminal(self) -> bool:
        return self.state in TERMINAL


# ---- the conditions, as named data --------------------------------------------
#
# Each is a name and a predicate. The name is what a refusal quotes, so a person
# reading "refused: an operation id was claimed" knows what to supply; the
# predicate is what actually runs. Prose in a docstring cannot be either.


@dataclass(frozen=True)
class Precondition:
    """What must already be true for an event to be allowed. Reads the rows the
    proposal had BEFORE the event, and the payload arriving with it."""

    name: str
    holds: Callable[[tuple[AuditRow, ...], Payload], bool]


@dataclass(frozen=True)
class Postcondition:
    """What the transition promises. Reads the rows INCLUDING the new one."""

    name: str
    holds: Callable[[tuple[AuditRow, ...]], bool]


@dataclass(frozen=True)
class Invariant:
    """What is true of every transition, before and after."""

    name: str
    holds: Callable[[Proposal, Proposal], bool]


def _gate_not_yet(gate: Gate) -> Precondition:
    return Precondition(
        f"{gate.value} has not been recorded yet",
        lambda rows, _payload: gate not in _gates_of(rows),
    )


def _gate_already(gate: Gate) -> Precondition:
    return Precondition(
        f"{gate.value} has already passed",
        lambda rows, _payload: gate in _gates_of(rows),
    )


BOTH_CHECKS_PASSED: Final = Precondition(
    "conservation and validation have both passed",
    lambda rows, _payload: _gates_of(rows) == BOTH_GATES,
)

A_WRITTEN_REASON: Final = Precondition(
    "a written reason was supplied",
    lambda _rows, payload: bool(payload.reason.strip()),
)

AN_OPERATION_ID: Final = Precondition(
    "an operation id was claimed",
    lambda _rows, payload: bool(payload.operation_id.strip()),
)

#: The rule that says WHY a write may be claimed at all, and the reason `asking`
#: is not a dead end: a high band posts on its own, a medium band needs a
#: person, and the person's answer is what supplies the missing certainty.
#:
#: It cannot fire through the table as it stands, because the only two doors
#: into `decided` are `ConfidenceHigh` and `Answered`. That was measured, not
#: assumed - a mutant that widened it to "any band at all" survived the whole
#: suite until `tests/test_state.py` began firing the predicate directly and
#: walking every path into `posting`. It is kept rather than deleted because it
#: is the guard that bites the day somebody adds a third door.
A_HIGH_BAND_OR_A_PERSON: Final = Precondition(
    "the band is high, or a person answered",
    lambda rows, _payload: _band_of(rows) is Band.HIGH or _answered_in(rows),
)


def _reaches(target: State) -> Postcondition:
    return Postcondition(
        f"the proposal is {target.value}",
        lambda rows: _state_of(rows) is target,
    )


def _gate_recorded(gate: Gate) -> Postcondition:
    return Postcondition(
        f"{gate.value} is recorded",
        lambda rows: gate in _gates_of(rows),
    )


BOTH_GATES_RECORDED: Final = Postcondition(
    "both gates are recorded",
    lambda rows: _gates_of(rows) == BOTH_GATES,
)

THE_REASON_IS_WRITTEN_DOWN: Final = Postcondition(
    "the reason is written down",
    lambda rows: bool(rows[-1].reason.strip()),
)

A_BAND_IS_ASSIGNED: Final = Postcondition(
    "a band is assigned",
    lambda rows: _band_of(rows) is not None,
)

THE_ANSWER_IS_RECORDED: Final = Postcondition(
    "the answer is recorded",
    lambda rows: _answered_in(rows),
)

THE_OPERATION_ID_IS_RECORDED: Final = Postcondition(
    "the operation id is recorded",
    lambda rows: bool(_operation_id_of(rows)),
)


#: The four the owner named, plus two this module needs to keep its own
#: promises. Every rule carries all six; a test asserts that rather than
#: trusting it, because a rule that quietly dropped one would be the rule that
#: needed it.
#:
#: None of the four can fire through `apply` as the table stands today - the
#: table already forbids the shapes they refuse. That is the point of an
#: invariant and also its hazard: a guard nobody has ever seen fail is a guard
#: nobody has tested. Each is a plain predicate for exactly that reason, and
#: `tests/test_state.py` hands each one the pair it exists to refuse.
GLOBAL_INVARIANTS: Final[tuple[Invariant, ...]] = (
    Invariant(
        "the proposal id never changes",
        lambda before, after: before.proposal_id == after.proposal_id,
    ),
    Invariant(
        "the amount never changes and never goes negative",
        lambda before, after: (
            after.amount_paise == before.amount_paise and after.amount_paise >= 0
        ),
    ),
    Invariant(
        "blocked is terminal and has no outgoing edge",
        lambda before, _after: before.state is not State.BLOCKED,
    ),
    Invariant(
        "posted is terminal and has no outgoing edge",
        lambda before, _after: before.state is not State.POSTED,
    ),
    Invariant(
        "a write is never claimed by something unchecked",
        lambda before, after: (
            after.state is not State.POSTING or State.CHECKED in before.visited
        ),
    ),
    Invariant(
        "the transition added exactly one audit row",
        lambda before, after: len(after.audit) == len(before.audit) + 1,
    ),
)


@dataclass(frozen=True)
class Rule:
    """One edge of the machine, with its three obligations attached.

    Every rule carries at least one pre-condition, at least one post-condition
    and at least one invariant. That is the owner's requirement and a test reads
    it off this table rather than off a promise in a docstring.
    """

    event: Event
    frm: tuple[State, ...]
    to: State
    pre: tuple[Precondition, ...]
    post: tuple[Postcondition, ...]
    invariants: tuple[Invariant, ...]


def _rule(
    event: Event,
    frm: tuple[State, ...],
    to: State,
    *,
    pre: tuple[Precondition, ...],
    post: tuple[Postcondition, ...] = (),
) -> Rule:
    """One edge. Reaching the destination is always a post-condition, so no rule
    can be written that promises nothing."""
    return Rule(
        event=event,
        frm=frm,
        to=to,
        pre=pre,
        post=(_reaches(to), *post),
        invariants=GLOBAL_INVARIANTS,
    )


#: The whole machine. Fifteen edges for thirteen events: the two gate events
#: each have two, because whether they finish the pair depends on what already
#: passed, and a rule whose destination is a conditional is a rule nobody can
#: read off the page.
#:
#: `HardRuleViolated` is deliberately absent from `posting`. A write is already
#: in flight with an operation id claimed in Tally; calling that proposal
#: blocked asserts that nothing was written, which at that moment nobody knows.
#: Only `WriteConfirmed` and `WriteFailed` are honest from there.
RULES: Final[tuple[Rule, ...]] = (
    _rule(
        Event.CONSERVATION_PASSED,
        (State.OBSERVED,),
        State.OBSERVED,
        pre=(_gate_not_yet(Gate.CONSERVATION), _gate_not_yet(Gate.VALIDATION)),
        post=(_gate_recorded(Gate.CONSERVATION),),
    ),
    _rule(
        Event.CONSERVATION_PASSED,
        (State.OBSERVED,),
        State.CHECKED,
        pre=(_gate_not_yet(Gate.CONSERVATION), _gate_already(Gate.VALIDATION)),
        post=(BOTH_GATES_RECORDED,),
    ),
    _rule(
        Event.VALIDATION_PASSED,
        (State.OBSERVED,),
        State.OBSERVED,
        pre=(_gate_not_yet(Gate.VALIDATION), _gate_not_yet(Gate.CONSERVATION)),
        post=(_gate_recorded(Gate.VALIDATION),),
    ),
    _rule(
        Event.VALIDATION_PASSED,
        (State.OBSERVED,),
        State.CHECKED,
        pre=(_gate_not_yet(Gate.VALIDATION), _gate_already(Gate.CONSERVATION)),
        post=(BOTH_GATES_RECORDED,),
    ),
    _rule(
        Event.CONSERVATION_FAILED,
        (State.OBSERVED,),
        State.BLOCKED,
        pre=(A_WRITTEN_REASON,),
        post=(THE_REASON_IS_WRITTEN_DOWN,),
    ),
    _rule(
        Event.VALIDATION_FAILED,
        (State.OBSERVED,),
        State.BLOCKED,
        pre=(A_WRITTEN_REASON,),
        post=(THE_REASON_IS_WRITTEN_DOWN,),
    ),
    _rule(
        Event.CONFIDENCE_HIGH,
        (State.CHECKED,),
        State.DECIDED,
        pre=(BOTH_CHECKS_PASSED,),
        post=(A_BAND_IS_ASSIGNED,),
    ),
    _rule(
        Event.CONFIDENCE_MEDIUM,
        (State.CHECKED,),
        State.ASKING,
        pre=(BOTH_CHECKS_PASSED,),
        post=(A_BAND_IS_ASSIGNED,),
    ),
    _rule(
        Event.CONFIDENCE_LOW,
        (State.CHECKED,),
        State.BLOCKED,
        pre=(BOTH_CHECKS_PASSED, A_WRITTEN_REASON),
        post=(A_BAND_IS_ASSIGNED, THE_REASON_IS_WRITTEN_DOWN),
    ),
    _rule(
        Event.ANSWERED,
        (State.ASKING,),
        State.DECIDED,
        pre=(A_WRITTEN_REASON,),
        post=(THE_ANSWER_IS_RECORDED,),
    ),
    _rule(
        Event.ANSWERS_EXHAUSTED,
        (State.ASKING,),
        State.BLOCKED,
        pre=(A_WRITTEN_REASON,),
        post=(THE_REASON_IS_WRITTEN_DOWN,),
    ),
    _rule(
        Event.WRITE_CLAIMED,
        (CLAIMABLE_FROM,),
        State.POSTING,
        pre=(AN_OPERATION_ID, A_HIGH_BAND_OR_A_PERSON),
        post=(THE_OPERATION_ID_IS_RECORDED,),
    ),
    _rule(
        Event.WRITE_CONFIRMED,
        (State.POSTING,),
        State.POSTED,
        pre=(A_WRITTEN_REASON,),
        post=(THE_REASON_IS_WRITTEN_DOWN,),
    ),
    _rule(
        Event.WRITE_FAILED,
        (State.POSTING,),
        State.BLOCKED,
        pre=(A_WRITTEN_REASON,),
        post=(THE_REASON_IS_WRITTEN_DOWN,),
    ),
    _rule(
        Event.HARD_RULE_VIOLATED,
        (State.OBSERVED, State.CHECKED, State.DECIDED, State.ASKING),
        State.BLOCKED,
        pre=(A_WRITTEN_REASON,),
        post=(THE_REASON_IS_WRITTEN_DOWN,),
    ),
)


def demand_all_held(
    failed: Sequence[str], *, kind: str, proposal_id: str, event: Event
) -> None:
    """Raise if any named guarantee did not hold. The one place that happens.

    Public, and fired directly by a test, because nothing in normal operation
    reaches the raise: the table already forbids every shape the guarantees
    refuse. An untested raise is the one that turns out to be a NameError the
    first time it is needed.
    """
    if failed:
        raise BrokenGuarantee(
            f"{event.value} on proposal {proposal_id!r} broke a {kind} this "
            f"module promises: " + ", ".join(failed) + ". Nothing is returned; "
            "a proposal reporting a state it did not earn is worse than a stop."
        )


def _select(rows: tuple[AuditRow, ...], payload: Payload) -> Rule:
    """The one rule that applies, or a refusal that says why.

    Two refusals, and they answer different questions. No candidate at all means
    this event cannot happen from this state - a wrong door. Candidates whose
    pre-conditions did not hold means the right door with something missing, and
    the missing thing is named so a person can supply it instead of going to
    read the table.
    """
    state = _state_of(rows)
    candidates = [r for r in RULES if r.event is payload.event and state in r.frm]
    if not candidates:
        terminal = (
            " and nothing happens to a proposal once it is terminal"
            if (state in TERMINAL)
            else ""
        )
        raise RejectedTransition(
            f"{payload.event.value} cannot happen to a proposal that is "
            f"{state.value}{terminal}. The machine has one door per event and "
            "this is not one of them."
        )
    unmet: list[str] = []
    for rule in candidates:
        missing = [p.name for p in rule.pre if not p.holds(rows, payload)]
        if not missing:
            return rule
        unmet.extend(name for name in missing if name not in unmet)
    raise RejectedTransition(
        f"{payload.event.value} is refused on a proposal that is {state.value} "
        "because " + ", and ".join(unmet) + "."
    )


def _replay(rows: tuple[AuditRow, ...]) -> None:
    """Refuse a history that is not a thing that could have happened.

    Two questions, both needed. Does the chain join up - does each row start
    where the last one ended, beginning at `observed`? And is each row a legal
    transition under the table?

    The first alone is not enough, and that gap is the whole reason this
    function exists. A forger who writes `before=<wherever the proposal is>`
    passes a chain check trivially; it is the rule replay that catches a row
    walking from `observed` straight into `posted`.
    """
    state = INITIAL_STATE
    for position, row in enumerate(rows):
        if type(row) is not AuditRow:
            raise UnrecordedMutation(
                f"row {position} of this history is a "
                f"{type(row).__name__}, not an audit row."
            )
        if row.before is not state:
            raise UnrecordedMutation(
                f"row {position} says it started from {row.before.value}, and "
                f"the chain had reached {state.value}. A transition happened "
                "that no row records, or this row was never part of this history."
            )
        payload = Payload(
            event=row.event,
            at=row.at,
            actor=row.actor,
            reason=row.reason,
            operation_id=row.operation_id,
        )
        try:
            rule = _select(rows[:position], payload)
        except RejectedTransition as exc:
            raise UnrecordedMutation(
                f"row {position} records {row.event.value} from "
                f"{row.before.value}, which is not a move this machine has: {exc}"
            ) from exc
        if rule.to is not row.after:
            raise UnrecordedMutation(
                f"row {position} records {row.event.value} landing in "
                f"{row.after.value}; from {row.before.value} it lands in "
                f"{rule.to.value}. A real event with an edited destination is "
                "still a forgery."
            )
        state = row.after


def apply(
    proposal: Proposal,
    event: Event,
    *,
    at: datetime.datetime,
    actor: Actor,
    reason: str = "",
    operation_id: str = "",
) -> Proposal:
    """Move a proposal, or refuse. The only way a proposal ever changes.

    Returns a new `Proposal`; the one passed in is untouched, so a rejected
    transition cannot leave a half-moved object behind for the next caller to
    find. `at` is required and never defaulted - see the module docstring.
    """
    if operation_id and event is not Event.WRITE_CLAIMED:
        raise ValueError(
            f"{event!r} does not claim a write, so it must not carry the "
            f"operation id {operation_id!r}. Dropping it silently is how a "
            "write gets attributed to an operation nobody claimed."
        )
    payload = Payload(
        event=event, at=at, actor=actor, reason=reason, operation_id=operation_id
    )
    rule = _select(proposal.audit, payload)
    row = AuditRow(
        event=event,
        at=at,
        before=proposal.state,
        after=rule.to,
        actor=actor,
        reason=reason,
        operation_id=operation_id,
    )
    moved = Proposal(
        proposal_id=proposal.proposal_id,
        amount_paise=proposal.amount_paise,
        audit=(*proposal.audit, row),
    )
    demand_all_held(
        [c.name for c in rule.post if not c.holds(moved.audit)],
        kind="post-condition",
        proposal_id=proposal.proposal_id,
        event=event,
    )
    demand_all_held(
        [i.name for i in rule.invariants if not i.holds(proposal, moved)],
        kind="invariant",
        proposal_id=proposal.proposal_id,
        event=event,
    )
    return moved
