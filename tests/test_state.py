"""The proposal state machine: seven states, thirteen events, and no other door.

WHY THIS FILE EXISTS
--------------------
A proposal is the thing that walks from "the reader thinks the bill says this"
to "this is now in somebody's books". Before this module that walk had no name.
Each stage was a local variable in whichever function happened to be running, so
there was no way to ask a proposal where it was, no way to prove it had not
skipped a stage, and no row anywhere saying it moved.

That is how a write gets claimed on something nobody checked. Not by anyone
deciding to skip the check - by a code path that never had a place to record
that the check happened, so nothing could notice it was missing.

So the walk is a machine, and the machine is written down here:

    observed   the reader has spoken, nothing is checked
    checked    conservation AND validation both passed
    decided    a confidence band has been assigned
    asking     a question is outstanding with a person
    blocked    TERMINAL, and it carries a written reason
    posting    a write is in flight and an operation id is claimed
    posted     written and read back - TERMINAL

Nothing may post from any of them. `posting` is a write already in flight, and
`blocked` may never post at all.

WHY THE AUDIT IS THE STATE, AND NOT A REPORT ABOUT IT
-----------------------------------------------------
`Proposal.state` is a PROPERTY computed from the audit rows. There is no state
field to assign, so "the state changed and nothing recorded it" is not a bug
that has to be caught - it is a sentence that cannot be written. `replace()`
refuses `state=` because it is not a field, and a hand-built audit is replayed
against the rule table on construction, so a forged row has to be a legal
transition or it does not load at all.

This is `reversal.py`'s doctrine - the log IS the state, one line at a time -
applied one layer earlier, before anything reaches Tally.

WHY THE TIMESTAMP IS AN ARGUMENT
---------------------------------
`apply` takes `at`. It never reads a clock, and a test scans the module's syntax
tree to prove no call to `now`, `utcnow` or `today` ever appears in it. A
machine whose audit rows depend on when the test happened to run cannot be
compared across two runs, and comparing two runs is the only way to catch a
transition that fires when it should not.

WHAT THIS FILE DOES NOT PROVE
------------------------------
It does not prove the conservation laws ran, that the validation was any good,
or that the band was computed from anything real. This module is handed
`ConservationPassed` and believes it. All it proves is that the SEQUENCE is
legal: that nothing reaches `posting` without having been `checked`, that
`blocked` is a wall, and that every move left a row behind.

It also cannot stop `object.__setattr__`. Anything willing to write that line
into the repository can put a proposal in any state it likes, and no runtime
guard in Python stops it. What stops it is that a reviewer reads it in the diff.

NO NETWORK, NO FIXTURES, NO IO, NO CLOCK. Every test here is a table and a
timestamp somebody typed.
"""

from __future__ import annotations

import ast
import dataclasses
import datetime
import itertools
import pathlib

import pytest

from accountant.cage.state import (
    GLOBAL_INVARIANTS,
    INITIAL_STATE,
    RULES,
    TERMINAL,
    WRITE_PERMISSION,
    AuditRow,
    Band,
    BrokenGuarantee,
    Event,
    Gate,
    Invariant,
    Payload,
    Proposal,
    RejectedTransition,
    State,
    UnrecordedMutation,
    apply,
    demand_all_held,
)
from accountant.schema import Actor

SYSTEM = Actor.ACCOUNTANT_DAD
PERSON = Actor.OPERATOR

#: One typed instant. Every timestamp in this file is derived from it, so two
#: runs of the suite produce byte-identical audit rows.
NOON = datetime.datetime(2026, 8, 12, 12, 0, 0, tzinfo=datetime.UTC)


def _tick(seconds: int) -> datetime.datetime:
    return NOON + datetime.timedelta(seconds=seconds)


def _observed() -> Proposal:
    return Proposal(proposal_id="p-1", amount_paise=118_000)


def _checked() -> Proposal:
    half = apply(_observed(), Event.CONSERVATION_PASSED, at=_tick(1), actor=SYSTEM)
    return apply(half, Event.VALIDATION_PASSED, at=_tick(2), actor=SYSTEM)


def _decided() -> Proposal:
    return apply(_checked(), Event.CONFIDENCE_HIGH, at=_tick(3), actor=SYSTEM)


def _asking() -> Proposal:
    return apply(_checked(), Event.CONFIDENCE_MEDIUM, at=_tick(3), actor=SYSTEM)


def _posting() -> Proposal:
    return apply(
        _decided(),
        Event.WRITE_CLAIMED,
        at=_tick(4),
        actor=SYSTEM,
        operation_id="op-7",
    )


def _posted() -> Proposal:
    return apply(
        _posting(),
        Event.WRITE_CONFIRMED,
        at=_tick(5),
        actor=SYSTEM,
        reason="read back in Tally: voucher op-7 is there for 118000 paise",
    )


def _blocked() -> Proposal:
    return apply(
        _observed(),
        Event.HARD_RULE_VIOLATED,
        at=_tick(1),
        actor=SYSTEM,
        reason="the period this bill falls in is closed",
    )


def _half_gated() -> Proposal:
    """One gate passed and the other not - still `observed`, and the only
    history in which two rules share a state and an event."""
    return apply(_observed(), Event.CONSERVATION_PASSED, at=_tick(1), actor=SYSTEM)


def _answered_proposal() -> Proposal:
    """`decided` by way of a person, rather than by a high band. The two reach
    the same state carrying different permission to write."""
    return apply(
        _asking(),
        Event.ANSWERED,
        at=_tick(4),
        actor=PERSON,
        reason="the operator confirmed the party is Sharma Traders",
    )


#: A builder for every one of the seven, so the rejection matrix below can put a
#: proposal in any state without knowing how it got there.
IN_STATE = {
    State.OBSERVED: _observed,
    State.CHECKED: _checked,
    State.DECIDED: _decided,
    State.ASKING: _asking,
    State.BLOCKED: _blocked,
    State.POSTING: _posting,
    State.POSTED: _posted,
}

#: Every (state, event) the rule table says may happen, derived from the table
#: rather than typed out again. A second hand-written copy drifts from the first
#: and then asserts the drift.
ALLOWED_PAIRS = {(state, rule.event) for rule in RULES for state in rule.frm}


def _send(proposal: Proposal, event: Event) -> Proposal:
    """Fire `event` with everything any rule could ask for.

    The matrix tests want the TABLE to decide, not a missing argument, so this
    supplies a reason always and an operation id exactly where one is legal.
    """
    return apply(
        proposal,
        event,
        at=_tick(9),
        actor=SYSTEM,
        reason="a sentence a person can read",
        operation_id="op-9" if event is Event.WRITE_CLAIMED else "",
    )


# ---- the shape of the machine ------------------------------------------------


def test_there_are_exactly_the_seven_states_the_owner_named() -> None:
    assert [s.value for s in State] == [
        "observed",
        "checked",
        "decided",
        "asking",
        "blocked",
        "posting",
        "posted",
    ]


def test_there_are_exactly_thirteen_events_and_no_fourteenth() -> None:
    """Thirteen is the owner's number. A fourteenth event added quietly is a
    fourteenth way to move a proposal, and the whole point of the table is that
    the ways are countable."""
    assert len(list(Event)) == 13


def test_every_event_has_at_least_one_rule_in_the_table() -> None:
    """An event with no rule is a name that does nothing - it would raise on
    every proposal in every state, which is a dead branch pretending to be a
    feature."""
    assert {rule.event for rule in RULES} == set(Event)


def test_every_state_is_reachable_from_observed() -> None:
    """A state nothing can reach cannot appear in any history, so it documents a
    thing that never happens."""
    reached = {INITIAL_STATE}
    frontier = [INITIAL_STATE]
    while frontier:
        for rule in RULES:
            if frontier[0] in rule.frm and rule.to not in reached:
                reached.add(rule.to)
                frontier.append(rule.to)
        frontier.pop(0)
    assert reached == set(State)


def test_every_rule_names_a_precondition_a_postcondition_and_an_invariant() -> None:
    """The owner's requirement, checked as data rather than trusted as prose."""
    for rule in RULES:
        assert rule.pre, rule.event
        assert rule.post, rule.event
        assert rule.invariants, rule.event


def test_every_rule_carries_all_the_global_invariants() -> None:
    """THE CONTROL on the test above. A rule could satisfy "at least one
    invariant" while quietly dropping the one that stops it, so the globals are
    required on every rule by name."""
    for rule in RULES:
        assert set(GLOBAL_INVARIANTS) <= set(rule.invariants), rule.event


def test_at_most_one_rule_ever_matches_a_history_and_an_event() -> None:
    """Two rules matching the same moment means the destination depends on the
    order somebody typed them in, which is not a machine, it is a coincidence.

    Checked against every history this file can build, not against the table in
    the abstract: for each one, every event is offered to every candidate rule
    and at most one may accept."""
    for build in (*IN_STATE.values(), _half_gated, _answered_proposal):
        proposal = build()
        for event in Event:
            payload = Payload(
                event=event,
                at=_tick(9),
                actor=SYSTEM,
                reason="a sentence",
                operation_id="op-9" if event is Event.WRITE_CLAIMED else "",
            )
            accepted = [
                rule
                for rule in RULES
                if rule.event is event
                and proposal.state in rule.frm
                and all(p.holds(proposal.audit, payload) for p in rule.pre)
            ]
            assert len(accepted) <= 1, (build.__name__, event)


def test_the_write_permission_table_covers_every_state_and_blocks_one_forever() -> None:
    """The owner's third column, kept as data so it can be read back."""
    assert set(WRITE_PERMISSION) == set(State)
    assert WRITE_PERMISSION[State.BLOCKED] == "never"
    assert WRITE_PERMISSION[State.POSTING] == "in progress"


def test_the_two_terminal_states_are_blocked_and_posted() -> None:
    assert set(TERMINAL) == {State.BLOCKED, State.POSTED}


# ---- one test per allowed transition -----------------------------------------


def test_a_new_proposal_is_observed_with_nothing_checked_and_no_band() -> None:
    proposal = _observed()
    assert proposal.state is State.OBSERVED
    assert proposal.gates == frozenset()
    assert proposal.band is None
    assert proposal.operation_id == ""
    assert proposal.said == ""


def test_an_unassigned_band_is_none_and_is_never_read_as_the_lowest_one() -> None:
    """ "We have not scored this" and "we scored it badly" are different facts.
    Collapsing them is the coercion this whole package exists to refuse, and it
    would turn every unread bill into one the machine has an opinion about."""
    assert _observed().band is None
    assert _checked().band is None


def test_only_the_two_terminal_states_report_themselves_as_terminal() -> None:
    for state, build in IN_STATE.items():
        assert build().terminal is (state in TERMINAL), state


def test_conservation_passing_on_its_own_leaves_the_proposal_observed() -> None:
    """One law passing is not "checked". `checked` means conservation AND
    validation, and a machine that moved on the first of the two would let a
    bill that balances but names no party through the gate."""
    proposal = apply(_observed(), Event.CONSERVATION_PASSED, at=_tick(1), actor=SYSTEM)
    assert proposal.state is State.OBSERVED
    assert proposal.gates == {Gate.CONSERVATION}


def test_validation_passing_on_its_own_leaves_the_proposal_observed_too() -> None:
    proposal = apply(_observed(), Event.VALIDATION_PASSED, at=_tick(1), actor=SYSTEM)
    assert proposal.state is State.OBSERVED
    assert proposal.gates == {Gate.VALIDATION}


def test_conservation_then_validation_reaches_checked() -> None:
    assert _checked().state is State.CHECKED


def test_validation_then_conservation_reaches_checked_as_well() -> None:
    """Neither gate depends on the other, so neither order is privileged. A
    machine that only accepted one order would refuse a perfectly checked bill
    because two functions ran in the wrong sequence."""
    first = apply(_observed(), Event.VALIDATION_PASSED, at=_tick(1), actor=SYSTEM)
    both = apply(first, Event.CONSERVATION_PASSED, at=_tick(2), actor=SYSTEM)
    assert both.state is State.CHECKED


def test_a_failed_conservation_law_blocks_the_proposal() -> None:
    blocked = apply(
        _observed(),
        Event.CONSERVATION_FAILED,
        at=_tick(1),
        actor=SYSTEM,
        reason="debits are 100000 paise against 100001 credited",
    )
    assert blocked.state is State.BLOCKED
    assert "100001" in blocked.said


def test_a_failed_validation_blocks_the_proposal() -> None:
    blocked = apply(
        _observed(),
        Event.VALIDATION_FAILED,
        at=_tick(1),
        actor=SYSTEM,
        reason="the party on this bill is not a ledger we have ever seen",
    )
    assert blocked.state is State.BLOCKED


def test_a_high_band_moves_a_checked_proposal_to_decided() -> None:
    decided = _decided()
    assert decided.state is State.DECIDED
    assert decided.band is Band.HIGH


def test_a_medium_band_puts_a_question_out_rather_than_deciding_alone() -> None:
    """A medium band is the product asking instead of guessing. The band is
    assigned at the same moment - `asking` is not a state with no opinion, it is
    a state with an opinion that needs a person's answer beside it."""
    asking = _asking()
    assert asking.state is State.ASKING
    assert asking.band is Band.MEDIUM


def test_a_low_band_blocks_because_a_guess_is_not_worth_asking_about() -> None:
    """A question built on a low band invites a person to rubber-stamp a guess,
    and their yes then carries the authority their reading never had."""
    blocked = apply(
        _checked(),
        Event.CONFIDENCE_LOW,
        at=_tick(3),
        actor=SYSTEM,
        reason="the total scored 0.0: the amount field did not parse as paise",
    )
    assert blocked.state is State.BLOCKED
    assert blocked.band is Band.LOW


def test_answering_the_outstanding_question_moves_it_to_decided() -> None:
    answered = apply(
        _asking(),
        Event.ANSWERED,
        at=_tick(4),
        actor=PERSON,
        reason="the operator confirmed the party is Sharma Traders",
    )
    assert answered.state is State.DECIDED
    assert answered.answered is True


def test_running_out_of_answers_blocks_rather_than_deciding_anyway() -> None:
    exhausted = apply(
        _asking(),
        Event.ANSWERS_EXHAUSTED,
        at=_tick(4),
        actor=SYSTEM,
        reason="asked three times and nobody answered; nothing is posted on this",
    )
    assert exhausted.state is State.BLOCKED


def test_claiming_a_write_moves_a_decided_proposal_to_posting() -> None:
    posting = _posting()
    assert posting.state is State.POSTING
    assert posting.operation_id == "op-7"


def test_an_answered_medium_band_may_claim_a_write() -> None:
    """The whole reason `asking` exists. A medium band cannot post by itself and
    a person's answer is what supplies the missing certainty - if an answered
    proposal still could not post, asking would be a dead end."""
    answered = apply(
        _asking(), Event.ANSWERED, at=_tick(4), actor=PERSON, reason="party confirmed"
    )
    posting = apply(
        answered, Event.WRITE_CLAIMED, at=_tick(5), actor=SYSTEM, operation_id="op-8"
    )
    assert posting.state is State.POSTING


def test_a_confirmed_write_that_was_read_back_reaches_posted() -> None:
    posted = _posted()
    assert posted.state is State.POSTED
    assert "read back" in posted.said


def test_a_failed_write_blocks_and_keeps_the_operation_id_it_claimed() -> None:
    """The operation id survives the failure on purpose: a write that failed is
    exactly the case where somebody has to go and look for that id in Tally."""
    failed = apply(
        _posting(),
        Event.WRITE_FAILED,
        at=_tick(5),
        actor=SYSTEM,
        reason="Tally answered with an error and the trial balance did not move",
    )
    assert failed.state is State.BLOCKED
    assert failed.operation_id == "op-7"


def test_a_hard_rule_violation_blocks_from_every_state_with_a_choice_left() -> None:
    """One event, four doors, because a hard rule can be discovered at any point
    before the write leaves - a closed period, a duplicate, a forbidden ledger."""
    for state in (State.OBSERVED, State.CHECKED, State.DECIDED, State.ASKING):
        blocked = apply(
            IN_STATE[state](),
            Event.HARD_RULE_VIOLATED,
            at=_tick(6),
            actor=SYSTEM,
            reason="this bill was already posted last Tuesday",
        )
        assert blocked.state is State.BLOCKED, state


# ---- the rejected transitions ------------------------------------------------


def test_the_table_is_the_only_door_and_every_other_pair_is_refused() -> None:
    """The exhaustive negative: all seven states against all thirteen events,
    minus the edges the table declares. Written as a matrix rather than as
    ninety-one hand-typed tests because a hand-typed list is where the one
    forgotten pair hides."""
    refused = 0
    for state in State:
        for event in Event:
            if (state, event) in ALLOWED_PAIRS:
                continue
            with pytest.raises(RejectedTransition):
                _send(IN_STATE[state](), event)
            refused += 1
    assert refused == len(State) * len(Event) - len(ALLOWED_PAIRS)


def test_the_control_every_pair_the_table_declares_actually_goes_through() -> None:
    """THE CONTROL on the matrix above. A machine that refused EVERYTHING would
    pass that test perfectly, so this fires every declared edge and requires it
    to move."""
    for state, event in ALLOWED_PAIRS:
        moved = _send(IN_STATE[state](), event)
        assert moved.audit[-1].before is state
        assert moved.audit[-1].event is event


def test_blocked_is_terminal_and_has_no_outgoing_edge_at_all() -> None:
    """The single most important negative in the file. `blocked` is a wall, not
    a pause, and there is no event - not an answer, not a retry, not a hard rule
    reversed - that takes a proposal back out of it."""
    for event in Event:
        with pytest.raises(RejectedTransition):
            _send(_blocked(), event)


def test_posted_is_terminal_too_because_the_books_already_changed() -> None:
    for event in Event:
        with pytest.raises(RejectedTransition):
            _send(_posted(), event)


def test_no_rule_in_the_table_leaves_a_terminal_state() -> None:
    """THE CONTROL on the two tests above, read off the table instead of fired.
    Those tests would still pass if `_blocked()` silently built something that
    was not blocked; this one cannot."""
    for rule in RULES:
        assert not set(rule.frm) & set(TERMINAL), rule.event


def test_a_write_cannot_be_claimed_from_observed() -> None:
    with pytest.raises(RejectedTransition):
        _send(_observed(), Event.WRITE_CLAIMED)


def test_a_write_cannot_be_claimed_straight_out_of_checked() -> None:
    """Checked is not decided. Skipping the band would post a bill whose
    arithmetic works and whose figures nobody was sure they read."""
    with pytest.raises(RejectedTransition):
        _send(_checked(), Event.WRITE_CLAIMED)


def test_a_band_cannot_be_assigned_before_both_checks_have_passed() -> None:
    half = apply(_observed(), Event.CONSERVATION_PASSED, at=_tick(1), actor=SYSTEM)
    with pytest.raises(RejectedTransition):
        _send(half, Event.CONFIDENCE_HIGH)


def test_an_answer_to_a_question_nobody_asked_is_refused() -> None:
    with pytest.raises(RejectedTransition):
        _send(_decided(), Event.ANSWERED)


def test_a_write_cannot_be_confirmed_before_it_was_ever_claimed() -> None:
    """The one that would make `posted` a lie: a proposal reporting itself
    written and read back with no operation id and nothing in flight."""
    with pytest.raises(RejectedTransition):
        _send(_decided(), Event.WRITE_CONFIRMED)


def test_a_hard_rule_violation_is_refused_while_a_write_is_in_flight() -> None:
    """The exception to the hard rule's four doors, and the reason for it: an
    operation id is already claimed in Tally. Calling that proposal `blocked`
    says nothing was written, which nobody knows. Only `WriteConfirmed` and
    `WriteFailed` are honest from here."""
    with pytest.raises(RejectedTransition):
        _send(_posting(), Event.HARD_RULE_VIOLATED)


def test_the_same_gate_cannot_be_recorded_twice() -> None:
    """Two conservation passes are one law run twice, not two laws. Counting it
    twice would reach `checked` with the validation never run."""
    once = apply(_observed(), Event.CONSERVATION_PASSED, at=_tick(1), actor=SYSTEM)
    with pytest.raises(RejectedTransition):
        apply(once, Event.CONSERVATION_PASSED, at=_tick(2), actor=SYSTEM)


def test_a_write_claimed_without_an_operation_id_is_refused() -> None:
    """`posting` means an operation id is claimed. Without one there is nothing
    to reverse by, nothing to read back, and nothing to search Tally for."""
    with pytest.raises(RejectedTransition):
        apply(_decided(), Event.WRITE_CLAIMED, at=_tick(4), actor=SYSTEM)


def test_a_medium_band_with_no_answer_may_not_claim_a_write() -> None:
    """Reached by hand rather than through `asking`, because the point is the
    precondition and not the route: a band below high needs a person."""
    with pytest.raises(RejectedTransition):
        apply(
            _asking(),
            Event.WRITE_CLAIMED,
            at=_tick(4),
            actor=SYSTEM,
            operation_id="op-3",
        )


def test_blocking_a_proposal_without_a_written_reason_is_refused() -> None:
    """`blocked` is TERMINAL WITH A WRITTEN REASON. A wall with no sentence on
    it is a proposal that stopped for reasons nobody can reconstruct."""
    for event in (
        Event.CONSERVATION_FAILED,
        Event.VALIDATION_FAILED,
        Event.HARD_RULE_VIOLATED,
    ):
        with pytest.raises(RejectedTransition):
            apply(_observed(), event, at=_tick(1), actor=SYSTEM)


def test_blocking_on_whitespace_is_the_same_as_blocking_on_nothing() -> None:
    with pytest.raises(RejectedTransition):
        apply(
            _observed(),
            Event.HARD_RULE_VIOLATED,
            at=_tick(1),
            actor=SYSTEM,
            reason="   \n\t ",
        )


def test_an_answer_with_no_words_in_it_is_refused() -> None:
    """A person clicking yes with nothing recorded is the answer that later
    cannot be distinguished from the system deciding on its own."""
    with pytest.raises(RejectedTransition):
        apply(_asking(), Event.ANSWERED, at=_tick(4), actor=PERSON)


def test_a_write_confirmed_with_no_read_back_evidence_is_refused() -> None:
    """`posted` means written AND READ BACK. The reason field is where the
    read-back is written down, so an empty one means nobody looked."""
    with pytest.raises(RejectedTransition):
        apply(_posting(), Event.WRITE_CONFIRMED, at=_tick(5), actor=SYSTEM)


def test_the_refusal_names_the_precondition_that_was_not_met() -> None:
    """A bare "rejected" sends a person to read the rule table. Naming the unmet
    precondition tells them what to supply instead."""
    with pytest.raises(RejectedTransition, match="operation id"):
        apply(_decided(), Event.WRITE_CLAIMED, at=_tick(4), actor=SYSTEM)


def test_the_refusal_from_a_terminal_state_says_it_is_terminal() -> None:
    with pytest.raises(RejectedTransition, match="terminal"):
        _send(_posted(), Event.WRITE_CLAIMED)


def test_an_operation_id_on_an_event_that_claims_no_write_is_refused() -> None:
    """Dropping it silently is how a write gets attributed to an operation
    nobody claimed. The caller is confused; say so."""
    with pytest.raises(ValueError, match="operation id"):
        apply(
            _observed(),
            Event.CONSERVATION_PASSED,
            at=_tick(1),
            actor=SYSTEM,
            operation_id="op-4",
        )


def test_something_that_is_not_one_of_the_thirteen_events_is_refused() -> None:
    with pytest.raises(TypeError):
        apply(_observed(), "ConservationPassed", at=_tick(1), actor=SYSTEM)  # type: ignore[arg-type]


def test_an_actor_that_is_not_one_of_the_two_is_refused() -> None:
    """`Actor` has exactly two values and no third. A free string here would put
    an unvalidated name in the audit row where the accountability lives."""
    with pytest.raises(TypeError):
        apply(
            _observed(),
            Event.CONSERVATION_PASSED,
            at=_tick(1),
            actor="tanveer",  # type: ignore[arg-type]
        )


# ---- a state mutation without an event -----------------------------------


def test_the_state_cannot_be_assigned_because_it_is_derived_from_the_audit() -> None:
    """The owner's requirement, met structurally rather than by a check. There
    is no state field to assign, so this is not a guard that could be forgotten
    - it is a sentence Python will not execute."""
    proposal = _observed()
    with pytest.raises(AttributeError):
        proposal.state = State.POSTED  # type: ignore[misc]


def test_replace_refuses_to_set_a_state_because_state_is_not_a_field() -> None:
    """THE CONTROL on the test above. A frozen dataclass still lets `replace()`
    build a new one with any field changed, so a state FIELD would have been
    settable without an event by anyone who knew that."""
    with pytest.raises(TypeError):
        dataclasses.replace(_observed(), state=State.POSTED)  # type: ignore[call-arg]


def test_an_audit_row_that_does_not_join_the_chain_is_refused() -> None:
    """A row claiming to start where the chain never was is what a reordered,
    duplicated or invented event looks like."""
    orphan = AuditRow(
        event=Event.WRITE_CONFIRMED,
        at=_tick(1),
        before=State.POSTING,
        after=State.POSTED,
        actor=SYSTEM,
        reason="read back",
    )
    with pytest.raises(UnrecordedMutation):
        Proposal(proposal_id="p-1", amount_paise=118_000, audit=(orphan,))


def test_a_forged_row_that_joins_the_chain_but_is_illegal_is_refused() -> None:
    """Joining up is not enough. This row starts exactly where the proposal is,
    so a chain check alone would load it - and it walks from `observed` straight
    into `posted`, which is the whole thing the table exists to forbid."""
    forged = AuditRow(
        event=Event.WRITE_CONFIRMED,
        at=_tick(1),
        before=State.OBSERVED,
        after=State.POSTED,
        actor=SYSTEM,
        reason="read back",
    )
    with pytest.raises(UnrecordedMutation):
        Proposal(proposal_id="p-1", amount_paise=118_000, audit=(forged,))


def test_a_row_with_a_legal_event_but_the_wrong_destination_is_refused() -> None:
    """`ConservationPassed` is a real event from `observed`; it does not go to
    `checked` on its own. A forger who copies a real event and edits only where
    it lands is caught by replaying the rule, not just the chain."""
    forged = AuditRow(
        event=Event.CONSERVATION_PASSED,
        at=_tick(1),
        before=State.OBSERVED,
        after=State.CHECKED,
        actor=SYSTEM,
    )
    with pytest.raises(UnrecordedMutation):
        Proposal(proposal_id="p-1", amount_paise=118_000, audit=(forged,))


def test_something_that_is_not_an_audit_row_at_all_is_refused() -> None:
    """A tuple of dicts loaded back out of JSON is the ordinary way this
    arrives. Reading `.before` off it would raise an AttributeError three frames
    away from the cause; refusing it here says what is wrong."""
    with pytest.raises(UnrecordedMutation, match="not an audit row"):
        Proposal(
            proposal_id="p-1",
            amount_paise=118_000,
            audit=({"event": "WriteConfirmed"},),  # type: ignore[arg-type]
        )


def test_a_history_that_does_not_start_at_observed_is_refused() -> None:
    forged = AuditRow(
        event=Event.WRITE_CONFIRMED,
        at=_tick(1),
        before=State.CHECKED,
        after=State.POSTED,
        actor=SYSTEM,
        reason="read back",
    )
    with pytest.raises(UnrecordedMutation):
        Proposal(proposal_id="p-1", amount_paise=118_000, audit=(forged,))


def test_the_control_a_real_history_reloads_without_complaint() -> None:
    """THE CONTROL on the four forgery tests. A constructor that refused EVERY
    audit would pass all of them and make the type useless."""
    posted = _posted()
    again = Proposal(
        proposal_id=posted.proposal_id,
        amount_paise=posted.amount_paise,
        audit=posted.audit,
    )
    assert again.state is State.POSTED
    assert again == posted


# ---- the global invariants ---------------------------------------------------


def test_the_proposal_id_is_the_same_at_every_step_of_a_full_walk() -> None:
    walk = [_observed(), _checked(), _decided(), _posting(), _posted()]
    assert {p.proposal_id for p in walk} == {"p-1"}


def test_the_control_the_id_invariant_fails_when_the_id_differs() -> None:
    """THE CONTROL. The invariant above can never fire through `apply`, which
    carries the id forward, so the only way to prove it bites is to hand it the
    pair it exists to refuse."""
    invariant = _invariant("the proposal id never changes")
    other = Proposal(proposal_id="p-2", amount_paise=118_000)
    assert invariant.holds(_observed(), _observed()) is True
    assert invariant.holds(_observed(), other) is False


def test_the_amount_is_unchanged_by_every_transition_in_a_full_walk() -> None:
    assert {p.amount_paise for p in (_observed(), _decided(), _posted())} == {118_000}


def test_the_control_the_amount_invariant_refuses_a_changed_amount() -> None:
    invariant = _invariant("the amount never changes and never goes negative")
    bigger = Proposal(proposal_id="p-1", amount_paise=118_001)
    assert invariant.holds(_observed(), _observed()) is True
    assert invariant.holds(_observed(), bigger) is False


def test_the_control_the_amount_invariant_refuses_a_negative_amount() -> None:
    """Built by force, because the constructor refuses a negative amount at the
    door and there is no other way to hand the invariant the case it guards
    against. An invariant nobody has ever seen fail is not an invariant."""
    negative = Proposal(proposal_id="p-1", amount_paise=1)
    object.__setattr__(negative, "amount_paise", -1)
    invariant = _invariant("the amount never changes and never goes negative")
    assert invariant.holds(negative, negative) is False


def test_the_control_the_blocked_invariant_refuses_anything_leaving_blocked() -> None:
    invariant = _invariant("blocked is terminal and has no outgoing edge")
    assert invariant.holds(_observed(), _checked()) is True
    assert invariant.holds(_blocked(), _posted()) is False


def test_no_path_through_the_table_reaches_posting_without_being_checked() -> None:
    """Walked over the table rather than asserted about one run. Every reachable
    (state, have we been checked) pair is enumerated, and `posting` may not
    appear beside a False."""
    seen = {(INITIAL_STATE, False)}
    frontier = [(INITIAL_STATE, False)]
    while frontier:
        state, been_checked = frontier.pop()
        for rule in (r for r in RULES if state in r.frm):
            step = (rule.to, been_checked or rule.to is State.CHECKED)
            if step not in seen:
                seen.add(step)
                frontier.append(step)
    assert not [s for s, checked in seen if s is State.POSTING and not checked]


def test_the_control_the_posting_invariant_refuses_an_unchecked_claim() -> None:
    """THE CONTROL on the walk above. The walk proves the table has no such
    path; this proves the runtime guard would still catch one if a rule were
    added tomorrow."""
    invariant = _invariant("a write is never claimed by something unchecked")
    assert invariant.holds(_decided(), _posting()) is True
    assert invariant.holds(_observed(), _posting()) is False


def _invariant(name: str) -> Invariant:
    """The global invariant with this name, so the controls above name what they
    are testing instead of indexing into a tuple by position."""
    for invariant in GLOBAL_INVARIANTS:
        if invariant.name == name:
            return invariant
    raise AssertionError(f"no global invariant called {name!r}")


def test_a_broken_guarantee_raises_rather_than_returning_a_bad_proposal() -> None:
    """`demand_all_held` is the one place a failed post-condition or invariant
    becomes an exception. Nothing in normal operation reaches it, which is
    exactly why it is fired directly here - an untested raise is a raise that
    turns out to be a NameError the first time it matters."""
    with pytest.raises(BrokenGuarantee, match="the amount never changes"):
        demand_all_held(
            ["the amount never changes"],
            kind="invariant",
            proposal_id="p-1",
            event=Event.WRITE_CLAIMED,
        )


def test_the_control_demand_all_held_says_nothing_when_nothing_failed() -> None:
    """THE CONTROL. A version that raised on every call would pass the test
    above and stop the machine dead, so an empty list must be silent - which is
    also the path every one of the fifteen legal transitions takes."""
    demand_all_held([], kind="invariant", proposal_id="p-1", event=Event.ANSWERED)
    assert _posted().state is State.POSTED


# ---- the audit row -----------------------------------------------------------


def test_every_transition_writes_exactly_one_audit_row() -> None:
    before = _decided()
    after = apply(
        before, Event.WRITE_CLAIMED, at=_tick(4), actor=SYSTEM, operation_id="op-7"
    )
    assert len(after.audit) == len(before.audit) + 1


def test_an_audit_row_carries_the_event_the_time_both_states_and_the_actor() -> None:
    """The five the owner named. A row missing any of them cannot answer "what
    moved this, when, from where, and who" months later."""
    row = _posted().audit[-1]
    assert row.event is Event.WRITE_CONFIRMED
    assert row.at == _tick(5)
    assert row.before is State.POSTING
    assert row.after is State.POSTED
    assert row.actor is SYSTEM


def test_the_whole_walk_is_in_the_audit_in_the_order_it_happened() -> None:
    events = [row.event for row in _posted().audit]
    assert events == [
        Event.CONSERVATION_PASSED,
        Event.VALIDATION_PASSED,
        Event.CONFIDENCE_HIGH,
        Event.WRITE_CLAIMED,
        Event.WRITE_CONFIRMED,
    ]


def test_each_row_starts_where_the_previous_row_ended() -> None:
    rows = _posted().audit
    assert rows[0].before is INITIAL_STATE
    assert all(a.after is b.before for a, b in itertools.pairwise(rows))


def test_the_timestamp_on_the_row_is_the_one_the_caller_supplied() -> None:
    row = apply(
        _observed(), Event.CONSERVATION_PASSED, at=_tick(41), actor=SYSTEM
    ).audit[-1]
    assert row.at == _tick(41)


def test_a_naive_timestamp_is_refused_because_it_is_ambiguous() -> None:
    """A datetime with no zone is a number of hours nobody can place. Two rows
    written either side of a clock change would sort into the wrong order, and
    the order is the whole evidence."""
    naive = datetime.datetime(2026, 8, 12, 12, 0, 0)
    with pytest.raises(ValueError, match="time zone"):
        apply(_observed(), Event.CONSERVATION_PASSED, at=naive, actor=SYSTEM)


def test_a_timestamp_that_is_not_a_datetime_at_all_is_refused() -> None:
    with pytest.raises(TypeError):
        apply(
            _observed(),
            Event.CONSERVATION_PASSED,
            at="2026-08-12T12:00:00Z",  # type: ignore[arg-type]
            actor=SYSTEM,
        )


def test_the_module_never_reads_a_clock() -> None:
    """Read off the syntax tree, not off behaviour. A `datetime.now()` anywhere
    in this module makes two runs of the same walk incomparable, and a test that
    only compared one run would never notice."""
    repo = pathlib.Path(__file__).resolve().parent.parent
    source = (repo / "accountant" / "cage" / "state.py").read_text(encoding="utf-8")
    names = {
        node.attr
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Attribute)
    }
    assert not names & {"now", "utcnow", "today", "time", "monotonic"}


def test_the_same_walk_twice_produces_identical_audits() -> None:
    """Determinism, rule 11.2.10. With the clock injected there is nothing left
    that can vary, and a test that says so is what stops someone adding one."""
    assert _posted() == _posted()
    assert _posted().audit == _posted().audit


def test_an_audit_row_cannot_be_edited_after_it_is_written() -> None:
    with pytest.raises(AttributeError):
        _posted().audit[-1].after = State.BLOCKED  # type: ignore[misc]


# ---- money -------------------------------------------------------------------


def test_a_float_amount_is_refused_rather_than_coerced() -> None:
    """Money is integer paise everywhere in this system, `conservation.py`
    included. Accepting a float here would let 0.1 + 0.2 into a statutory
    record."""
    with pytest.raises(TypeError):
        Proposal(proposal_id="p-1", amount_paise=118_000.0)  # type: ignore[arg-type]


def test_a_bool_amount_is_refused_because_bool_is_an_int_in_python() -> None:
    """`isinstance(True, int)` is True and `True == 1`, so a flag passed where
    an amount belonged would otherwise become a proposal for one paisa."""
    with pytest.raises(TypeError):
        Proposal(proposal_id="p-1", amount_paise=True)  # type: ignore[arg-type]


def test_an_unread_amount_is_refused_and_never_read_as_zero() -> None:
    """`None` is "nobody read it". A zero-paise proposal is a real thing to
    refuse later; a proposal on nothing at all is not a proposal."""
    with pytest.raises(TypeError):
        Proposal(proposal_id="p-1", amount_paise=None)  # type: ignore[arg-type]


def test_a_negative_amount_is_refused_at_the_door() -> None:
    with pytest.raises(ValueError, match="negative"):
        Proposal(proposal_id="p-1", amount_paise=-1)


def test_a_zero_amount_is_allowed_here_and_refused_further_down() -> None:
    """Deliberately not refused. A bill read as zero paise is a real thing that
    has to reach a refusal a person can read, and crashing the state machine on
    it turns a plain sentence into a stack trace."""
    assert Proposal(proposal_id="p-1", amount_paise=0).state is State.OBSERVED


def test_a_proposal_with_no_id_is_refused() -> None:
    """Every audit row is keyed by it. A blank id makes two proposals'
    histories one history."""
    with pytest.raises(ValueError, match="id"):
        Proposal(proposal_id="   ", amount_paise=1)


# ---- REVIEW NOTES ------------------------------------------------------------
#
# Read back adversarially, as somebody who did not write it.
#
# 1. FIXED - the rejection matrix could have passed against a machine that
#    refused every transition. `test_the_control_every_pair_the_table_declares_
#    actually_goes_through` fires all fifteen declared edges and requires each
#    to move, which is the control the matrix needed.
#
# 2. FIXED - `test_blocked_is_terminal...` and `test_posted_is_terminal...` both
#    depend on `_blocked()` and `_posted()` genuinely being in those states. If
#    a builder quietly returned an `observed` proposal the tests would still
#    pass on most events. `test_no_rule_in_the_table_leaves_a_terminal_state`
#    reads the same claim off the table with no builder involved.
#
# 3. FIXED - the three invariants that can never fire through `apply` (id,
#    amount, blocked-terminal, unchecked-claim) were originally asserted only by
#    walking a happy path, which proves nothing about the predicate. Each now
#    has a control that hands the predicate the pair it exists to refuse.
#
# 4. NOT DONE - `test_at_most_one_rule_ever_matches_a_state_and_an_event` checks
#    that rules sharing a (state, event) have distinct destinations. It does NOT
#    prove their preconditions are mutually exclusive; two rules with the same
#    precondition and different destinations would still resolve by table order.
#    Proving exclusivity needs the precondition predicates enumerated over every
#    reachable history, which is a bigger fixture than this file should carry.
#    The four gate rules are the only ones that share a pair today.
#
# 5. NOT DONE - nothing here tests a walk longer than five transitions, and
#    `_replay` is O(n^2) in the number of rows. At the lengths this machine
#    produces (at most eight) that is not worth measuring, but if a proposal
#    ever accumulated hundreds of rows the constructor would be the cost.
